from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from .base import ObservationRecord, StorageBackend

_LOGGER = logging.getLogger(__name__)

_SCHEMA_VERSION = 1

_SQL_CREATE_SCHEMA_VERSION = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
)
"""

_SQL_CREATE_ENTITIES = """
CREATE TABLE IF NOT EXISTS entities (
    id          BLOB NOT NULL PRIMARY KEY,
    entity_id   VARCHAR NOT NULL UNIQUE,
    domain      VARCHAR NOT NULL,
    first_seen  VARCHAR NOT NULL,
    last_seen   VARCHAR NOT NULL
)
"""

_SQL_CREATE_OBSERVATIONS = """
CREATE TABLE IF NOT EXISTS observations (
    id             BLOB NOT NULL PRIMARY KEY,
    entity_pk      BLOB NOT NULL REFERENCES entities(id),
    field          VARCHAR NOT NULL,
    value_type     VARCHAR NOT NULL CHECK (value_type IN (
                       'str', 'int', 'float', 'bool', 'null',
                       'datetime', 'date', 'time', 'timedelta', 'json'
                   )),
    value_str      VARCHAR,
    value_int      BIGINT,
    value_float    DOUBLE,
    value_bool     INTEGER CHECK (value_bool IN (0, 1) OR value_bool IS NULL),
    value_datetime VARCHAR,
    value_date     VARCHAR,
    value_time     VARCHAR,
    value_json     VARCHAR,
    first_seen     VARCHAR NOT NULL,
    last_seen      VARCHAR NOT NULL
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

_SQL_HAS_OBS_IN_RANGE = """
SELECT 1 FROM observations
WHERE entity_pk = ? AND field = ?
  AND first_seen <= ?
  AND last_seen  >= ?
LIMIT 1
"""

# Maps target schema version → sync migration function.
# Each function receives an open duckdb.DuckDBPyConnection and must not commit.
_MigrationFn = Callable[[duckdb.DuckDBPyConnection], None]
_MIGRATIONS: dict[int, _MigrationFn] = {}


def _to_blob(u: uuid.UUID) -> bytes:
    return u.bytes


def _from_blob(b: bytes) -> uuid.UUID:
    return uuid.UUID(bytes=b)


def _fmt(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


class DuckDBBackend(StorageBackend):
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: duckdb.DuckDBPyConnection | None = None
        self._in_transaction = False

    # ------------------------------------------------------------------
    # Executor helper
    # ------------------------------------------------------------------

    async def _run_sync(self, fn: Callable[[], Any]) -> Any:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, fn)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        def _open() -> duckdb.DuckDBPyConnection:
            return duckdb.connect(str(self._db_path))

        self._conn = await self._run_sync(_open)
        await self._run_sync(self._apply_migrations_sync)

    async def close(self) -> None:
        if self._conn is not None:
            conn = self._conn
            self._conn = None
            await self._run_sync(conn.close)

    # ------------------------------------------------------------------
    # Schema migrations (sync, runs inside executor)
    # ------------------------------------------------------------------

    def _apply_migrations_sync(self) -> None:
        conn = self._conn
        assert conn is not None

        conn.execute(_SQL_CREATE_SCHEMA_VERSION)

        row = conn.execute(_SQL_SELECT_SCHEMA_VERSION).fetchone()
        current: int = row[0] if row else 0

        if current == 0:
            _LOGGER.debug(
                "Initializing fresh DuckDB schema (version %d)", _SCHEMA_VERSION
            )
            conn.execute(_SQL_CREATE_ENTITIES)
            conn.execute(_SQL_CREATE_OBSERVATIONS)
            for sql in _SQL_CREATE_INDEXES:
                conn.execute(sql)
            conn.execute(_SQL_INSERT_SCHEMA_VERSION, [_SCHEMA_VERSION])
            conn.commit()
            return

        if current == _SCHEMA_VERSION:
            return

        if current > _SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema version {current} is newer than integration "
                f"schema version {_SCHEMA_VERSION}. Please update the integration."
            )

        conn.begin()
        try:
            for target in range(current + 1, _SCHEMA_VERSION + 1):
                _LOGGER.info("Migrating DuckDB schema to version %d", target)
                migrate = _MIGRATIONS.get(target)
                if migrate is None:
                    raise NotImplementedError(
                        f"No migration defined for schema version {target}."
                    )
                migrate(conn)
            conn.execute(_SQL_UPDATE_SCHEMA_VERSION, [_SCHEMA_VERSION])
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # ------------------------------------------------------------------
    # Transaction control
    # ------------------------------------------------------------------

    async def begin(self) -> None:
        conn = self._conn
        assert conn is not None
        await self._run_sync(conn.begin)
        self._in_transaction = True

    async def commit(self) -> None:
        conn = self._conn
        assert conn is not None
        await self._run_sync(conn.commit)
        self._in_transaction = False

    async def rollback(self) -> None:
        conn = self._conn
        assert conn is not None
        await self._run_sync(conn.rollback)
        self._in_transaction = False

    async def _maybe_commit(self) -> None:
        if not self._in_transaction:
            conn = self._conn
            assert conn is not None
            await self._run_sync(conn.commit)

    # ------------------------------------------------------------------
    # Entity management
    # ------------------------------------------------------------------

    async def get_or_create_entity(
        self, entity_id: str, domain: str, ts: datetime
    ) -> uuid.UUID:
        assert self._conn is not None
        ts_str = _fmt(ts)

        def _select() -> tuple[Any, ...] | None:
            return self._conn.execute(_SQL_SELECT_ENTITY, [entity_id]).fetchone()

        row = await self._run_sync(_select)

        if row is not None:
            pk = _from_blob(bytes(row[0]))

            def _update() -> None:
                self._conn.execute(
                    _SQL_UPDATE_ENTITY_LAST_SEEN, [ts_str, _to_blob(pk)]
                )

            await self._run_sync(_update)
            await self._maybe_commit()
            return pk

        from .uuid7 import uuid7
        pk = uuid7()

        def _insert() -> None:
            self._conn.execute(
                _SQL_INSERT_ENTITY,
                [_to_blob(pk), entity_id, domain, ts_str, ts_str],
            )

        await self._run_sync(_insert)
        await self._maybe_commit()
        return pk

    # ------------------------------------------------------------------
    # Observations
    # ------------------------------------------------------------------

    async def get_latest_observation(
        self, entity_pk: uuid.UUID, field_name: str
    ) -> ObservationRecord | None:
        assert self._conn is not None

        def _select() -> tuple[Any, ...] | None:
            return self._conn.execute(
                _SQL_SELECT_LATEST_OBS, [_to_blob(entity_pk), field_name]
            ).fetchone()

        row = await self._run_sync(_select)
        return _row_to_record(row) if row is not None else None

    async def insert_observation(self, obs: ObservationRecord) -> None:
        assert self._conn is not None
        params = [
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
        ]

        def _insert() -> None:
            self._conn.execute(_SQL_INSERT_OBS, params)

        await self._run_sync(_insert)
        await self._maybe_commit()

    async def update_last_seen(self, obs_id: uuid.UUID, ts: datetime) -> None:
        assert self._conn is not None
        params = [_fmt(ts), _to_blob(obs_id)]

        def _update() -> None:
            self._conn.execute(_SQL_UPDATE_OBS_LAST_SEEN, params)

        await self._run_sync(_update)
        await self._maybe_commit()

    async def has_observations_in_range(
        self,
        entity_pk: uuid.UUID,
        field_name: str,
        start: datetime,
        end: datetime,
    ) -> bool:
        assert self._conn is not None
        params = [_to_blob(entity_pk), field_name, _fmt(end), _fmt(start)]

        def _select() -> tuple[Any, ...] | None:
            return self._conn.execute(_SQL_HAS_OBS_IN_RANGE, params).fetchone()

        row = await self._run_sync(_select)
        return row is not None


def _row_to_record(row: tuple[Any, ...]) -> ObservationRecord:
    # DuckDB returns plain tuples; columns ordered as per _SQL_SELECT_LATEST_OBS.
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
