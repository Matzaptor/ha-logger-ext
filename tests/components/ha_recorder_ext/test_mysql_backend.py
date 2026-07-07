"""Behavioral tests for MySQLBackend (aiomysql fully mocked)."""
from __future__ import annotations

import sys
import types
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.ha_recorder_ext.storage.base import ObservationRecord
from custom_components.ha_recorder_ext.storage.uuid7 import uuid7

# ---------------------------------------------------------------------------
# Ensure mysql.py is importable even when aiomysql is not installed.
# All tests replace _mysql_module.aiomysql with a test-specific mock via
# patch.object, so this stub is only needed for the top-level import.
# ---------------------------------------------------------------------------
_AIOMYSQL_STUB = types.ModuleType("aiomysql")
_AIOMYSQL_STUB.Pool = object  # type: ignore[attr-defined]
_AIOMYSQL_STUB.Connection = object  # type: ignore[attr-defined]
_cursors_stub = types.ModuleType("aiomysql.cursors")
_cursors_stub.Cursor = object  # type: ignore[attr-defined]
_AIOMYSQL_STUB.cursors = _cursors_stub  # type: ignore[attr-defined]
sys.modules.setdefault("aiomysql", _AIOMYSQL_STUB)
sys.modules.setdefault("aiomysql.cursors", _cursors_stub)

import custom_components.ha_recorder_ext.storage.mysql as _mysql_module  # noqa: E402
from custom_components.ha_recorder_ext.storage.mysql import MySQLBackend  # noqa: E402

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------
TS = datetime(2026, 5, 6, 10, 0, 0, tzinfo=timezone.utc)
TS_STR = "2026-05-06T10:00:00+00:00"


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


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


class _DualMock:
    """An object that can be both awaited and used as an async context manager.

    aiomysql's pool.acquire() and conn.cursor() exhibit this dual interface.
    """

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


def _make_cursor() -> MagicMock:
    cur = MagicMock()
    cur.execute = AsyncMock()
    cur.fetchone = AsyncMock(return_value=None)
    return cur


def _make_connection(cursor: MagicMock) -> MagicMock:
    conn = MagicMock()
    conn.cursor = MagicMock(side_effect=lambda: _DualMock(cursor))
    conn.begin = AsyncMock()
    conn.commit = AsyncMock()
    conn.rollback = AsyncMock()
    conn.close = MagicMock()
    return conn


def _make_pool(conn: MagicMock) -> MagicMock:
    pool = MagicMock()
    pool.acquire = MagicMock(side_effect=lambda: _DualMock(conn))
    pool.close = MagicMock()
    pool.wait_closed = AsyncMock()
    return pool


def _make_aiomysql(pool: MagicMock) -> MagicMock:
    aiomysql_mock = MagicMock()
    aiomysql_mock.create_pool = AsyncMock(return_value=pool)
    aiomysql_mock.Pool = object
    aiomysql_mock.Connection = object
    aiomysql_mock.cursors = MagicMock()
    return aiomysql_mock


def _default_mocks() -> tuple[MagicMock, MagicMock, MagicMock, MagicMock]:
    """Return (cursor, conn, pool, aiomysql) with default AsyncMock wiring."""
    cur = _make_cursor()
    conn = _make_connection(cur)
    pool = _make_pool(conn)
    aiomysql_mock = _make_aiomysql(pool)
    return cur, conn, pool, aiomysql_mock


def _backend() -> MySQLBackend:
    return MySQLBackend(
        host="localhost",
        port=3306,
        database="testdb",
        username="user",
        password="pass",
    )


def _obs_row(obs: ObservationRecord) -> tuple:
    """Build a 14-element row tuple matching what MySQL returns for an observation."""
    return (
        obs.id.bytes,
        obs.entity_pk.bytes,
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
        TS_STR,
        TS_STR,
    )


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestMySQLBackend:
    """Full behavioral test coverage for MySQLBackend with aiomysql mocked."""

    # ------------------------------------------------------------------
    # Schema initialization & migrations
    # ------------------------------------------------------------------

    async def test_initialize_creates_schema_on_fresh_db(self) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        cur.fetchone = AsyncMock(return_value=None)  # no schema_version row yet

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()

        aiomysql_mock.create_pool.assert_called_once_with(
            host="localhost",
            port=3306,
            db="testdb",
            user="user",
            password="pass",
            autocommit=False,
            charset="utf8mb4",
        )
        conn.commit.assert_called_once()
        assert backend._pool is pool

    async def test_initialize_no_op_when_schema_already_current(self) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        cur.fetchone = AsyncMock(return_value=(1,))  # already at version 1

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()

        conn.commit.assert_not_called()

    async def test_initialize_raises_when_schema_newer_than_integration(self) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        cur.fetchone = AsyncMock(return_value=(9999,))

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            with pytest.raises(RuntimeError, match="newer than integration"):
                await backend.initialize()

    async def test_initialize_raises_on_missing_migration_function(self) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        cur.fetchone = AsyncMock(return_value=(1,))  # current version is 1; integration wants 2

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            with patch.object(_mysql_module, "_SCHEMA_VERSION", 2):
                with pytest.raises(NotImplementedError, match="No migration defined"):
                    await backend.initialize()

        conn.rollback.assert_called_once()

    async def test_initialize_migration_failure_triggers_rollback(self) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        cur.fetchone = AsyncMock(return_value=(1,))

        async def _bad_migration(_conn: object) -> None:
            raise ValueError("migration exploded")

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            with patch.object(_mysql_module, "_SCHEMA_VERSION", 2):
                with patch.object(_mysql_module, "_MIGRATIONS", {2: _bad_migration}):
                    with pytest.raises(ValueError, match="migration exploded"):
                        await backend.initialize()

        conn.rollback.assert_called_once()

    async def test_initialize_index_creation_failure_is_swallowed(self) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        cur.fetchone = AsyncMock(return_value=None)  # fresh schema

        def _exec_side_effect(sql: str, params: tuple = ()) -> None:
            if "CREATE INDEX" in sql:
                raise Exception("duplicate index")

        cur.execute = AsyncMock(side_effect=_exec_side_effect)

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()  # must not raise despite index errors

        conn.commit.assert_called_once()  # still commits after swallowed failures

    async def test_initialize_runs_registered_migration(self) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        cur.fetchone = AsyncMock(return_value=(1,))

        calls: list[str] = []

        async def _migration_v2(_conn: object) -> None:
            calls.append("v2")

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            with patch.object(_mysql_module, "_SCHEMA_VERSION", 2):
                with patch.object(_mysql_module, "_MIGRATIONS", {2: _migration_v2}):
                    await backend.initialize()

        assert calls == ["v2"]
        conn.commit.assert_called_once()  # final UPDATE + commit

    # ------------------------------------------------------------------
    # Lifecycle: close
    # ------------------------------------------------------------------

    async def test_close_before_initialize_is_safe(self) -> None:
        backend = _backend()
        await backend.close()  # pool is None — must not raise

    async def test_close_releases_pool(self) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        cur.fetchone = AsyncMock(return_value=(1,))

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()
            await backend.close()

        pool.close.assert_called_once()
        pool.wait_closed.assert_called_once()
        assert backend._pool is None

    async def test_close_releases_active_transaction_connection(self) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        cur.fetchone = AsyncMock(return_value=(1,))

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()
            await backend.begin()
            assert backend._conn is conn
            await backend.close()

        assert backend._conn is None
        assert backend._pool is None
        conn.close.assert_called_once()  # transaction connection explicitly closed

    # ------------------------------------------------------------------
    # Entity management
    # ------------------------------------------------------------------

    async def test_get_or_create_entity_inserts_new_entity(self) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        entity_pk = uuid7()
        # schema check → entity not found → entity found after INSERT IGNORE
        cur.fetchone = AsyncMock(side_effect=[(1,), None, (entity_pk.bytes,)])

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()
            pk = await backend.get_or_create_entity("sensor.temp", "sensor", TS)

        assert isinstance(pk, uuid.UUID)
        assert pk == entity_pk

    async def test_get_or_create_entity_returns_existing_entity(self) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        entity_pk = uuid7()
        cur.fetchone = AsyncMock(side_effect=[(1,), (entity_pk.bytes,)])

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()
            pk = await backend.get_or_create_entity("sensor.temp", "sensor", TS)

        assert pk == entity_pk

    async def test_get_or_create_entity_handles_insert_ignore_race(self) -> None:
        """When INSERT IGNORE is a no-op (race), the pk comes from the post-insert re-fetch."""
        cur, conn, pool, aiomysql_mock = _default_mocks()
        winner_pk = uuid7()
        cur.fetchone = AsyncMock(side_effect=[(1,), None, (winner_pk.bytes,)])

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()
            pk = await backend.get_or_create_entity("sensor.temp", "sensor", TS)

        assert pk == winner_pk

    async def test_get_or_create_entity_updates_last_seen_for_existing(self) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        entity_pk = uuid7()
        cur.fetchone = AsyncMock(side_effect=[(1,), (entity_pk.bytes,)])

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()
            cur.execute.reset_mock()
            await backend.get_or_create_entity("sensor.temp", "sensor", TS)

        update_calls = [c for c in cur.execute.call_args_list if "UPDATE entities" in c.args[0]]
        assert len(update_calls) == 1

    # ------------------------------------------------------------------
    # Observations: get_latest_observation
    # ------------------------------------------------------------------

    async def test_get_latest_observation_returns_record(self) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        entity_pk = uuid7()
        obs = _obs(entity_pk, "state", "float", value_float=21.5)
        cur.fetchone = AsyncMock(side_effect=[(1,), _obs_row(obs)])

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()
            result = await backend.get_latest_observation(entity_pk, "state")

        assert result is not None
        assert result.value_type == "float"
        assert result.value_float == 21.5
        assert result.field_name == "state"
        assert result.entity_pk == entity_pk

    async def test_get_latest_observation_returns_none_when_absent(self) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        cur.fetchone = AsyncMock(side_effect=[(1,), None])

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()
            result = await backend.get_latest_observation(uuid7(), "state")

        assert result is None

    @pytest.mark.parametrize("field,vtype,col_idx,value", [
        ("state", "str", 4, "on"),
        ("count", "int", 5, 42),
        ("temp", "float", 6, 21.5),
        ("flag", "bool", 7, 1),
        ("ts_field", "datetime", 8, "2026-05-06T10:00:00+00:00"),
        ("d_field", "date", 9, "2026-05-06"),
        ("t_field", "time", 10, "10:00:00"),
        ("data", "json", 11, '{"k": 1}'),
    ])
    async def test_get_latest_observation_all_value_types(
        self, field: str, vtype: str, col_idx: int, value: object
    ) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        entity_pk = uuid7()
        obs_id = uuid7()
        row = list((
            obs_id.bytes, entity_pk.bytes, field, vtype,
            None, None, None, None, None, None, None, None,
            TS_STR, TS_STR,
        ))
        row[col_idx] = value
        cur.fetchone = AsyncMock(side_effect=[(1,), tuple(row)])

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()
            result = await backend.get_latest_observation(entity_pk, field)

        assert result is not None
        assert result.value_type == vtype
        assert getattr(result, f"value_{vtype}") == value

    # ------------------------------------------------------------------
    # Observations: insert_observation
    # ------------------------------------------------------------------

    async def test_insert_observation_float(self) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        cur.fetchone = AsyncMock(return_value=(1,))
        entity_pk = uuid7()
        obs = _obs(entity_pk, "state", "float", value_float=21.5)

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()
            cur.execute.reset_mock()
            await backend.insert_observation(obs)

        insert_calls = [c for c in cur.execute.call_args_list if "INSERT INTO observations" in c.args[0]]
        assert len(insert_calls) == 1
        params = insert_calls[0].args[1]
        assert params[2] == "state"   # field
        assert params[3] == "float"   # value_type
        assert params[6] == 21.5      # value_float

    async def test_insert_observation_str(self) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        cur.fetchone = AsyncMock(return_value=(1,))
        entity_pk = uuid7()
        obs = _obs(entity_pk, "state", "str", value_str="on")

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()
            cur.execute.reset_mock()
            await backend.insert_observation(obs)

        insert_calls = [c for c in cur.execute.call_args_list if "INSERT INTO observations" in c.args[0]]
        params = insert_calls[0].args[1]
        assert params[3] == "str"
        assert params[4] == "on"  # value_str

    async def test_insert_observation_int(self) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        cur.fetchone = AsyncMock(return_value=(1,))
        entity_pk = uuid7()
        obs = _obs(entity_pk, "count", "int", value_int=99)

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()
            cur.execute.reset_mock()
            await backend.insert_observation(obs)

        insert_calls = [c for c in cur.execute.call_args_list if "INSERT INTO observations" in c.args[0]]
        params = insert_calls[0].args[1]
        assert params[3] == "int"
        assert params[5] == 99  # value_int

    async def test_insert_observation_bool(self) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        cur.fetchone = AsyncMock(return_value=(1,))
        entity_pk = uuid7()
        obs = _obs(entity_pk, "on", "bool", value_bool=1)

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()
            cur.execute.reset_mock()
            await backend.insert_observation(obs)

        insert_calls = [c for c in cur.execute.call_args_list if "INSERT INTO observations" in c.args[0]]
        params = insert_calls[0].args[1]
        assert params[3] == "bool"
        assert params[7] == 1  # value_bool

    async def test_insert_observation_json(self) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        cur.fetchone = AsyncMock(return_value=(1,))
        entity_pk = uuid7()
        obs = _obs(entity_pk, "attrs", "json", value_json='{"k": 1}')

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()
            cur.execute.reset_mock()
            await backend.insert_observation(obs)

        insert_calls = [c for c in cur.execute.call_args_list if "INSERT INTO observations" in c.args[0]]
        params = insert_calls[0].args[1]
        assert params[3] == "json"
        assert params[11] == '{"k": 1}'  # value_json

    async def test_insert_observation_encodes_uuids_as_bytes(self) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        cur.fetchone = AsyncMock(return_value=(1,))
        entity_pk = uuid7()
        obs = _obs(entity_pk, "state", "str", value_str="x")

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()
            cur.execute.reset_mock()
            await backend.insert_observation(obs)

        insert_calls = [c for c in cur.execute.call_args_list if "INSERT INTO observations" in c.args[0]]
        params = insert_calls[0].args[1]
        assert params[0] == obs.id.bytes         # id as BINARY(16)
        assert params[1] == entity_pk.bytes      # entity_pk as BINARY(16)

    async def test_insert_observation_formats_timestamps_as_utc_iso(self) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        cur.fetchone = AsyncMock(return_value=(1,))
        entity_pk = uuid7()
        obs = _obs(entity_pk, "state", "str", value_str="on")

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()
            cur.execute.reset_mock()
            await backend.insert_observation(obs)

        insert_calls = [c for c in cur.execute.call_args_list if "INSERT INTO observations" in c.args[0]]
        params = insert_calls[0].args[1]
        assert params[12] == TS_STR  # first_seen
        assert params[13] == TS_STR  # last_seen

    async def test_insert_observation_auto_commits_outside_transaction(self) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        cur.fetchone = AsyncMock(return_value=(1,))
        obs = _obs(uuid7(), "state", "float", value_float=1.0)

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()
            conn.commit.reset_mock()
            await backend.insert_observation(obs)

        conn.commit.assert_called_once()

    # ------------------------------------------------------------------
    # Observations: update_last_seen
    # ------------------------------------------------------------------

    async def test_update_last_seen_issues_correct_sql(self) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        cur.fetchone = AsyncMock(return_value=(1,))
        obs_id = uuid7()
        ts2 = TS + timedelta(hours=1)

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()
            cur.execute.reset_mock()
            await backend.update_last_seen(obs_id, ts2)

        update_calls = [c for c in cur.execute.call_args_list if "UPDATE observations SET last_seen" in c.args[0]]
        assert len(update_calls) == 1
        params = update_calls[0].args[1]
        assert params[0] == "2026-05-06T11:00:00+00:00"  # formatted ts
        assert params[1] == obs_id.bytes                  # BINARY(16)

    # ------------------------------------------------------------------
    # Observations: has_observations_in_range
    # ------------------------------------------------------------------

    async def test_has_observations_in_range_returns_true(self) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        cur.fetchone = AsyncMock(side_effect=[(1,), (1,)])  # schema, then found row

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()
            result = await backend.has_observations_in_range(
                uuid7(), "state", TS, TS + timedelta(hours=1)
            )

        assert result is True

    async def test_has_observations_in_range_returns_false(self) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        cur.fetchone = AsyncMock(side_effect=[(1,), None])

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()
            result = await backend.has_observations_in_range(
                uuid7(), "state", TS, TS + timedelta(hours=1)
            )

        assert result is False

    async def test_has_observations_in_range_passes_end_before_start_in_params(self) -> None:
        """SQL uses first_seen <= end AND last_seen >= start, so end is param[2], start is param[3]."""
        cur, conn, pool, aiomysql_mock = _default_mocks()
        cur.fetchone = AsyncMock(side_effect=[(1,), None])
        entity_pk = uuid7()
        start = TS
        end = TS + timedelta(hours=2)

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()
            cur.execute.reset_mock()
            await backend.has_observations_in_range(entity_pk, "state", start, end)

        range_calls = [c for c in cur.execute.call_args_list if "first_seen" in c.args[0]]
        assert len(range_calls) == 1
        params = range_calls[0].args[1]
        assert params[0] == entity_pk.bytes
        assert params[1] == "state"
        assert params[2] == _mysql_module._fmt(end)    # end comes before start
        assert params[3] == _mysql_module._fmt(start)

    # ------------------------------------------------------------------
    # Transaction control
    # ------------------------------------------------------------------

    async def test_begin_sets_transaction_state(self) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        cur.fetchone = AsyncMock(return_value=(1,))

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()
            await backend.begin()

        assert backend._in_transaction is True
        assert backend._conn is conn
        conn.begin.assert_called_once()

    async def test_commit_clears_transaction_state(self) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        cur.fetchone = AsyncMock(return_value=(1,))

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()
            await backend.begin()
            await backend.commit()

        assert backend._in_transaction is False
        assert backend._conn is None
        conn.commit.assert_called_once()
        conn.close.assert_called_once()

    async def test_rollback_clears_transaction_state(self) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        cur.fetchone = AsyncMock(return_value=(1,))

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()
            await backend.begin()
            await backend.rollback()

        assert backend._in_transaction is False
        assert backend._conn is None
        conn.rollback.assert_called_once()
        conn.close.assert_called_once()

    async def test_insert_in_transaction_does_not_call_pool_acquire(self) -> None:
        """Inside a transaction, _execute uses the dedicated conn, not pool.acquire()."""
        cur, conn, pool, aiomysql_mock = _default_mocks()
        cur.fetchone = AsyncMock(return_value=(1,))
        obs = _obs(uuid7(), "state", "int", value_int=42)

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()
            pool.acquire.reset_mock()

            await backend.begin()
            await backend.insert_observation(obs)
            await backend.commit()

        pool.acquire.assert_called_once()   # only the begin() acquire
        conn.commit.assert_called_once()    # only MySQLBackend.commit(), not _execute

    async def test_fetchone_in_transaction_uses_active_conn(self) -> None:
        """_fetchone in transaction mode goes through self._conn, not pool.acquire()."""
        cur, conn, pool, aiomysql_mock = _default_mocks()
        cur.fetchone = AsyncMock(side_effect=[(1,), None])

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()
            pool.acquire.reset_mock()

            await backend.begin()
            result = await backend.get_latest_observation(uuid7(), "state")
            assert result is None
            await backend.rollback()

        pool.acquire.assert_called_once()  # only begin()

    async def test_insert_in_transaction_does_not_auto_commit(self) -> None:
        cur, conn, pool, aiomysql_mock = _default_mocks()
        cur.fetchone = AsyncMock(return_value=(1,))
        obs = _obs(uuid7(), "state", "float", value_float=1.0)

        backend = _backend()
        with patch.object(_mysql_module, "aiomysql", aiomysql_mock):
            await backend.initialize()
            conn.commit.reset_mock()

            await backend.begin()
            await backend.insert_observation(obs)
            # commit not yet called — rollback instead
            await backend.rollback()

        conn.commit.assert_not_called()
        conn.rollback.assert_called_once()

    # ------------------------------------------------------------------
    # Helper functions (_fmt, _to_blob, _from_blob)
    # ------------------------------------------------------------------

    def test_fmt_naive_datetime_treated_as_utc(self) -> None:
        naive = datetime(2026, 1, 1, 12, 0, 0)
        result = _mysql_module._fmt(naive)
        assert "+00:00" in result
        assert "2026-01-01T12:00:00" in result

    def test_fmt_aware_datetime_converted_to_utc(self) -> None:
        tz_plus2 = timezone(timedelta(hours=2))
        aware = datetime(2026, 1, 1, 14, 0, 0, tzinfo=tz_plus2)
        result = _mysql_module._fmt(aware)
        assert result == "2026-01-01T12:00:00+00:00"

    def test_fmt_utc_datetime_unchanged(self) -> None:
        result = _mysql_module._fmt(TS)
        assert result == TS_STR

    def test_uuid_blob_roundtrip(self) -> None:
        u = uuid7()
        assert _mysql_module._from_blob(_mysql_module._to_blob(u)) == u

    def test_to_blob_returns_16_bytes(self) -> None:
        blob = _mysql_module._to_blob(uuid7())
        assert isinstance(blob, bytes)
        assert len(blob) == 16
