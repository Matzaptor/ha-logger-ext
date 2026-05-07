from __future__ import annotations

import sys
import types
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.ha_logger_ext.storage.base import ObservationRecord
from custom_components.ha_logger_ext.storage.factory import create_backend

try:
    from custom_components.ha_logger_ext.storage.duckdb import DuckDBBackend
    _DUCKDB_AVAILABLE = True
except ImportError:
    _DUCKDB_AVAILABLE = False
    DuckDBBackend = None  # type: ignore[assignment,misc]
from custom_components.ha_logger_ext.storage.uuid7 import uuid7
from custom_components.ha_logger_ext.const import (
    CONF_DB_HOST,
    CONF_DB_NAME,
    CONF_DB_PASSWORD,
    CONF_DB_PATH,
    CONF_DB_PORT,
    CONF_DB_TYPE,
    CONF_DB_USERNAME,
    DB_TYPE_DUCKDB,
    DB_TYPE_MYSQL,
    DB_TYPE_POSTGRESQL,
    DEFAULT_MYSQL_PORT,
    DEFAULT_POSTGRESQL_PORT,
)

TS = datetime(2026, 5, 6, 10, 0, 0, tzinfo=timezone.utc)


def _obs(entity_pk: uuid.UUID, field: str, value_type: str, **kwargs: object) -> ObservationRecord:
    return ObservationRecord(
        id=uuid7(),
        entity_pk=entity_pk,
        field_name=field,
        value_type=value_type,
        first_seen=TS,
        last_seen=TS,
        **kwargs,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# DuckDB — real tests (embedded, no network)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _DUCKDB_AVAILABLE, reason="duckdb not installed")
class TestDuckDBBackend:
    async def test_duckdb_creates_entity(self, tmp_path: Path) -> None:
        backend = DuckDBBackend(tmp_path / "test.duckdb")
        await backend.initialize()
        pk = await backend.get_or_create_entity("sensor.temp", "sensor", TS)
        assert isinstance(pk, uuid.UUID)
        await backend.close()

    async def test_duckdb_insert_and_retrieve_observation(self, tmp_path: Path) -> None:
        backend = DuckDBBackend(tmp_path / "test.duckdb")
        await backend.initialize()

        pk = await backend.get_or_create_entity("sensor.temp", "sensor", TS)
        obs = _obs(pk, "state", "float", value_float=21.5)
        await backend.insert_observation(obs)

        latest = await backend.get_latest_observation(pk, "state")
        assert latest is not None
        assert latest.value_type == "float"
        assert latest.value_float == 21.5
        assert latest.field_name == "state"

        await backend.close()

    async def test_duckdb_deduplication(self, tmp_path: Path) -> None:
        backend = DuckDBBackend(tmp_path / "test.duckdb")
        await backend.initialize()

        pk = await backend.get_or_create_entity("sensor.temp", "sensor", TS)
        obs = _obs(pk, "state", "float", value_float=20.0)
        await backend.insert_observation(obs)

        ts2 = datetime(2026, 5, 6, 11, 0, 0, tzinfo=timezone.utc)
        await backend.update_last_seen(obs.id, ts2)

        latest = await backend.get_latest_observation(pk, "state")
        assert latest is not None
        assert latest.id == obs.id
        assert latest.value_float == 20.0

        await backend.close()

    async def test_duckdb_close_before_initialize_is_safe(self, tmp_path: Path) -> None:
        backend = DuckDBBackend(tmp_path / "test.duckdb")
        await backend.close()

    async def test_duckdb_schema_newer_than_integration_raises(self, tmp_path: Path) -> None:
        import duckdb

        db_path = tmp_path / "test.duckdb"
        conn = duckdb.connect(str(db_path))
        conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version (version) VALUES (9999)")
        conn.commit()
        conn.close()

        backend = DuckDBBackend(db_path)
        with pytest.raises(RuntimeError, match="newer than integration"):
            await backend.initialize()


# ---------------------------------------------------------------------------
# MySQL — factory tests (aiomysql mocked)
# ---------------------------------------------------------------------------

class TestMySQLFactory:
    def test_mysql_factory_creates_mysql_backend(self, tmp_path: Path) -> None:
        aiomysql_mock = types.ModuleType("aiomysql")
        aiomysql_mock.Pool = object  # type: ignore[attr-defined]
        aiomysql_mock.Connection = object  # type: ignore[attr-defined]
        aiomysql_mock.cursors = types.ModuleType("aiomysql.cursors")  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"aiomysql": aiomysql_mock}):
            from custom_components.ha_logger_ext.storage.mysql import MySQLBackend

            config = {
                CONF_DB_TYPE: DB_TYPE_MYSQL,
                CONF_DB_HOST: "localhost",
                CONF_DB_PORT: DEFAULT_MYSQL_PORT,
                CONF_DB_NAME: "test",
                CONF_DB_USERNAME: "u",
                CONF_DB_PASSWORD: "p",
            }
            backend = create_backend(config, str(tmp_path))
            assert isinstance(backend, MySQLBackend)

    def test_mysql_unknown_db_type_still_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Unknown database type"):
            create_backend({"db_type": "oracle"}, str(tmp_path))


# ---------------------------------------------------------------------------
# PostgreSQL — factory tests (asyncpg mocked)
# ---------------------------------------------------------------------------

class TestPostgreSQLFactory:
    def test_postgresql_factory_creates_postgresql_backend(self, tmp_path: Path) -> None:
        asyncpg_mock = types.ModuleType("asyncpg")
        asyncpg_mock.Pool = object  # type: ignore[attr-defined]
        asyncpg_mock.Connection = object  # type: ignore[attr-defined]
        asyncpg_mock.Record = object  # type: ignore[attr-defined]
        transaction_mod = types.ModuleType("asyncpg.transaction")
        transaction_mod.Transaction = object  # type: ignore[attr-defined]
        asyncpg_mock.transaction = transaction_mod  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"asyncpg": asyncpg_mock, "asyncpg.transaction": transaction_mod}):
            from custom_components.ha_logger_ext.storage.postgresql import PostgreSQLBackend

            config = {
                CONF_DB_TYPE: DB_TYPE_POSTGRESQL,
                CONF_DB_HOST: "pg.local",
                CONF_DB_PORT: DEFAULT_POSTGRESQL_PORT,
                CONF_DB_NAME: "test",
                CONF_DB_USERNAME: "pg_user",
                CONF_DB_PASSWORD: "pgpass",
            }
            backend = create_backend(config, str(tmp_path))
            assert isinstance(backend, PostgreSQLBackend)
