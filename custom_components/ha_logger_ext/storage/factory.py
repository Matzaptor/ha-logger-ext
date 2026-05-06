from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import StorageBackend
from .sqlite import SQLiteBackend
from ..const import CONF_DB_PATH, CONF_DB_TYPE, DB_TYPE_SQLITE, DEFAULT_DB_PATH


def create_backend(config: dict[str, Any], config_dir: str) -> StorageBackend:
    """Return a StorageBackend instance based on the integration config entry data."""
    db_type = config.get(CONF_DB_TYPE, DB_TYPE_SQLITE)
    match db_type:
        case "sqlite":
            raw = config.get(CONF_DB_PATH, DEFAULT_DB_PATH)
            path = Path(raw)
            if not path.is_absolute():
                path = Path(config_dir) / path
            return SQLiteBackend(path)
        case "mysql" | "postgresql":
            raise NotImplementedError(f"Backend '{db_type}' is not yet implemented.")
        case _:
            raise ValueError(f"Unknown database type: {db_type!r}")
