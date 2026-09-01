from __future__ import annotations

import asyncio
import functools
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from .base import ObservationRecord, StorageBackend

_LOGGER = logging.getLogger(__name__)

_SCHEMA_VERSION = 2

_PRAGMA_FK = "PRAGMA foreign_keys = ON"
_PRAGMA_WAL = "PRAGMA journal_mode = WAL"

_SQL_CREATE_SCHEMA_VERSION = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
)
"""

_SQL_CREATE_ENTITIES = """
CREATE TABLE IF NOT EXISTS entities (
    id          BLOB NOT NULL PRIMARY KEY,
    entity_id   TEXT NOT NULL UNIQUE,
    domain      TEXT NOT NULL,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
)
"""

_SQL_CREATE_OBSERVATIONS = """
CREATE TABLE IF NOT EXISTS observations (
    id             BLOB NOT NULL PRIMARY KEY,
    entity_pk      BLOB NOT NULL
                        REFERENCES entities(id)
                        ON DELETE CASCADE ON UPDATE CASCADE,
    field          TEXT NOT NULL,
    value_type     TEXT NOT NULL CHECK (value_type IN (
                       'str', 'int', 'float', 'bool', 'null',
                       'datetime', 'date', 'time', 'timedelta', 'json'
                   )),
    value_str      TEXT,
    value_int      INTEGER,
    value_float    REAL,
    value_bool     INTEGER CHECK (value_bool IN (0, 1) OR value_bool IS NULL),
    value_datetime TEXT,
    value_date     TEXT,
    value_time     TEXT,
    value_json     TEXT,
    first_seen     TEXT NOT NULL,
    last_seen      TEXT NOT NULL
)
"""

_SQL_CREATE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_obs_entity_field "
    "ON observations(entity_pk, field)",

    "CREATE INDEX IF NOT EXISTS idx_obs_entity_field_last "
    "ON observations(entity_pk, field, last_seen DESC)",

    "CREATE INDEX IF NOT EXISTS idx_obs_first_seen "
    "ON observations(first_seen)",
)

_SQL_SELECT_SCHEMA_VERSION = "SELECT version FROM schema_version LIMIT 1"
_SQL_INSERT_SCHEMA_VERSION = "INSERT INTO schema_version (version) VALUES (?)"
_SQL_UPDATE_SCHEMA_VERSION = "UPDATE schema_version SET version = ?"

_SQL_SELECT_ENTITY = "SELECT id FROM entities WHERE entity_id = ?"
_SQL_UPDATE_ENTITY_LAST_SEEN = "UPDATE entities SET last_seen = ? WHERE id = ?"
_SQL_INSERT_ENTITY = (
    "INSERT INTO entities (id, entity_id, domain, first_seen, last_seen) "
    "VALUES (?, ?, ?, ?, ?)"
)

_SQL_SELECT_LATEST_OBS = """
SELECT id, entity_pk, field, value_type,
       value_str, value_int, value_float, value_bool,
       value_datetime, value_date, value_time, value_json,
       first_seen, last_seen
FROM observations
WHERE entity_pk = ? AND field = ?
ORDER BY last_seen DESC
LIMIT 1
"""

_SQL_INSERT_OBS = """
INSERT INTO observations (
    id, entity_pk, field, value_type,
    value_str, value_int, value_float, value_bool,
    value_datetime, value_date, value_time, value_json,
    first_seen, last_seen
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SQL_UPDATE_OBS_LAST_SEEN = "UPDATE observations SET last_seen = ? WHERE id = ?"

# Two intervals [A,B] and [C,D] overlap when A <= D AND B >= C.
_SQL_HAS_OBS_IN_RANGE = """
SELECT 1 FROM observations
WHERE entity_pk = ? AND field = ?
  AND first_seen <= ?
  AND last_seen  >= ?
LIMIT 1
"""


async def _migrate_to_v2(conn: aiosqlite.Connection) -> None:
    """Ensure observation indexes exist. Re-running CREATE INDEX IF NOT
    EXISTS is a no-op on a database that already has them; it's a retrofit
    safety net for a database whose fresh-init index creation was somehow
    skipped or interrupted (see the MySQL backend for a real-world case of
    this happening on a different engine)."""
    for sql in _SQL_CREATE_INDEXES:
        await conn.execute(sql)


# Maps target schema version → async migration coroutine.
# Each function receives an open aiosqlite.Connection and must not commit.
# To add a migration: define an async function and register it here.
# Example:
#   async def _migrate_to_v3(conn: aiosqlite.Connection) -> None:
#       await conn.execute("ALTER TABLE observations ADD COLUMN ...")
#   _MIGRATIONS[3] = _migrate_to_v3
_MigrationFn = Callable[[aiosqlite.Connection], Awaitable[None]]
_MIGRATIONS: dict[int, _MigrationFn] = {2: _migrate_to_v2}


def _to_blob(u: uuid.UUID) -> bytes:
    return u.bytes


def _from_blob(b: bytes) -> uuid.UUID:
    return uuid.UUID(bytes=b)


def _fmt(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


class SQLiteBackend(StorageBackend):
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._in_transaction = False
        # The live-recording flush loop and the import service both hold a
        # reference to this same backend instance and can run concurrently.
        # aiosqlite serializes actual I/O on its single connection, but
        # without this lock a flush transaction (begin()...commit()) could
        # still overlap with an import call's own write in a way that makes
        # _maybe_commit() skip committing the import's write (because
        # self._in_transaction was set True by the flush, not by the import
        # call itself) — the import's row would then only be persisted (or
        # rolled back) as a side effect of whatever the flush does with its
        # own transaction, which is not correct for either caller.
        self._lock = asyncio.Lock()
        # The task that currently owns the open transaction (if any). Only
        # that task's own calls may bypass the lock while a transaction is
        # open — a *different* task must never skip it just because
        # self._in_transaction happens to be True, or it would incorrectly
        # piggyback its own write onto that task's transaction.
        self._transaction_owner: asyncio.Task[Any] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        # The parent directory may not exist yet (factory.py no longer creates
        # it synchronously). mkdir is cheap in the common case but can stall
        # on a network-mounted config directory or a slow disk, so it's kept
        # off the event loop the same way any other blocking I/O here would be.
        await asyncio.get_running_loop().run_in_executor(
            None,
            functools.partial(self._db_path.parent.mkdir, parents=True, exist_ok=True),
        )
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute(_PRAGMA_FK)
        await self._conn.execute(_PRAGMA_WAL)
        await self._apply_migrations()
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Schema migrations
    # ------------------------------------------------------------------

    async def _apply_migrations(self) -> None:
        await self._conn.execute(_SQL_CREATE_SCHEMA_VERSION)

        async with self._conn.execute(_SQL_SELECT_SCHEMA_VERSION) as cur:
            row = await cur.fetchone()
        current = row["version"] if row else 0

        if current == 0:
            _LOGGER.debug("Initializing fresh database schema (version %d)", _SCHEMA_VERSION)
            await self._conn.execute(_SQL_CREATE_ENTITIES)
            await self._conn.execute(_SQL_CREATE_OBSERVATIONS)
            for sql in _SQL_CREATE_INDEXES:
                await self._conn.execute(sql)
            await self._conn.execute(_SQL_INSERT_SCHEMA_VERSION, (_SCHEMA_VERSION,))
            return

        if current == _SCHEMA_VERSION:
            return

        if current > _SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema version {current} is newer than integration "
                f"schema version {_SCHEMA_VERSION}. Please update the integration."
            )

        for target in range(current + 1, _SCHEMA_VERSION + 1):
            _LOGGER.info("Migrating database schema to version %d", target)
            await self._run_migration(target)
        await self._conn.execute(_SQL_UPDATE_SCHEMA_VERSION, (_SCHEMA_VERSION,))

    async def _run_migration(self, to_version: int) -> None:
        migrate = _MIGRATIONS.get(to_version)
        if migrate is None:
            raise NotImplementedError(
                f"No migration defined for schema version {to_version}."
            )
        await migrate(self._conn)

    # ------------------------------------------------------------------
    # Transaction control
    # ------------------------------------------------------------------

    async def begin(self) -> None:
        # Held until commit()/rollback() releases it — see _serialize().
        await self._lock.acquire()
        self._in_transaction = True
        self._transaction_owner = asyncio.current_task()

    async def commit(self) -> None:
        assert self._conn is not None
        try:
            await self._conn.commit()
        finally:
            self._in_transaction = False
            self._transaction_owner = None
            self._lock.release()

    async def rollback(self) -> None:
        assert self._conn is not None
        try:
            await self._conn.rollback()
        finally:
            self._in_transaction = False
            self._transaction_owner = None
            self._lock.release()

    async def _maybe_commit(self) -> None:
        """Commit only when not inside an explicit transaction."""
        if not self._in_transaction:
            assert self._conn is not None
            await self._conn.commit()

    @asynccontextmanager
    async def _serialize(self) -> AsyncIterator[None]:
        """Hold the backend-wide lock unless the *current task* owns the open transaction.

        The live-recording flush loop and the import service both hold a
        reference to this same backend instance and can run concurrently.
        Only the task that itself called begin() may bypass the lock while a
        transaction is open (its own follow-up calls would otherwise
        deadlock against the lock it's already holding) — any *other* task
        must wait, or it would incorrectly piggyback its own write onto that
        task's transaction.
        """
        if self._transaction_owner is asyncio.current_task():
            yield
        else:
            async with self._lock:
                yield

    # ------------------------------------------------------------------
    # Entity management
    # ------------------------------------------------------------------

    async def get_or_create_entity(
        self, entity_id: str, domain: str, ts: datetime
    ) -> uuid.UUID:
        async with self._serialize():
            assert self._conn is not None
            ts_str = _fmt(ts)

            async with self._conn.execute(_SQL_SELECT_ENTITY, (entity_id,)) as cur:
                row = await cur.fetchone()

            if row is not None:
                pk = _from_blob(row["id"])
                await self._conn.execute(_SQL_UPDATE_ENTITY_LAST_SEEN, (ts_str, _to_blob(pk)))
                await self._maybe_commit()
                return pk

            from .uuid7 import uuid7
            pk = uuid7()
            await self._conn.execute(
                _SQL_INSERT_ENTITY, (_to_blob(pk), entity_id, domain, ts_str, ts_str)
            )
            await self._maybe_commit()
            return pk

    # ------------------------------------------------------------------
    # Observations
    # ------------------------------------------------------------------

    async def get_latest_observation(
        self, entity_pk: uuid.UUID, field_name: str
    ) -> ObservationRecord | None:
        async with self._serialize():
            assert self._conn is not None
            async with self._conn.execute(
                _SQL_SELECT_LATEST_OBS, (_to_blob(entity_pk), field_name)
            ) as cur:
                row = await cur.fetchone()
            return _row_to_record(row) if row is not None else None

    async def insert_observation(self, obs: ObservationRecord) -> None:
        async with self._serialize():
            assert self._conn is not None
            await self._conn.execute(
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
            await self._maybe_commit()

    async def update_last_seen(self, obs_id: uuid.UUID, ts: datetime) -> None:
        async with self._serialize():
            assert self._conn is not None
            await self._conn.execute(
                _SQL_UPDATE_OBS_LAST_SEEN, (_fmt(ts), _to_blob(obs_id))
            )
            await self._maybe_commit()

    async def has_observations_in_range(
        self,
        entity_pk: uuid.UUID,
        field_name: str,
        start: datetime,
        end: datetime,
    ) -> bool:
        async with self._serialize():
            assert self._conn is not None
            async with self._conn.execute(
                _SQL_HAS_OBS_IN_RANGE,
                (_to_blob(entity_pk), field_name, _fmt(end), _fmt(start)),
            ) as cur:
                row = await cur.fetchone()
            return row is not None


def _row_to_record(row: aiosqlite.Row) -> ObservationRecord:
    return ObservationRecord(
        id=_from_blob(row["id"]),
        entity_pk=_from_blob(row["entity_pk"]),
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
