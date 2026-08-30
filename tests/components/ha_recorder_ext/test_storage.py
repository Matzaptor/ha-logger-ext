from __future__ import annotations

import asyncio
import sys
import types
import aiosqlite
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from custom_components.ha_recorder_ext.const import DB_TYPE_SQLITE, DEFAULT_DB_PATH
from custom_components.ha_recorder_ext.storage.base import ObservationRecord
from custom_components.ha_recorder_ext.storage.factory import create_backend
from custom_components.ha_recorder_ext.storage.serialization import serialize, values_equal
from custom_components.ha_recorder_ext.storage.sqlite import SQLiteBackend, _MIGRATIONS, _SCHEMA_VERSION
from custom_components.ha_recorder_ext.storage.uuid7 import uuid7

TS = datetime(2026, 5, 6, 10, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
async def backend(tmp_path: Path) -> SQLiteBackend:
    b = SQLiteBackend(tmp_path / "test.db")
    await b.initialize()
    yield b
    await b.close()


def _obs(entity_pk, field, value_type, **kwargs) -> ObservationRecord:
    return ObservationRecord(
        id=uuid7(),
        entity_pk=entity_pk,
        field_name=field,
        value_type=value_type,
        first_seen=TS,
        last_seen=TS,
        **kwargs,
    )


class TestEntityManagement:
    async def test_creates_entity(self, backend: SQLiteBackend) -> None:
        pk = await backend.get_or_create_entity("sensor.temp", "sensor", TS)
        assert pk is not None

    async def test_same_entity_id_returns_same_pk(self, backend: SQLiteBackend) -> None:
        pk1 = await backend.get_or_create_entity("sensor.temp", "sensor", TS)
        pk2 = await backend.get_or_create_entity("sensor.temp", "sensor", TS)
        assert pk1 == pk2

    async def test_different_entities_have_different_pks(self, backend: SQLiteBackend) -> None:
        pk1 = await backend.get_or_create_entity("sensor.temp", "sensor", TS)
        pk2 = await backend.get_or_create_entity("sensor.humidity", "sensor", TS)
        assert pk1 != pk2


class TestObservations:
    async def test_no_observation_returns_none(self, backend: SQLiteBackend) -> None:
        pk = await backend.get_or_create_entity("sensor.temp", "sensor", TS)
        assert await backend.get_latest_observation(pk, "state") is None

    async def test_insert_and_retrieve_float(self, backend: SQLiteBackend) -> None:
        pk = await backend.get_or_create_entity("sensor.temp", "sensor", TS)
        obs = _obs(pk, "state", "float", value_float=20.5)
        await backend.insert_observation(obs)

        latest = await backend.get_latest_observation(pk, "state")
        assert latest is not None
        assert latest.value_type == "float"
        assert latest.value_float == 20.5

    async def test_insert_and_retrieve_str(self, backend: SQLiteBackend) -> None:
        pk = await backend.get_or_create_entity("sensor.temp", "sensor", TS)
        obs = _obs(pk, "unit_of_measurement", "str", value_str="°C")
        await backend.insert_observation(obs)

        latest = await backend.get_latest_observation(pk, "unit_of_measurement")
        assert latest.value_str == "°C"

    async def test_update_last_seen(self, backend: SQLiteBackend) -> None:
        pk = await backend.get_or_create_entity("sensor.temp", "sensor", TS)
        obs = _obs(pk, "state", "float", value_float=20.5)
        await backend.insert_observation(obs)

        new_ts = TS + timedelta(minutes=5)
        await backend.update_last_seen(obs.id, new_ts)

        latest = await backend.get_latest_observation(pk, "state")
        assert latest.first_seen == TS
        assert latest.last_seen == new_ts

    async def test_different_fields_are_independent(self, backend: SQLiteBackend) -> None:
        pk = await backend.get_or_create_entity("sensor.temp", "sensor", TS)
        await backend.insert_observation(_obs(pk, "state", "float", value_float=20.5))
        await backend.insert_observation(_obs(pk, "unit_of_measurement", "str", value_str="°C"))

        state = await backend.get_latest_observation(pk, "state")
        unit = await backend.get_latest_observation(pk, "unit_of_measurement")

        assert state.value_float == 20.5
        assert unit.value_str == "°C"

    async def test_get_latest_returns_most_recent(self, backend: SQLiteBackend) -> None:
        pk = await backend.get_or_create_entity("sensor.temp", "sensor", TS)
        obs1 = ObservationRecord(
            id=uuid7(), entity_pk=pk, field_name="state", value_type="float",
            value_float=20.0, first_seen=TS, last_seen=TS,
        )
        obs2 = ObservationRecord(
            id=uuid7(), entity_pk=pk, field_name="state", value_type="float",
            value_float=21.0,
            first_seen=TS + timedelta(minutes=1),
            last_seen=TS + timedelta(minutes=1),
        )
        await backend.insert_observation(obs1)
        await backend.insert_observation(obs2)

        latest = await backend.get_latest_observation(pk, "state")
        assert latest.value_float == 21.0


class TestDeduplicationLogic:
    """Test the deduplication pattern used by RecorderCoordinator._record_field."""

    async def _record(
        self, backend: SQLiteBackend, entity_pk, field: str, raw_value, ts: datetime
    ) -> None:
        serialized = serialize(raw_value)
        latest = await backend.get_latest_observation(entity_pk, field)
        if latest is not None:
            latest_dict = {
                "value_type": latest.value_type,
                "value_str": latest.value_str,
                "value_int": latest.value_int,
                "value_float": latest.value_float,
                "value_bool": latest.value_bool,
                "value_datetime": latest.value_datetime,
                "value_date": latest.value_date,
                "value_time": latest.value_time,
                "value_json": latest.value_json,
            }
            if values_equal(latest_dict, serialized):
                await backend.update_last_seen(latest.id, ts)
                return
        obs = ObservationRecord(
            id=uuid7(),
            entity_pk=entity_pk,
            field_name=field,
            value_type=serialized["value_type"],
            value_float=serialized.get("value_float"),
            value_str=serialized.get("value_str"),
            first_seen=ts,
            last_seen=ts,
        )
        await backend.insert_observation(obs)

    async def test_repeated_value_extends_last_seen(self, backend: SQLiteBackend) -> None:
        pk = await backend.get_or_create_entity("sensor.temp", "sensor", TS)
        t0, t1, t2 = TS, TS + timedelta(minutes=1), TS + timedelta(minutes=2)

        await self._record(backend, pk, "state", 20.0, t0)
        await self._record(backend, pk, "state", 20.0, t1)
        await self._record(backend, pk, "state", 20.0, t2)

        latest = await backend.get_latest_observation(pk, "state")
        assert latest.first_seen == t0   # original first_seen preserved
        assert latest.last_seen == t2    # last_seen updated

    async def test_value_change_opens_new_interval(self, backend: SQLiteBackend) -> None:
        # 20 → 21 → 20 must produce 3 rows, not 2
        pk = await backend.get_or_create_entity("sensor.temp", "sensor", TS)
        t0 = TS
        t1 = TS + timedelta(minutes=1)
        t2 = TS + timedelta(minutes=2)

        await self._record(backend, pk, "state", 20.0, t0)
        await self._record(backend, pk, "state", 21.0, t1)
        await self._record(backend, pk, "state", 20.0, t2)

        # latest row is the second "20", opened at t2
        latest = await backend.get_latest_observation(pk, "state")
        assert latest.value_float == 20.0
        assert latest.first_seen == t2   # new interval, not the first one

    async def test_type_change_opens_new_interval(self, backend: SQLiteBackend) -> None:
        pk = await backend.get_or_create_entity("sensor.door", "binary_sensor", TS)
        t0 = TS
        t1 = TS + timedelta(seconds=10)

        await self._record(backend, pk, "state", "on", t0)
        await self._record(backend, pk, "state", "off", t1)

        latest = await backend.get_latest_observation(pk, "state")
        assert latest.value_str == "off"
        assert latest.first_seen == t1


class TestSchemaMigrations:
    async def test_fresh_db_gets_schema_version(self, tmp_path: Path) -> None:
        b = SQLiteBackend(tmp_path / "test.db")
        await b.initialize()

        async with aiosqlite.connect(tmp_path / "test.db") as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute("SELECT version FROM schema_version") as cur:
                row = await cur.fetchone()
        assert row["version"] == _SCHEMA_VERSION

        await b.close()

    async def test_existing_db_without_schema_version_is_adopted(
        self, tmp_path: Path
    ) -> None:
        """Databases created before schema_version was introduced are adopted at v1."""
        db_path = tmp_path / "old.db"

        # Create a database with the old structure (no schema_version table)
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute("""
                CREATE TABLE entities (
                    id BLOB NOT NULL PRIMARY KEY,
                    entity_id TEXT NOT NULL UNIQUE,
                    domain TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL
                )
            """)
            await conn.execute("""
                CREATE TABLE observations (
                    id BLOB NOT NULL PRIMARY KEY,
                    entity_pk BLOB NOT NULL,
                    field TEXT NOT NULL,
                    value_type TEXT NOT NULL,
                    value_str TEXT,
                    value_int INTEGER,
                    value_float REAL,
                    value_bool INTEGER,
                    value_datetime TEXT,
                    value_date TEXT,
                    value_time TEXT,
                    value_json TEXT,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL
                )
            """)
            await conn.commit()

        # initialize() should adopt the existing DB without errors
        b = SQLiteBackend(db_path)
        await b.initialize()

        async with aiosqlite.connect(db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute("SELECT version FROM schema_version") as cur:
                row = await cur.fetchone()
        assert row["version"] == _SCHEMA_VERSION

        await b.close()

    async def test_newer_schema_version_raises(self, tmp_path: Path) -> None:
        """If DB has a newer schema version than the code, raise RuntimeError."""
        db_path = tmp_path / "future.db"

        async with aiosqlite.connect(db_path) as conn:
            await conn.execute(
                "CREATE TABLE schema_version (version INTEGER NOT NULL)"
            )
            await conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (_SCHEMA_VERSION + 1,)
            )
            await conn.commit()

        b = SQLiteBackend(db_path)
        with pytest.raises(RuntimeError, match="newer"):
            await b.initialize()
        await b.close()

    async def test_migration_dispatcher_is_called(self, tmp_path: Path) -> None:
        """_MIGRATIONS dispatch table is invoked for each incremental version step."""
        db_path = tmp_path / "migrate.db"

        # Seed a v1 database (current schema, without schema_version table so
        # initialize() treats it as version 0 and stamps it at 1).
        b = SQLiteBackend(db_path)
        await b.initialize()
        await b.close()

        # Patch _MIGRATIONS to register a fake v2 migration and bump the code version.
        called: list[int] = []

        async def _fake_v2(conn: aiosqlite.Connection) -> None:
            called.append(2)

        import custom_components.ha_recorder_ext.storage.sqlite as _mod
        original_version = _mod._SCHEMA_VERSION
        original_migrations = dict(_mod._MIGRATIONS)
        try:
            _mod._SCHEMA_VERSION = 2
            _mod._MIGRATIONS[2] = _fake_v2

            b2 = SQLiteBackend(db_path)
            await b2.initialize()
            await b2.close()
        finally:
            _mod._SCHEMA_VERSION = original_version
            _mod._MIGRATIONS.clear()
            _mod._MIGRATIONS.update(original_migrations)

        assert called == [2]

        async with aiosqlite.connect(db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute("SELECT version FROM schema_version") as cur:
                row = await cur.fetchone()
        assert row["version"] == 2

    async def test_missing_migration_raises(self, tmp_path: Path) -> None:
        """NotImplementedError if a required migration has no registered function."""
        db_path = tmp_path / "missing.db"

        b = SQLiteBackend(db_path)
        await b.initialize()
        await b.close()

        import custom_components.ha_recorder_ext.storage.sqlite as _mod
        original_version = _mod._SCHEMA_VERSION
        try:
            _mod._SCHEMA_VERSION = 2  # bump without registering a migration

            b2 = SQLiteBackend(db_path)
            with pytest.raises(NotImplementedError):
                await b2.initialize()
            await b2.close()
        finally:
            _mod._SCHEMA_VERSION = original_version


class TestBatchTransactions:
    async def test_begin_defers_commit(self, tmp_path: Path) -> None:
        b = SQLiteBackend(tmp_path / "test.db")
        await b.initialize()
        pk = await b.get_or_create_entity("sensor.temp", "sensor", TS)

        await b.begin()
        obs = ObservationRecord(
            id=__import__("custom_components.ha_recorder_ext.storage.uuid7", fromlist=["uuid7"]).uuid7(),
            entity_pk=pk, field_name="state", value_type="float",
            value_float=20.0, first_seen=TS, last_seen=TS,
        )
        await b.insert_observation(obs)
        # commit not yet called — record exists in the transaction but
        # reading within same connection shows it (SQLite in-transaction reads)
        latest = await b.get_latest_observation(pk, "state")
        assert latest is not None

        await b.commit()
        await b.close()

    async def test_rollback_discards_writes(self, tmp_path: Path) -> None:
        b = SQLiteBackend(tmp_path / "test.db")
        await b.initialize()
        pk = await b.get_or_create_entity("sensor.temp", "sensor", TS)

        await b.begin()
        from custom_components.ha_recorder_ext.storage.uuid7 import uuid7
        obs = ObservationRecord(
            id=uuid7(), entity_pk=pk, field_name="state",
            value_type="float", value_float=99.0, first_seen=TS, last_seen=TS,
        )
        await b.insert_observation(obs)
        await b.rollback()

        latest = await b.get_latest_observation(pk, "state")
        assert latest is None
        await b.close()

    async def test_concurrent_call_waits_for_open_transaction(
        self, tmp_path: Path
    ) -> None:
        """A concurrent read/write while a transaction is open (e.g. the live
        flush loop's begin()...commit(), running concurrently with an import
        using the same backend instance) must wait for commit()/rollback(),
        not run interleaved with it."""
        b = SQLiteBackend(tmp_path / "test.db")
        await b.initialize()

        await b.begin()

        order: list[str] = []

        async def _concurrent_write() -> None:
            await b.get_or_create_entity("sensor.other", "sensor", TS)
            order.append("write_done")

        task = asyncio.create_task(_concurrent_write())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not task.done(), "concurrent call must block while the transaction is open"

        order.append("commit")
        await b.commit()
        await task
        await b.close()

        assert order == ["commit", "write_done"]


class TestCreateBackend:
    def test_default_path_uses_dedicated_subdirectory(self, tmp_path: Path) -> None:
        backend = create_backend(
            {"db_type": DB_TYPE_SQLITE},
            config_dir=str(tmp_path),
        )
        expected = tmp_path / DEFAULT_DB_PATH
        assert backend._db_path == expected

    async def test_creates_parent_directory_for_default_path(self, tmp_path: Path) -> None:
        # Directory creation happens in SQLiteBackend.initialize(), off the
        # event loop — create_backend() itself only resolves the path now.
        backend = create_backend({"db_type": DB_TYPE_SQLITE}, config_dir=str(tmp_path))
        await backend.initialize()
        try:
            expected_dir = (tmp_path / DEFAULT_DB_PATH).parent
            assert expected_dir.is_dir()
        finally:
            await backend.close()

    async def test_creates_parent_directory_for_nested_custom_path(self, tmp_path: Path) -> None:
        backend = create_backend(
            {"db_type": DB_TYPE_SQLITE, "db_path": "subdir/nested/custom.db"},
            config_dir=str(tmp_path),
        )
        await backend.initialize()
        try:
            assert (tmp_path / "subdir" / "nested").is_dir()
        finally:
            await backend.close()

    async def test_absolute_path_creates_parent_directory(self, tmp_path: Path) -> None:
        abs_db = tmp_path / "abs" / "data.db"
        backend = create_backend(
            {"db_type": DB_TYPE_SQLITE, "db_path": str(abs_db)},
            config_dir="/irrelevant",
        )
        await backend.initialize()
        try:
            assert abs_db.parent.is_dir()
        finally:
            await backend.close()

    def test_returns_sqlite_backend_instance(self, tmp_path: Path) -> None:
        backend = create_backend({"db_type": DB_TYPE_SQLITE}, config_dir=str(tmp_path))
        assert isinstance(backend, SQLiteBackend)

    def test_unknown_db_type_raises_value_error(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Unknown database type"):
            create_backend({"db_type": "oracle"}, config_dir=str(tmp_path))

    def test_duckdb_raises_temporarily_disabled_error(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="temporarily disabled"):
            create_backend({"db_type": "duckdb"}, config_dir=str(tmp_path))

    def test_mysql_factory_returns_mysql_backend(self, tmp_path: Path) -> None:
        aiomysql_mock = types.ModuleType("aiomysql")
        aiomysql_mock.Pool = object  # type: ignore[attr-defined]
        aiomysql_mock.Connection = object  # type: ignore[attr-defined]
        aiomysql_mock.cursors = types.ModuleType("aiomysql.cursors")  # type: ignore[attr-defined]
        with patch.dict(sys.modules, {"aiomysql": aiomysql_mock}):
            from custom_components.ha_recorder_ext.storage.mysql import MySQLBackend
            backend = create_backend({"db_type": "mysql"}, config_dir=str(tmp_path))
            assert isinstance(backend, MySQLBackend)

    def test_postgresql_factory_returns_postgresql_backend(self, tmp_path: Path) -> None:
        asyncpg_mock = types.ModuleType("asyncpg")
        asyncpg_mock.Pool = object  # type: ignore[attr-defined]
        asyncpg_mock.Connection = object  # type: ignore[attr-defined]
        asyncpg_mock.Record = object  # type: ignore[attr-defined]
        transaction_mod = types.ModuleType("asyncpg.transaction")
        transaction_mod.Transaction = object  # type: ignore[attr-defined]
        asyncpg_mock.transaction = transaction_mod  # type: ignore[attr-defined]
        with patch.dict(sys.modules, {"asyncpg": asyncpg_mock, "asyncpg.transaction": transaction_mod}):
            from custom_components.ha_recorder_ext.storage.postgresql import PostgreSQLBackend
            backend = create_backend({"db_type": "postgresql"}, config_dir=str(tmp_path))
            assert isinstance(backend, PostgreSQLBackend)


class TestSQLiteBackendEdgeCases:
    async def test_close_before_initialize_is_safe(self, tmp_path: Path) -> None:
        b = SQLiteBackend(tmp_path / "test.db")
        await b.close()  # must not raise

    async def test_initialize_is_idempotent(self, tmp_path: Path) -> None:
        b = SQLiteBackend(tmp_path / "test.db")
        await b.initialize()
        await b.initialize()  # second call must not raise or corrupt schema
        pk = await b.get_or_create_entity("sensor.temp", "sensor", TS)
        assert pk is not None
        await b.close()

    async def test_schema_already_at_current_version(self, tmp_path: Path) -> None:
        db_file = tmp_path / "test.db"
        b = SQLiteBackend(db_file)
        await b.initialize()
        await b.close()

        # Re-open — schema already at _SCHEMA_VERSION; no migration should run.
        b2 = SQLiteBackend(db_file)
        await b2.initialize()
        pk = await b2.get_or_create_entity("sensor.temp", "sensor", TS)
        assert pk is not None
        await b2.close()

    async def test_schema_newer_than_integration_raises_runtime_error(
        self, tmp_path: Path
    ) -> None:
        db_file = tmp_path / "test.db"
        async with aiosqlite.connect(db_file) as db:
            await db.execute(
                "CREATE TABLE schema_version (version INTEGER NOT NULL)"
            )
            # Claim a future version that the integration doesn't know about.
            await db.execute("INSERT INTO schema_version VALUES (999)")
            await db.commit()

        b = SQLiteBackend(db_file)
        try:
            with pytest.raises(RuntimeError, match="newer than integration"):
                await b.initialize()
        finally:
            await b.close()
