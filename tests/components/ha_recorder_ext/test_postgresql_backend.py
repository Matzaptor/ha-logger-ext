"""Behavioral tests for PostgreSQLBackend connection handling (asyncpg mocked)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.ha_recorder_ext.storage.postgresql import PostgreSQLBackend


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


def _backend() -> PostgreSQLBackend:
    return PostgreSQLBackend(
        host="localhost", port=5432, database="testdb", username="user", password="pass"
    )


class TestPostgreSQLBackendTimeouts:
    async def test_initialize_passes_connect_and_command_timeout(self) -> None:
        conn = MagicMock()
        conn.execute = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"version": 1})
        conn.transaction = MagicMock(return_value=_DualMock(None))
        pool = MagicMock()
        pool.acquire = MagicMock(side_effect=lambda: _DualMock(conn))

        create_pool = AsyncMock(return_value=pool)
        with patch("asyncpg.create_pool", create_pool):
            backend = _backend()
            await backend.initialize()

        assert create_pool.call_args.kwargs["timeout"] == 10
        assert create_pool.call_args.kwargs["command_timeout"] == 300


class TestPostgreSQLSchemaMigrations:
    async def test_migration_v2_retrofits_missing_indexes_on_v1_database(self) -> None:
        """A database already stamped at schema_version 1 must get the
        observation indexes (re-)created by the v2 migration — a no-op
        safety net on PostgreSQL, whose CREATE INDEX IF NOT EXISTS was
        never broken, but exercises the same retrofit path relied on to
        fix the real gap on the MySQL backend."""
        conn = MagicMock()
        conn.execute = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"version": 1})
        conn.transaction = MagicMock(return_value=_DualMock(None))
        pool = MagicMock()
        pool.acquire = MagicMock(side_effect=lambda: _DualMock(conn))

        create_pool = AsyncMock(return_value=pool)
        with patch("asyncpg.create_pool", create_pool):
            backend = _backend()
            await backend.initialize()

        executed_sql = [call.args[0] for call in conn.execute.call_args_list]
        assert sum(sql.startswith("CREATE INDEX IF NOT EXISTS") for sql in executed_sql) == 3
        assert any(sql.startswith("UPDATE schema_version") for sql in executed_sql)


class TestPostgreSQLBackendConcurrency:
    async def test_concurrent_call_waits_for_open_transaction(self) -> None:
        """A concurrent read/write while a transaction is open (e.g. the live
        flush loop's begin()...commit(), running concurrently with an import
        using the same backend instance) must wait for commit()/rollback(),
        not run interleaved with it — asyncpg is not safe for concurrent use
        of one connection."""
        conn = MagicMock()
        conn.execute = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"version": 1})
        tx = MagicMock()
        tx.start = AsyncMock()
        tx.commit = AsyncMock()
        conn.transaction = MagicMock(return_value=tx)
        pool = MagicMock()
        pool.acquire = MagicMock(side_effect=lambda: _DualMock(conn))
        pool.release = AsyncMock()

        create_pool = AsyncMock(return_value=pool)
        with patch("asyncpg.create_pool", create_pool):
            backend = _backend()
            await backend.initialize()
            await backend.begin()

            order: list[str] = []

            async def _concurrent_fetch() -> None:
                await backend._fetchrow("SELECT 1")
                order.append("fetch_done")

            task = asyncio.create_task(_concurrent_fetch())
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert not task.done(), "concurrent call must block while the transaction is open"

            order.append("commit")
            await backend.commit()
            await task

        assert order == ["commit", "fetch_done"]
