from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import StorageBackend
from .sqlite import SQLiteBackend
from ..const import (
    CONF_DB_HOST,
    CONF_DB_NAME,
    CONF_DB_PASSWORD,
    CONF_DB_PATH,
    CONF_DB_PORT,
    CONF_DB_TYPE,
    CONF_DB_USERNAME,
    DB_TYPE_MYSQL,
    DB_TYPE_POSTGRESQL,
    DB_TYPE_SQLITE,
    DEFAULT_DB_HOST,
    DEFAULT_DB_NAME,
    DEFAULT_DB_PATH,
    DEFAULT_MYSQL_PORT,
    DEFAULT_POSTGRESQL_PORT,
)


def create_backend(config: dict[str, Any], config_dir: str) -> StorageBackend:
    """Return a StorageBackend instance based on the integration config entry data."""
    db_type = config.get(CONF_DB_TYPE, DB_TYPE_SQLITE)
    match db_type:
        case "sqlite":
            raw = config.get(CONF_DB_PATH, DEFAULT_DB_PATH)
            path = Path(raw)
            if not path.is_absolute():
                path = Path(config_dir) / path
            path.parent.mkdir(parents=True, exist_ok=True)
            return SQLiteBackend(path)

        case "duckdb":
            # Temporarily disabled: duckdb's native worker threads crash the
            # interpreter on shutdown under Python 3.14
            # ("Fatal Python error: gilstate_tss_set"). Re-enable once
            # upstream duckdb ships a fix for this Python version.
            raise ValueError(
                "The 'duckdb' backend is temporarily disabled pending a "
                "Python 3.14 compatibility fix upstream. Use sqlite, mysql, "
                "or postgresql instead."
            )

        case "mysql":
            try:
                from .mysql import MySQLBackend
            except ImportError as err:
                raise ValueError(
                    "The 'aiomysql' package is not installed. "
                    "Install it manually: pip install aiomysql>=0.2.0"
                ) from err
            return MySQLBackend(
                host=config.get(CONF_DB_HOST, DEFAULT_DB_HOST),
                port=int(config.get(CONF_DB_PORT, DEFAULT_MYSQL_PORT)),
                database=config.get(CONF_DB_NAME, DEFAULT_DB_NAME),
                username=config.get(CONF_DB_USERNAME, ""),
                password=config.get(CONF_DB_PASSWORD, ""),
            )

        case "postgresql":
            try:
                from .postgresql import PostgreSQLBackend
            except ImportError as err:
                raise ValueError(
                    "The 'asyncpg' package is not installed. "
                    "Install it manually: pip install asyncpg>=0.29.0"
                ) from err
            return PostgreSQLBackend(
                host=config.get(CONF_DB_HOST, DEFAULT_DB_HOST),
                port=int(config.get(CONF_DB_PORT, DEFAULT_POSTGRESQL_PORT)),
                database=config.get(CONF_DB_NAME, DEFAULT_DB_NAME),
                username=config.get(CONF_DB_USERNAME, ""),
                password=config.get(CONF_DB_PASSWORD, ""),
            )

        case _:
            raise ValueError(f"Unknown database type: {db_type!r}")
