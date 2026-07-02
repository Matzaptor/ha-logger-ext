from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from custom_components.ha_logger_ext.const import (
    CONF_DB_HOST,
    CONF_DB_NAME,
    CONF_DB_PASSWORD,
    CONF_DB_PATH,
    CONF_DB_PORT,
    CONF_DB_USERNAME,
)
from custom_components.ha_logger_ext.external_recorder_reader import (
    ExternalRecorderSchemaError,
    MySQLExternalRecorderReader,
    PostgreSQLExternalRecorderReader,
    SQLiteExternalRecorderReader,
    create_external_recorder_reader,
)

TS0 = datetime(2022, 1, 1, 0, 0, tzinfo=timezone.utc)
TS1 = datetime(2022, 1, 1, 1, 0, tzinfo=timezone.utc)
TS2 = datetime(2022, 1, 1, 2, 0, tzinfo=timezone.utc)


async def _create_recorder_db(
    path: Path, rows: list[tuple[str, str, datetime, dict | None]]
) -> None:
    """Build a minimal, modern-schema (HA 2023.4+) recorder sqlite DB for tests."""
    async with aiosqlite.connect(path) as conn:
        await conn.execute(
            "CREATE TABLE states_meta ("
            "metadata_id INTEGER PRIMARY KEY, entity_id TEXT UNIQUE)"
        )
        await conn.execute(
            "CREATE TABLE state_attributes ("
            "attributes_id INTEGER PRIMARY KEY, shared_attrs TEXT)"
        )
        await conn.execute(
            "CREATE TABLE states ("
            "state_id INTEGER PRIMARY KEY, metadata_id INTEGER, state TEXT, "
            "attributes_id INTEGER, last_updated_ts REAL)"
        )
        metadata_ids: dict[str, int] = {}
        attribute_ids: dict[str, int] = {}
        for entity_id, state, ts, attrs in rows:
            if entity_id not in metadata_ids:
                cur = await conn.execute(
                    "INSERT INTO states_meta (entity_id) VALUES (?)", (entity_id,)
                )
                metadata_ids[entity_id] = cur.lastrowid

            attributes_id = None
            if attrs is not None:
                key = json.dumps(attrs, sort_keys=True)
                if key not in attribute_ids:
                    cur = await conn.execute(
                        "INSERT INTO state_attributes (shared_attrs) VALUES (?)", (key,)
                    )
                    attribute_ids[key] = cur.lastrowid
                attributes_id = attribute_ids[key]

            await conn.execute(
                "INSERT INTO states (metadata_id, state, attributes_id, last_updated_ts) "
                "VALUES (?, ?, ?, ?)",
                (metadata_ids[entity_id], state, attributes_id, ts.timestamp()),
            )
        await conn.commit()


class TestSQLiteExternalRecorderReader:
    async def test_fetch_all_entity_ids(self, tmp_path: Path) -> None:
        db_path = tmp_path / "recorder.db"
        await _create_recorder_db(
            db_path,
            [("sensor.a", "1", TS0, None), ("sensor.b", "2", TS0, None)],
        )
        reader = SQLiteExternalRecorderReader(db_path)
        await reader.connect()
        try:
            ids = await reader.fetch_all_entity_ids()
        finally:
            await reader.close()
        assert set(ids) == {"sensor.a", "sensor.b"}

    async def test_fetch_earliest_state_time(self, tmp_path: Path) -> None:
        db_path = tmp_path / "recorder.db"
        await _create_recorder_db(
            db_path,
            [("sensor.a", "1", TS1, None), ("sensor.a", "2", TS0, None)],
        )
        reader = SQLiteExternalRecorderReader(db_path)
        await reader.connect()
        try:
            earliest = await reader.fetch_earliest_state_time()
        finally:
            await reader.close()
        assert earliest == TS0

    async def test_fetch_earliest_state_time_empty_db(self, tmp_path: Path) -> None:
        db_path = tmp_path / "recorder.db"
        await _create_recorder_db(db_path, [])
        reader = SQLiteExternalRecorderReader(db_path)
        await reader.connect()
        try:
            earliest = await reader.fetch_earliest_state_time()
        finally:
            await reader.close()
        assert earliest is None

    async def test_fetch_states_returns_chronological_order_and_attributes(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "recorder.db"
        await _create_recorder_db(
            db_path,
            [
                ("sensor.a", "20", TS0, {"unit": "C"}),
                ("sensor.a", "21", TS1, {"unit": "C"}),
            ],
        )
        reader = SQLiteExternalRecorderReader(db_path)
        await reader.connect()
        try:
            states = await reader.fetch_states(TS0, TS2, ["sensor.a"])
        finally:
            await reader.close()

        assert list(states.keys()) == ["sensor.a"]
        rows = states["sensor.a"]
        assert [r.state for r in rows] == ["20", "21"]
        assert rows[0].attributes == {"unit": "C"}
        assert rows[0].last_updated == TS0

    async def test_fetch_states_without_attributes(self, tmp_path: Path) -> None:
        db_path = tmp_path / "recorder.db"
        await _create_recorder_db(db_path, [("sensor.a", "on", TS0, None)])
        reader = SQLiteExternalRecorderReader(db_path)
        await reader.connect()
        try:
            states = await reader.fetch_states(TS0, TS2, ["sensor.a"])
        finally:
            await reader.close()
        assert states["sensor.a"][0].attributes == {}

    async def test_fetch_states_filters_by_entity_id(self, tmp_path: Path) -> None:
        db_path = tmp_path / "recorder.db"
        await _create_recorder_db(
            db_path,
            [("sensor.a", "1", TS0, None), ("sensor.b", "2", TS0, None)],
        )
        reader = SQLiteExternalRecorderReader(db_path)
        await reader.connect()
        try:
            states = await reader.fetch_states(TS0, TS2, ["sensor.a"])
        finally:
            await reader.close()
        assert list(states.keys()) == ["sensor.a"]

    async def test_fetch_states_empty_entity_ids_returns_empty(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "recorder.db"
        await _create_recorder_db(db_path, [("sensor.a", "1", TS0, None)])
        reader = SQLiteExternalRecorderReader(db_path)
        await reader.connect()
        try:
            states = await reader.fetch_states(TS0, TS2, [])
        finally:
            await reader.close()
        assert states == {}

    async def test_connect_missing_states_meta_raises_schema_error(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "old_recorder.db"
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute(
                "CREATE TABLE states (state_id INTEGER PRIMARY KEY, "
                "entity_id TEXT, state TEXT)"
            )
            await conn.commit()

        reader = SQLiteExternalRecorderReader(db_path)
        with pytest.raises(ExternalRecorderSchemaError):
            await reader.connect()


# ---------------------------------------------------------------------------
# MySQL / PostgreSQL — mocked drivers (mirrors tests/.../test_mysql_backend.py)
# ---------------------------------------------------------------------------


class _DualMock:
    """Awaitable object that also works as an async context manager."""

    def __init__(self, inner: object) -> None:
        self._inner = inner

    def __await__(self):
        async def _coro():
            return self._inner
        return _coro().__await__()

    async def __aenter__(self):
        return self._inner

    async def __aexit__(self, *args: object) -> None:
        pass


def _make_aiomysql_module(pool: MagicMock) -> MagicMock:
    """A fake 'aiomysql' module exposing create_pool, for patch.dict(sys.modules, ...).

    Patching sys.modules directly (rather than patching the 'create_pool'
    attribute on the real module) is required here because other test modules
    in this suite install a partial aiomysql stub into sys.modules at import
    time, which would otherwise be picked up first during test collection.
    """
    module = MagicMock()
    module.create_pool = AsyncMock(return_value=pool)
    return module


class TestMySQLExternalRecorderReader:
    def _reader(self) -> MySQLExternalRecorderReader:
        return MySQLExternalRecorderReader(
            host="localhost", port=3306, database="testdb", username="u", password="p"
        )

    async def test_connect_checks_schema_and_raises_when_missing(self) -> None:
        cur = MagicMock()
        cur.execute = AsyncMock()
        cur.fetchone = AsyncMock(return_value=None)  # states_meta not found
        conn = MagicMock()
        conn.cursor = MagicMock(side_effect=lambda: _DualMock(cur))
        pool = MagicMock()
        pool.acquire = MagicMock(side_effect=lambda: _DualMock(conn))
        pool.close = MagicMock()
        pool.wait_closed = AsyncMock()

        with patch.dict(sys.modules, {"aiomysql": _make_aiomysql_module(pool)}):
            reader = self._reader()
            with pytest.raises(ExternalRecorderSchemaError):
                await reader.connect()

    async def test_fetch_all_entity_ids_issues_expected_query(self) -> None:
        cur = MagicMock()
        cur.execute = AsyncMock()
        cur.fetchone = AsyncMock(return_value=(1,))  # schema check succeeds
        cur.fetchall = AsyncMock(return_value=[("sensor.a",), ("sensor.b",)])
        conn = MagicMock()
        conn.cursor = MagicMock(side_effect=lambda: _DualMock(cur))
        pool = MagicMock()
        pool.acquire = MagicMock(side_effect=lambda: _DualMock(conn))

        with patch.dict(sys.modules, {"aiomysql": _make_aiomysql_module(pool)}):
            reader = self._reader()
            await reader.connect()
            ids = await reader.fetch_all_entity_ids()

        assert ids == ["sensor.a", "sensor.b"]
        assert "states_meta" in cur.execute.call_args_list[-1].args[0]

    async def test_fetch_states_builds_dynamic_in_placeholders(self) -> None:
        cur = MagicMock()
        cur.execute = AsyncMock()
        cur.fetchone = AsyncMock(return_value=(1,))
        cur.fetchall = AsyncMock(
            return_value=[("sensor.a", "20", TS0.timestamp(), '{"unit": "C"}')]
        )
        conn = MagicMock()
        conn.cursor = MagicMock(side_effect=lambda: _DualMock(cur))
        pool = MagicMock()
        pool.acquire = MagicMock(side_effect=lambda: _DualMock(conn))

        with patch.dict(sys.modules, {"aiomysql": _make_aiomysql_module(pool)}):
            reader = self._reader()
            await reader.connect()
            states = await reader.fetch_states(TS0, TS2, ["sensor.a", "sensor.b"])

        sql, params = cur.execute.call_args_list[-1].args
        assert sql.count("%s") == 4  # start, end, + 2 entity_ids
        assert params[-2:] == ("sensor.a", "sensor.b")
        assert states["sensor.a"][0].attributes == {"unit": "C"}


class TestPostgreSQLExternalRecorderReader:
    def _reader(self) -> PostgreSQLExternalRecorderReader:
        return PostgreSQLExternalRecorderReader(
            host="localhost", port=5432, database="testdb", username="u", password="p"
        )

    async def test_connect_checks_schema_and_raises_when_missing(self) -> None:
        conn = MagicMock()
        conn.fetchrow = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(side_effect=lambda: _DualMock(conn))
        pool.close = AsyncMock()

        with patch("asyncpg.create_pool", AsyncMock(return_value=pool)):
            reader = self._reader()
            with pytest.raises(ExternalRecorderSchemaError):
                await reader.connect()

    async def test_fetch_states_uses_array_bind_for_entity_ids(self) -> None:
        conn = MagicMock()
        conn.fetchrow = AsyncMock(return_value={"?column?": 1})
        conn.fetch = AsyncMock(
            return_value=[
                {
                    "entity_id": "sensor.a",
                    "state": "20",
                    "last_updated_ts": TS0.timestamp(),
                    "shared_attrs": '{"unit": "C"}',
                }
            ]
        )
        pool = MagicMock()
        pool.acquire = MagicMock(side_effect=lambda: _DualMock(conn))

        with patch("asyncpg.create_pool", AsyncMock(return_value=pool)):
            reader = self._reader()
            await reader.connect()
            states = await reader.fetch_states(TS0, TS2, ["sensor.a", "sensor.b"])

        args = conn.fetch.call_args.args
        assert args[-1] == ["sensor.a", "sensor.b"]
        assert states["sensor.a"][0].attributes == {"unit": "C"}


class TestCreateExternalRecorderReader:
    def test_sqlite_requires_db_path(self) -> None:
        with pytest.raises(ValueError):
            create_external_recorder_reader("sqlite", {})

    def test_sqlite_returns_sqlite_reader(self, tmp_path: Path) -> None:
        reader = create_external_recorder_reader(
            "sqlite", {CONF_DB_PATH: str(tmp_path / "x.db")}
        )
        assert isinstance(reader, SQLiteExternalRecorderReader)

    def test_mysql_returns_mysql_reader(self) -> None:
        reader = create_external_recorder_reader(
            "mysql",
            {
                CONF_DB_HOST: "localhost",
                CONF_DB_PORT: 3306,
                CONF_DB_NAME: "db",
                CONF_DB_USERNAME: "u",
                CONF_DB_PASSWORD: "p",
            },
        )
        assert isinstance(reader, MySQLExternalRecorderReader)

    def test_postgresql_returns_postgresql_reader(self) -> None:
        reader = create_external_recorder_reader(
            "postgresql",
            {
                CONF_DB_HOST: "localhost",
                CONF_DB_PORT: 5432,
                CONF_DB_NAME: "db",
                CONF_DB_USERNAME: "u",
                CONF_DB_PASSWORD: "p",
            },
        )
        assert isinstance(reader, PostgreSQLExternalRecorderReader)

    def test_unknown_db_type_raises(self) -> None:
        with pytest.raises(ValueError):
            create_external_recorder_reader("duckdb", {})
