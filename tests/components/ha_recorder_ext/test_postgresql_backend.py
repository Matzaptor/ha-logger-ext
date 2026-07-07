"""Behavioral tests for PostgreSQLBackend connection handling (asyncpg mocked)."""
from __future__ import annotations

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
