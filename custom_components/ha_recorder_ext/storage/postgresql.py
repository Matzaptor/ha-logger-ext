from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

import asyncpg

from .base import ObservationRecord, StorageBackend

_LOGGER = logging.getLogger(__name__)

_SCHEMA_VERSION = 1

# Neither connecting nor querying PostgreSQL times out by default in asyncpg,
# so a dead connection or network blip would otherwise hang every read/write
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
    id          BYTEA        NOT NULL PRIMARY KEY,
    entity_id   TEXT         NOT NULL UNIQUE,
    domain      TEXT         NOT NULL,
    first_seen  TEXT         NOT NULL,
    last_seen   TEXT         NOT NULL
)
"""

_SQL_CREATE_OBSERVATIONS = """
CREATE TABLE IF NOT EXISTS observations (
    id             BYTEA       NOT NULL PRIMARY KEY,
    entity_pk      BYTEA       NOT NULL
                               REFERENCES entities(id)
                               ON DELETE CASCADE ON UPDATE CASCADE,
    field          TEXT        NOT NULL,
    value_type     TEXT        NOT NULL,
    value_str      TEXT,
    value_int      BIGINT,
    value_float    DOUBLE PRECISION,
    value_bool     SMALLINT    CHECK (value_bool IN (0, 1) OR value_bool IS NULL),
    value_datetime TEXT,
    value_date     TEXT,
    value_time     TEXT,
    value_json     TEXT,
    first_seen     TEXT        NOT NULL,
    last_seen      TEXT        NOT NULL
)
"""

_SQL_CREATE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_obs_entity_field ON observations(entity_pk, field)",
    "CREATE INDEX IF NOT EXISTS idx_obs_entity_field_last ON observations(entity_pk, field, last_seen DESC)",
    "CREATE INDEX IF NOT EXISTS idx_obs_first_seen ON observations(first_seen)",
)

_SQL_SELECT_SCHEMA_VERSION = "SELECT version FROM schema_version LIMIT 1"
_SQL_INSERT_SCHEMA_VERSION = "INSERT INTO schema_version (version) VALUES ($1)"
_SQL_UPDATE_SCHEMA_VERSION = "UPDATE schema_version SET version = $1"

_SQL_SELECT_ENTITY = "SELECT id FROM entities WHERE entity_id = $1"
_SQL_UPDATE_ENTITY_LAST_SEEN = "UPDATE entities SET last_seen = $1 WHERE id = $2"
_SQL_INSERT_ENTITY = (
    "INSERT INTO entities (id, entity_id, domain, first_seen, last_seen) "
    "VALUES ($1, $2, $3, $4, $5) ON CONFLICT (entity_id) DO NOTHING"
)

_SQL_SELECT_LATEST_OBS = """
SELECT id, entity_pk, field, value_type,
       value_str, value_int, value_float, value_bool,
       value_datetime, value_date, value_time, value_json,
       first_seen, last_seen
FROM observations
WHERE entity_pk = $1 AND field = $2
ORDER BY last_seen DESC
LIMIT 1
"""

_SQL_INSERT_OBS = """
INSERT INTO observations (
    id, entity_pk, field, value_type,
    value_str, value_int, value_float, value_bool,
    value_datetime, value_date, value_time, value_json,
    first_seen, last_seen
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
"""

_SQL_UPDATE_OBS_LAST_SEEN = "UPDATE observations SET last_seen = $1 WHERE id = $2"

_SQL_HAS_OBS_IN_RANGE = """
SELECT 1 FROM observations
WHERE entity_pk = $1 AND field = $2
  AND first_seen <= $3
  AND last_seen  >= $4
LIMIT 1
"""

_MigrationFn = Callable[[asyncpg.Connection], Awaitable[None]]
_MIGRATIONS: dict[int, _MigrationFn] = {}


def _to_blob(u: uuid.UUID) -> bytes:
    return u.bytes


def _from_blob(b: bytes) -> uuid.UUID:
    return uuid.UUID(bytes=b)


def _fmt(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


class PostgreSQLBackend(StorageBackend):
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
        self._pool: asyncpg.Pool | None = None
        self._conn: asyncpg.Connection | None = None
        self._tx: asyncpg.transaction.Transaction | None = None
        self._in_transaction = False

    async def initialize(self) -> None:
        try:
            self._pool = await asyncpg.create_pool(
                host=self._host,
                port=self._port,
                database=self._database,
                user=self._username,
                password=self._password,
                timeout=_CONNECT_TIMEOUT_SECONDS,
                command_timeout=_QUERY_TIMEOUT_SECONDS,
            )
        except TimeoutError as err:
            raise TimeoutError(
                f"Timed out connecting to PostgreSQL storage backend at "
                f"{self._host}:{self._port} after {_CONNECT_TIMEOUT_SECONDS}s"
            ) from err
        await self._apply_migrations()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            self._tx = None
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _apply_migrations(self) -> None:
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(_SQL_CREATE_SCHEMA_VERSION)
                row = await conn.fetchrow(_SQL_SELECT_SCHEMA_VERSION)
                current: int = row["version"] if row else 0

                if current == 0:
                    _LOGGER.debug("Initializing fresh PostgreSQL schema (version %d)", _SCHEMA_VERSION)
                    await conn.execute(_SQL_CREATE_ENTITIES)
                    await conn.execute(_SQL_CREATE_OBSERVATIONS)
                    for sql in _SQL_CREATE_INDEXES:
                        await conn.execute(sql)
                    await conn.execute(_SQL_INSERT_SCHEMA_VERSION, _SCHEMA_VERSION)
                    return

                if current == _SCHEMA_VERSION:
                    return

                if current > _SCHEMA_VERSION:
                    raise RuntimeError(
                        f"Database schema version {current} is newer than integration "
                        f"schema version {_SCHEMA_VERSION}. Please update the integration."
                    )

                for target in range(current + 1, _SCHEMA_VERSION + 1):
                    _LOGGER.info("Migrating PostgreSQL schema to version %d", target)
                    migrate = _MIGRATIONS.get(target)
                    if migrate is None:
                        raise RuntimeError(f"No migration defined for schema version {target}.")
                    await migrate(conn)
                await conn.execute(_SQL_UPDATE_SCHEMA_VERSION, _SCHEMA_VERSION)

    async def begin(self) -> None:
        assert self._pool is not None
        self._conn = await self._pool.acquire()
        self._tx = self._conn.transaction()
        await self._tx.start()
        self._in_transaction = True

    async def commit(self) -> None:
        assert self._tx is not None and self._conn is not None
        await self._tx.commit()
        await self._pool.release(self._conn)  # type: ignore[union-attr]
        self._conn = None
        self._tx = None
        self._in_transaction = False

    async def rollback(self) -> None:
        assert self._tx is not None and self._conn is not None
        await self._tx.rollback()
        await self._pool.release(self._conn)  # type: ignore[union-attr]
        self._conn = None
        self._tx = None
        self._in_transaction = False

    async def _execute(self, sql: str, *args: Any) -> None:
        if self._in_transaction:
            assert self._conn is not None
            await self._conn.execute(sql, *args)
            return
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            await conn.execute(sql, *args)

    async def _fetchrow(self, sql: str, *args: Any) -> asyncpg.Record | None:
        if self._in_transaction:
            assert self._conn is not None
            return await self._conn.fetchrow(sql, *args)
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(sql, *args)

    async def get_or_create_entity(self, entity_id: str, domain: str, ts: datetime) -> uuid.UUID:
        ts_str = _fmt(ts)
        row = await self._fetchrow(_SQL_SELECT_ENTITY, entity_id)
        if row is not None:
            pk = _from_blob(bytes(row["id"]))
            await self._execute(_SQL_UPDATE_ENTITY_LAST_SEEN, ts_str, _to_blob(pk))
            return pk

        from .uuid7 import uuid7
        pk = uuid7()
        await self._execute(_SQL_INSERT_ENTITY, _to_blob(pk), entity_id, domain, ts_str, ts_str)
        row = await self._fetchrow(_SQL_SELECT_ENTITY, entity_id)
        assert row is not None
        return _from_blob(bytes(row["id"]))

    async def get_latest_observation(self, entity_pk: uuid.UUID, field_name: str) -> ObservationRecord | None:
        row = await self._fetchrow(_SQL_SELECT_LATEST_OBS, _to_blob(entity_pk), field_name)
        return _row_to_record(row) if row is not None else None

    async def insert_observation(self, obs: ObservationRecord) -> None:
        await self._execute(
            _SQL_INSERT_OBS,
            _to_blob(obs.id), _to_blob(obs.entity_pk), obs.field_name, obs.value_type,
            obs.value_str, obs.value_int, obs.value_float, obs.value_bool,
            obs.value_datetime, obs.value_date, obs.value_time, obs.value_json,
            _fmt(obs.first_seen), _fmt(obs.last_seen),
        )

    async def update_last_seen(self, obs_id: uuid.UUID, ts: datetime) -> None:
        await self._execute(_SQL_UPDATE_OBS_LAST_SEEN, _fmt(ts), _to_blob(obs_id))

    async def has_observations_in_range(
        self,
        entity_pk: uuid.UUID,
        field_name: str,
        start: datetime,
        end: datetime,
    ) -> bool:
        row = await self._fetchrow(
            _SQL_HAS_OBS_IN_RANGE, _to_blob(entity_pk), field_name, _fmt(end), _fmt(start)
        )
        return row is not None


def _row_to_record(row: asyncpg.Record) -> ObservationRecord:
    return ObservationRecord(
        id=_from_blob(bytes(row["id"])),
        entity_pk=_from_blob(bytes(row["entity_pk"])),
        field_name=row["field"],
        value_type=row["value_type"],
        value_str=row["value_str"],
        value_int=row["value_int"],
        value_float=row["value_float"],
        value_bool=row["value_bool"],
        value_datetime=row["value_datetime"],
        value_date=row["value_date"],
        value_time=row["value_time"],
        value_json=row["value_json"],
        first_seen=datetime.fromisoformat(row["first_seen"]),
        last_seen=datetime.fromisoformat(row["last_seen"]),
    )
