from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .const import (
    CONF_DB_HOST,
    CONF_DB_NAME,
    CONF_DB_PASSWORD,
    CONF_DB_PATH,
    CONF_DB_PORT,
    CONF_DB_USERNAME,
)

_SCHEMA_ERROR_MESSAGE = (
    "Table 'states_meta' not found. This looks like a pre-2023.4 HA recorder "
    "backup (entity_id was still stored inline on 'states'), which is not "
    "supported. Only the modern normalized recorder schema (states_meta + "
    "state_attributes) is supported."
)


class ExternalRecorderSchemaError(Exception):
    """The external database does not look like a supported HA recorder backup."""


@dataclass
class ExternalStateRow:
    """One state row read from an external HA recorder database.

    Duck-type compatible with the objects importer._collect_field_values()
    expects: .state, .attributes (mapping), .last_updated (datetime).
    """

    entity_id: str
    state: str
    attributes: dict[str, Any]
    last_updated: datetime


def _group_rows(
    rows: list[tuple[str, str, float, str | None]],
) -> dict[str, list[ExternalStateRow]]:
    grouped: dict[str, list[ExternalStateRow]] = {}
    for entity_id, state, last_updated_ts, shared_attrs in rows:
        attributes = json.loads(shared_attrs) if shared_attrs else {}
        row = ExternalStateRow(
            entity_id=entity_id,
            state=state,
            attributes=attributes,
            last_updated=datetime.fromtimestamp(float(last_updated_ts), tz=timezone.utc),
        )
        grouped.setdefault(entity_id, []).append(row)
    return grouped


class ExternalRecorderReader(ABC):
    """Read-only access to a Home Assistant Recorder database hosted elsewhere.

    Targets the modern, normalized recorder schema (states_meta + states +
    state_attributes, HA 2023.4+). Never writes to the external database.
    """

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def fetch_all_entity_ids(self) -> list[str]: ...

    @abstractmethod
    async def fetch_earliest_state_time(self) -> datetime | None: ...

    @abstractmethod
    async def fetch_states(
        self, start: datetime, end: datetime, entity_ids: list[str]
    ) -> dict[str, list[ExternalStateRow]]: ...


# ----------------------------------------------------------------------
# SQLite
# ----------------------------------------------------------------------

_SQLITE_CHECK_SCHEMA = (
    "SELECT name FROM sqlite_master WHERE type='table' AND name='states_meta'"
)
_SQLITE_SELECT_ENTITY_IDS = "SELECT DISTINCT entity_id FROM states_meta ORDER BY entity_id"
_SQLITE_SELECT_EARLIEST_TS = "SELECT MIN(last_updated_ts) FROM states"


def _sqlite_select_states_sql(entity_count: int) -> str:
    placeholders = ",".join("?" * entity_count)
    return f"""
        SELECT sm.entity_id, s.state, s.last_updated_ts, sa.shared_attrs
        FROM states s
        JOIN states_meta sm ON s.metadata_id = sm.metadata_id
        LEFT JOIN state_attributes sa ON s.attributes_id = sa.attributes_id
        WHERE s.last_updated_ts >= ? AND s.last_updated_ts < ?
          AND sm.entity_id IN ({placeholders})
        ORDER BY sm.entity_id, s.last_updated_ts
    """


class SQLiteExternalRecorderReader(ExternalRecorderReader):
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: Any = None

    async def connect(self) -> None:
        import aiosqlite

        db_uri = f"file:{quote(str(self._db_path))}?mode=ro"
        self._conn = await aiosqlite.connect(db_uri, uri=True)
        async with self._conn.execute(_SQLITE_CHECK_SCHEMA) as cur:
            row = await cur.fetchone()
        if row is None:
            await self._conn.close()
            self._conn = None
            raise ExternalRecorderSchemaError(_SCHEMA_ERROR_MESSAGE)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def fetch_all_entity_ids(self) -> list[str]:
        assert self._conn is not None
        async with self._conn.execute(_SQLITE_SELECT_ENTITY_IDS) as cur:
            rows = await cur.fetchall()
        return [row[0] for row in rows]

    async def fetch_earliest_state_time(self) -> datetime | None:
        assert self._conn is not None
        async with self._conn.execute(_SQLITE_SELECT_EARLIEST_TS) as cur:
            row = await cur.fetchone()
        if row is None or row[0] is None:
            return None
        return datetime.fromtimestamp(float(row[0]), tz=timezone.utc)

    async def fetch_states(
        self, start: datetime, end: datetime, entity_ids: list[str]
    ) -> dict[str, list[ExternalStateRow]]:
        if not entity_ids:
            return {}
        assert self._conn is not None
        sql = _sqlite_select_states_sql(len(entity_ids))
        params = (start.timestamp(), end.timestamp(), *entity_ids)
        async with self._conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return _group_rows([tuple(row) for row in rows])


# ----------------------------------------------------------------------
# MySQL
# ----------------------------------------------------------------------

_MYSQL_CHECK_SCHEMA = (
    "SELECT 1 FROM information_schema.tables "
    "WHERE table_schema = DATABASE() AND table_name = 'states_meta'"
)
_MYSQL_SELECT_ENTITY_IDS = "SELECT DISTINCT entity_id FROM states_meta"
_MYSQL_SELECT_EARLIEST_TS = "SELECT MIN(last_updated_ts) FROM states"


def _mysql_select_states_sql(entity_count: int) -> str:
    placeholders = ",".join(["%s"] * entity_count)
    return f"""
        SELECT sm.entity_id, s.state, s.last_updated_ts, sa.shared_attrs
        FROM states s
        JOIN states_meta sm ON s.metadata_id = sm.metadata_id
        LEFT JOIN state_attributes sa ON s.attributes_id = sa.attributes_id
        WHERE s.last_updated_ts >= %s AND s.last_updated_ts < %s
          AND sm.entity_id IN ({placeholders})
        ORDER BY sm.entity_id, s.last_updated_ts
    """


class MySQLExternalRecorderReader(ExternalRecorderReader):
    def __init__(
        self, host: str, port: int, database: str, username: str, password: str
    ) -> None:
        self._host = host
        self._port = port
        self._database = database
        self._username = username
        self._password = password
        self._pool: Any = None

    async def connect(self) -> None:
        import aiomysql

        self._pool = await aiomysql.create_pool(
            host=self._host,
            port=self._port,
            db=self._database,
            user=self._username,
            password=self._password,
            autocommit=True,
            charset="utf8mb4",
        )
        async with self._pool.acquire() as conn, conn.cursor() as cur:
            await cur.execute(_MYSQL_CHECK_SCHEMA)
            row = await cur.fetchone()
        if row is None:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None
            raise ExternalRecorderSchemaError(_SCHEMA_ERROR_MESSAGE)

    async def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None

    async def fetch_all_entity_ids(self) -> list[str]:
        assert self._pool is not None
        async with self._pool.acquire() as conn, conn.cursor() as cur:
            await cur.execute(_MYSQL_SELECT_ENTITY_IDS)
            rows = await cur.fetchall()
        return [row[0] for row in rows]

    async def fetch_earliest_state_time(self) -> datetime | None:
        assert self._pool is not None
        async with self._pool.acquire() as conn, conn.cursor() as cur:
            await cur.execute(_MYSQL_SELECT_EARLIEST_TS)
            row = await cur.fetchone()
        if row is None or row[0] is None:
            return None
        return datetime.fromtimestamp(float(row[0]), tz=timezone.utc)

    async def fetch_states(
        self, start: datetime, end: datetime, entity_ids: list[str]
    ) -> dict[str, list[ExternalStateRow]]:
        if not entity_ids:
            return {}
        assert self._pool is not None
        sql = _mysql_select_states_sql(len(entity_ids))
        params = (start.timestamp(), end.timestamp(), *entity_ids)
        async with self._pool.acquire() as conn, conn.cursor() as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()
        return _group_rows([tuple(row) for row in rows])


# ----------------------------------------------------------------------
# PostgreSQL
# ----------------------------------------------------------------------

_POSTGRESQL_CHECK_SCHEMA = (
    "SELECT 1 FROM information_schema.tables WHERE table_name = 'states_meta'"
)
_POSTGRESQL_SELECT_ENTITY_IDS = "SELECT DISTINCT entity_id FROM states_meta"
_POSTGRESQL_SELECT_EARLIEST_TS = "SELECT MIN(last_updated_ts) FROM states"
_POSTGRESQL_SELECT_STATES = """
    SELECT sm.entity_id, s.state, s.last_updated_ts, sa.shared_attrs
    FROM states s
    JOIN states_meta sm ON s.metadata_id = sm.metadata_id
    LEFT JOIN state_attributes sa ON s.attributes_id = sa.attributes_id
    WHERE s.last_updated_ts >= $1 AND s.last_updated_ts < $2
      AND sm.entity_id = ANY($3::text[])
    ORDER BY sm.entity_id, s.last_updated_ts
"""


class PostgreSQLExternalRecorderReader(ExternalRecorderReader):
    def __init__(
        self, host: str, port: int, database: str, username: str, password: str
    ) -> None:
        self._host = host
        self._port = port
        self._database = database
        self._username = username
        self._password = password
        self._pool: Any = None

    async def connect(self) -> None:
        import asyncpg

        self._pool = await asyncpg.create_pool(
            host=self._host,
            port=self._port,
            database=self._database,
            user=self._username,
            password=self._password,
        )
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_POSTGRESQL_CHECK_SCHEMA)
        if row is None:
            await self._pool.close()
            self._pool = None
            raise ExternalRecorderSchemaError(_SCHEMA_ERROR_MESSAGE)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def fetch_all_entity_ids(self) -> list[str]:
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_POSTGRESQL_SELECT_ENTITY_IDS)
        return [row["entity_id"] for row in rows]

    async def fetch_earliest_state_time(self) -> datetime | None:
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            value = await conn.fetchval(_POSTGRESQL_SELECT_EARLIEST_TS)
        if value is None:
            return None
        return datetime.fromtimestamp(float(value), tz=timezone.utc)

    async def fetch_states(
        self, start: datetime, end: datetime, entity_ids: list[str]
    ) -> dict[str, list[ExternalStateRow]]:
        if not entity_ids:
            return {}
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                _POSTGRESQL_SELECT_STATES, start.timestamp(), end.timestamp(), entity_ids
            )
        tuples = [
            (row["entity_id"], row["state"], row["last_updated_ts"], row["shared_attrs"])
            for row in rows
        ]
        return _group_rows(tuples)


# ----------------------------------------------------------------------
# Factory
# ----------------------------------------------------------------------


def create_external_recorder_reader(
    db_type: str, config: dict[str, Any]
) -> ExternalRecorderReader:
    """Return an ExternalRecorderReader for the given db_type and connection params.

    `config` uses the same CONF_DB_* keys as a service call's data dict.
    """
    match db_type:
        case "sqlite":
            raw_path = config.get(CONF_DB_PATH)
            if not raw_path:
                raise ValueError("db_path is required when db_type is 'sqlite'")
            return SQLiteExternalRecorderReader(Path(raw_path))

        case "mysql":
            try:
                import aiomysql  # noqa: F401
            except ImportError as err:
                raise ValueError(
                    "The 'aiomysql' package is not installed. "
                    "Install it manually: pip install aiomysql>=0.2.0"
                ) from err
            return MySQLExternalRecorderReader(
                host=config[CONF_DB_HOST],
                port=int(config[CONF_DB_PORT]),
                database=config[CONF_DB_NAME],
                username=config[CONF_DB_USERNAME],
                password=config[CONF_DB_PASSWORD],
            )

        case "postgresql":
            try:
                import asyncpg  # noqa: F401
            except ImportError as err:
                raise ValueError(
                    "The 'asyncpg' package is not installed. "
                    "Install it manually: pip install asyncpg>=0.29.0"
                ) from err
            return PostgreSQLExternalRecorderReader(
                host=config[CONF_DB_HOST],
                port=int(config[CONF_DB_PORT]),
                database=config[CONF_DB_NAME],
                username=config[CONF_DB_USERNAME],
                password=config[CONF_DB_PASSWORD],
            )

        case _:
            raise ValueError(f"Unknown database type: {db_type!r}")
