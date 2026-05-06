from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from .base import ObservationRecord, StorageBackend

_PRAGMA_FK = "PRAGMA foreign_keys = ON"
_PRAGMA_WAL = "PRAGMA journal_mode = WAL"

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

    async def initialize(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute(_PRAGMA_FK)
        await self._conn.execute(_PRAGMA_WAL)
        await self._conn.execute(_SQL_CREATE_ENTITIES)
        await self._conn.execute(_SQL_CREATE_OBSERVATIONS)
        for sql in _SQL_CREATE_INDEXES:
            await self._conn.execute(sql)
        await self._conn.commit()

    async def get_or_create_entity(
        self, entity_id: str, domain: str, ts: datetime
    ) -> uuid.UUID:
        assert self._conn is not None
        ts_str = _fmt(ts)

        async with self._conn.execute(_SQL_SELECT_ENTITY, (entity_id,)) as cur:
            row = await cur.fetchone()

        if row is not None:
            pk = _from_blob(row["id"])
            await self._conn.execute(_SQL_UPDATE_ENTITY_LAST_SEEN, (ts_str, _to_blob(pk)))
            await self._conn.commit()
            return pk

        from .uuid7 import uuid7
        pk = uuid7()
        await self._conn.execute(
            _SQL_INSERT_ENTITY, (_to_blob(pk), entity_id, domain, ts_str, ts_str)
        )
        await self._conn.commit()
        return pk

    async def get_latest_observation(
        self, entity_pk: uuid.UUID, field_name: str
    ) -> ObservationRecord | None:
        assert self._conn is not None
        async with self._conn.execute(
            _SQL_SELECT_LATEST_OBS, (_to_blob(entity_pk), field_name)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_record(row) if row is not None else None

    async def insert_observation(self, obs: ObservationRecord) -> None:
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
        await self._conn.commit()

    async def update_last_seen(self, obs_id: uuid.UUID, ts: datetime) -> None:
        assert self._conn is not None
        await self._conn.execute(
            _SQL_UPDATE_OBS_LAST_SEEN, (_fmt(ts), _to_blob(obs_id))
        )
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None


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
