from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import aiomysql

from .base import ObservationRecord, StorageBackend

_LOGGER = logging.getLogger(__name__)

_SCHEMA_VERSION = 1

# Neither connecting nor querying MySQL times out by default in aiomysql, so
# a dead connection or network blip would otherwise hang every read/write
# this backend does forever, with no error and no visible query anywhere
# (same failure mode as the external recorder reader — see
# external_recorder_reader.py for the read-side fix this mirrors).
_CONNECT_TIMEOUT_SECONDS = 10
_QUERY_TIMEOUT_SECONDS = 300

_SQL_CREATE_SCHEMA_VERSION = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
)
"""

_SQL_CREATE_ENTITIES = """
CREATE TABLE IF NOT EXISTS entities (
    id          BINARY(16)   NOT NULL PRIMARY KEY,
    entity_id   VARCHAR(255) NOT NULL UNIQUE,
    domain      VARCHAR(128) NOT NULL,
    first_seen  VARCHAR(32)  NOT NULL,
    last_seen   VARCHAR(32)  NOT NULL
)
"""

_SQL_CREATE_OBSERVATIONS = """
CREATE TABLE IF NOT EXISTS observations (
    id             BINARY(16)   NOT NULL PRIMARY KEY,
    entity_pk      BINARY(16)   NOT NULL,
    field          VARCHAR(255) NOT NULL,
    value_type     VARCHAR(16)  NOT NULL,
    value_str      TEXT,
    value_int      BIGINT,
    value_float    DOUBLE,
    value_bool     TINYINT(1)   CHECK (value_bool IN (0, 1) OR value_bool IS NULL),
    value_datetime VARCHAR(64),
    value_date     VARCHAR(16),
    value_time     VARCHAR(20),
    value_json     TEXT,
    first_seen     VARCHAR(32)  NOT NULL,
    last_seen      VARCHAR(32)  NOT NULL,
    CONSTRAINT fk_obs_entity FOREIGN KEY (entity_pk)
        REFERENCES entities(id)
        ON DELETE CASCADE
        ON UPDATE CASCADE
)
"""

# (index name, creation SQL) pairs. No "IF NOT EXISTS": MariaDB accepts that
# syntax on CREATE INDEX, but MySQL does not (parse error) — existence is
# checked explicitly against information_schema.STATISTICS instead, see
# _apply_migrations().
_INDEX_DEFINITIONS = (
    (
        "idx_obs_entity_field",
        "CREATE INDEX idx_obs_entity_field ON observations(entity_pk, field(64))",
    ),
    (
        "idx_obs_entity_field_last",
        "CREATE INDEX idx_obs_entity_field_last "
        "ON observations(entity_pk, field(64), last_seen DESC)",
    ),
    (
        "idx_obs_first_seen",
        "CREATE INDEX idx_obs_first_seen ON observations(first_seen)",
    ),
)

_SQL_CHECK_INDEX_EXISTS = (
    "SELECT 1 FROM information_schema.STATISTICS "
    "WHERE table_schema = DATABASE() AND table_name = 'observations' "
    "AND index_name = %s LIMIT 1"
)

_SQL_SELECT_SCHEMA_VERSION = "SELECT version FROM schema_version LIMIT 1"
_SQL_INSERT_SCHEMA_VERSION = "INSERT INTO schema_version (version) VALUES (%s)"
_SQL_UPDATE_SCHEMA_VERSION = "UPDATE schema_version SET version = %s"

_SQL_SELECT_ENTITY = "SELECT id FROM entities WHERE entity_id = %s"
_SQL_UPDATE_ENTITY_LAST_SEEN = "UPDATE entities SET last_seen = %s WHERE id = %s"
_SQL_INSERT_ENTITY = (
    "INSERT IGNORE INTO entities (id, entity_id, domain, first_seen, last_seen) "
    "VALUES (%s, %s, %s, %s, %s)"
)

_SQL_SELECT_LATEST_OBS = """
SELECT id, entity_pk, field, value_type,
       value_str, value_int, value_float, value_bool,
       value_datetime, value_date, value_time, value_json,
       first_seen, last_seen
FROM observations
WHERE entity_pk = %s AND field = %s
ORDER BY last_seen DESC
LIMIT 1
"""

_SQL_INSERT_OBS = """
INSERT INTO observations (
    id, entity_pk, field, value_type,
    value_str, value_int, value_float, value_bool,
    value_datetime, value_date, value_time, value_json,
    first_seen, last_seen
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

_SQL_UPDATE_OBS_LAST_SEEN = "UPDATE observations SET last_seen = %s WHERE id = %s"

_SQL_HAS_OBS_IN_RANGE = """
SELECT 1 FROM observations
WHERE entity_pk = %s AND field = %s
  AND first_seen <= %s
  AND last_seen  >= %s
LIMIT 1
"""

# Maps target schema version → async migration coroutine.
# Each function receives an open aiomysql.Connection and must not commit.
_MigrationFn = Callable[[aiomysql.Connection], Awaitable[None]]
_MIGRATIONS: dict[int, _MigrationFn] = {}


def _to_blob(u: uuid.UUID) -> bytes:
    return u.bytes


def _from_blob(b: bytes) -> uuid.UUID:
    return uuid.UUID(bytes=b)


def _fmt(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


class MySQLBackend(StorageBackend):
    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        username: str,
        password: str,
    ) -> None:
        self._host = host
        self._port = port
        self._database = database
        self._username = username
        self._password = password
        self._pool: aiomysql.Pool | None = None
        # Active connection held during an explicit transaction.
        self._conn: aiomysql.Connection | None = None
        self._in_transaction = False
        # The live-recording flush loop and the import service both hold a
        # reference to this same backend instance and can run concurrently.
        # self._conn/self._in_transaction are shared instance state: without
        # this lock, an import call could observe a transaction opened by the
        # flush loop and reuse its connection concurrently — aiomysql is not
        # safe for concurrent use of one connection (two coroutines reading
        # the socket at once corrupts the wire protocol for both).
        self._lock = asyncio.Lock()
        # The task that currently owns the open transaction (if any). Only
        # that task's own calls may bypass the lock while a transaction is
        # open — a *different* task must never skip it just because
        # self._in_transaction happens to be True, or it would reuse another
        # task's transaction connection concurrently, which is the exact bug
        # this locking exists to prevent.
        self._transaction_owner: asyncio.Task[Any] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        try:
            self._pool = await aiomysql.create_pool(
                host=self._host,
                port=self._port,
                db=self._database,
                user=self._username,
                password=self._password,
                autocommit=False,
                charset="utf8mb4",
                connect_timeout=_CONNECT_TIMEOUT_SECONDS,
            )
        except TimeoutError as err:
            raise TimeoutError(
                f"Timed out connecting to MySQL storage backend at "
                f"{self._host}:{self._port} after {_CONNECT_TIMEOUT_SECONDS}s"
            ) from err
        await self._apply_migrations()

    async def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        if self._pool is not None:
            self._pool.close()
            try:
                await asyncio.wait_for(
                    self._pool.wait_closed(), timeout=_CONNECT_TIMEOUT_SECONDS
                )
            except TimeoutError:
                # Don't let a stuck close() hang shutdown/cleanup or mask
                # whatever error the caller may already be handling — the
                # pool object is discarded either way.
                _LOGGER.warning(
                    "Timed out closing MySQL storage backend pool at %s:%s after %ds",
                    self._host,
                    self._port,
                    _CONNECT_TIMEOUT_SECONDS,
                )
            self._pool = None

    # ------------------------------------------------------------------
    # Schema migrations
    # ------------------------------------------------------------------

    async def _apply_migrations(self) -> None:
        assert self._pool is not None

        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(_SQL_CREATE_SCHEMA_VERSION)
                await cur.execute(_SQL_SELECT_SCHEMA_VERSION)
                row = await cur.fetchone()
                current: int = row[0] if row else 0

                if current == 0:
                    _LOGGER.debug(
                        "Initializing fresh MySQL schema (version %d)", _SCHEMA_VERSION
                    )
                    await cur.execute(_SQL_CREATE_ENTITIES)
                    await cur.execute(_SQL_CREATE_OBSERVATIONS)
                    for index_name, sql in _INDEX_DEFINITIONS:
                        await cur.execute(_SQL_CHECK_INDEX_EXISTS, (index_name,))
                        if await cur.fetchone():
                            _LOGGER.debug(
                                "Skipping index creation (already exists): %s", index_name
                            )
                            continue
                        try:
                            await cur.execute(sql)
                        except Exception as err:
                            _LOGGER.warning(
                                "Index creation failed and was skipped — queries "
                                "may be slower than expected until this is fixed "
                                "(%s): %s",
                                err,
                                sql,
                            )
                    await cur.execute(_SQL_INSERT_SCHEMA_VERSION, (_SCHEMA_VERSION,))
                    await conn.commit()
                    return

                if current == _SCHEMA_VERSION:
                    return

                if current > _SCHEMA_VERSION:
                    raise RuntimeError(
                        f"Database schema version {current} is newer than integration "
                        f"schema version {_SCHEMA_VERSION}. Please update the integration."
                    )

                try:
                    for target in range(current + 1, _SCHEMA_VERSION + 1):
                        _LOGGER.info(
                            "Migrating MySQL schema to version %d", target
                        )
                        migrate = _MIGRATIONS.get(target)
                        if migrate is None:
                            raise NotImplementedError(
                                f"No migration defined for schema version {target}."
                            )
                        await migrate(conn)
                    await cur.execute(_SQL_UPDATE_SCHEMA_VERSION, (_SCHEMA_VERSION,))
                    await conn.commit()
                except Exception:
                    await conn.rollback()
                    raise

    # ------------------------------------------------------------------
    # Transaction control
    # ------------------------------------------------------------------

    async def begin(self) -> None:
        # Held until commit()/rollback() releases it — see _serialize().
        await self._lock.acquire()
        try:
            self._conn = await self._acquire()
            await self._with_timeout(self._conn.begin())
            self._in_transaction = True
            self._transaction_owner = asyncio.current_task()
        except Exception:
            self._lock.release()
            raise

    async def commit(self) -> None:
        assert self._conn is not None
        try:
            await self._with_timeout(self._conn.commit())
        finally:
            self._release_active_conn()
            self._transaction_owner = None
            self._lock.release()

    async def rollback(self) -> None:
        assert self._conn is not None
        try:
            await self._with_timeout(self._conn.rollback())
        finally:
            self._release_active_conn()
            self._transaction_owner = None
            self._lock.release()

    # ------------------------------------------------------------------
    # Internal connection helper
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def _serialize(self) -> AsyncIterator[None]:
        """Hold the backend-wide lock unless the *current task* owns the open transaction.

        The live-recording flush loop and the import service both hold a
        reference to this same backend instance and can run concurrently.
        Only the task that itself called begin() may bypass the lock while a
        transaction is open (its own follow-up calls would otherwise
        deadlock against the lock it's already holding) — any *other* task
        must wait, or it would reuse that transaction's connection
        concurrently, which is the exact bug this locking exists to prevent.
        """
        if self._transaction_owner is asyncio.current_task():
            yield
        else:
            async with self._lock:
                yield

    def _active_conn(self) -> aiomysql.Connection:
        """Return the transaction connection or raise if not in a transaction."""
        assert self._conn is not None, "No active transaction connection."
        return self._conn

    def _release_active_conn(self) -> None:
        assert self._pool is not None and self._conn is not None
        self._pool.release(self._conn)
        self._conn = None
        self._in_transaction = False

    async def _acquire(self) -> aiomysql.Connection:
        """Acquire a pool connection with a timeout.

        Using the pool's `async with` form (or a bare `await pool.acquire()`
        followed by `conn.close()`) leaves no way to bound how long we wait,
        and closing a pool-acquired connection directly — instead of
        releasing it back via `pool.release()` — permanently leaks it from
        the pool's accounting. Every non-transactional write used to do
        exactly that, silently shrinking the pool by one connection each
        time until it was fully exhausted and every future acquire hung
        forever with no timeout and no query ever reaching the server to
        explain why.
        """
        assert self._pool is not None
        try:
            return await asyncio.wait_for(
                self._pool.acquire(), timeout=_CONNECT_TIMEOUT_SECONDS
            )
        except TimeoutError as err:
            raise TimeoutError(
                f"Timed out acquiring a MySQL storage backend connection from "
                f"the pool at {self._host}:{self._port} after "
                f"{_CONNECT_TIMEOUT_SECONDS}s (the pool may be exhausted)"
            ) from err

    async def _with_timeout(self, coro: Awaitable[Any]) -> None:
        try:
            await asyncio.wait_for(coro, timeout=_QUERY_TIMEOUT_SECONDS)
        except TimeoutError as err:
            raise TimeoutError(
                f"MySQL storage backend query at {self._host}:{self._port} "
                f"did not complete within {_QUERY_TIMEOUT_SECONDS}s"
            ) from err

    async def _execute(self, sql: str, params: tuple | None = None) -> None:
        """Execute a statement on the active connection (transaction mode) or acquire a temporary one.

        No caller uses the cursor afterwards, so unlike a select this doesn't
        need to return it — closing it here (via `async with`, matching
        _fetchone) instead of leaving it open is what actually matters: an
        unclosed cursor per call was accumulating against the connection for
        the life of the process on the busiest code path this backend has.
        """
        async with self._serialize():
            if self._in_transaction:
                conn = self._active_conn()
                async with conn.cursor() as cur:
                    await self._with_timeout(cur.execute(sql, params or ()))
                return

            assert self._pool is not None
            conn = await self._acquire()
            try:
                async with conn.cursor() as cur:
                    await self._with_timeout(cur.execute(sql, params or ()))
                    await conn.commit()
            finally:
                self._pool.release(conn)

    async def _fetchone(
        self, sql: str, params: tuple | None = None
    ) -> tuple | None:
        async with self._serialize():
            if self._in_transaction:
                conn = self._active_conn()
                async with conn.cursor() as cur:
                    await self._with_timeout(cur.execute(sql, params or ()))
                    return await cur.fetchone()

            assert self._pool is not None
            conn = await self._acquire()
            try:
                async with conn.cursor() as cur:
                    await self._with_timeout(cur.execute(sql, params or ()))
                    return await cur.fetchone()
            finally:
                self._pool.release(conn)

    # ------------------------------------------------------------------
    # Entity management
    # ------------------------------------------------------------------

    async def get_or_create_entity(
        self, entity_id: str, domain: str, ts: datetime
    ) -> uuid.UUID:
        ts_str = _fmt(ts)
        row = await self._fetchone(_SQL_SELECT_ENTITY, (entity_id,))

        if row is not None:
            pk = _from_blob(bytes(row[0]))
            await self._execute(_SQL_UPDATE_ENTITY_LAST_SEEN, (ts_str, _to_blob(pk)))
            return pk

        from .uuid7 import uuid7
        pk = uuid7()
        await self._execute(
            _SQL_INSERT_ENTITY,
            (_to_blob(pk), entity_id, domain, ts_str, ts_str),
        )
        # A race on INSERT IGNORE means another worker inserted first; fetch the winner.
        row = await self._fetchone(_SQL_SELECT_ENTITY, (entity_id,))
        assert row is not None
        return _from_blob(bytes(row[0]))

    # ------------------------------------------------------------------
    # Observations
    # ------------------------------------------------------------------

    async def get_latest_observation(
        self, entity_pk: uuid.UUID, field_name: str
    ) -> ObservationRecord | None:
        row = await self._fetchone(
            _SQL_SELECT_LATEST_OBS, (_to_blob(entity_pk), field_name)
        )
        return _row_to_record(row) if row is not None else None

    async def insert_observation(self, obs: ObservationRecord) -> None:
        await self._execute(
            _SQL_INSERT_OBS,
            (
                _to_blob(obs.id),
                _to_blob(obs.entity_pk),
                obs.field_name,
                obs.value_type,
                obs.value_str,
                obs.value_int,
                obs.value_float,
                obs.value_bool,
                obs.value_datetime,
                obs.value_date,
                obs.value_time,
                obs.value_json,
                _fmt(obs.first_seen),
                _fmt(obs.last_seen),
            ),
        )

    async def update_last_seen(self, obs_id: uuid.UUID, ts: datetime) -> None:
        await self._execute(
            _SQL_UPDATE_OBS_LAST_SEEN, (_fmt(ts), _to_blob(obs_id))
        )

    async def has_observations_in_range(
        self,
        entity_pk: uuid.UUID,
        field_name: str,
        start: datetime,
        end: datetime,
    ) -> bool:
        row = await self._fetchone(
            _SQL_HAS_OBS_IN_RANGE,
            (_to_blob(entity_pk), field_name, _fmt(end), _fmt(start)),
        )
        return row is not None


def _row_to_record(row: tuple) -> ObservationRecord:
    return ObservationRecord(
        id=_from_blob(bytes(row[0])),
        entity_pk=_from_blob(bytes(row[1])),
        field_name=row[2],
        value_type=row[3],
        value_str=row[4],
        value_int=row[5],
        value_float=row[6],
        value_bool=row[7],
        value_datetime=row[8],
        value_date=row[9],
        value_time=row[10],
        value_json=row[11],
        first_seen=datetime.fromisoformat(row[12]),
        last_seen=datetime.fromisoformat(row[13]),
    )
