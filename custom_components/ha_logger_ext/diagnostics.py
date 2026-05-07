from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_DB_PATH,
    CONF_DB_TYPE,
    CONF_EXCLUDE_ATTRIBUTES,
    CONF_EXCLUDE_DOMAINS,
    CONF_EXCLUDE_ENTITIES,
    CONF_FLUSH_INTERVAL,
    CONF_QUEUE_MAX_SIZE,
    DEFAULT_FLUSH_INTERVAL,
    DEFAULT_QUEUE_MAX_SIZE,
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    from . import LoggerCoordinator

    coordinator: LoggerCoordinator = entry.runtime_data
    return {
        "config": {
            CONF_DB_TYPE: entry.data.get(CONF_DB_TYPE),
            CONF_DB_PATH: entry.data.get(CONF_DB_PATH),
            CONF_EXCLUDE_DOMAINS: entry.data.get(CONF_EXCLUDE_DOMAINS, []),
            CONF_EXCLUDE_ENTITIES: entry.data.get(CONF_EXCLUDE_ENTITIES, []),
            CONF_EXCLUDE_ATTRIBUTES: entry.data.get(CONF_EXCLUDE_ATTRIBUTES, []),
        },
        "options": {
            CONF_FLUSH_INTERVAL: entry.options.get(
                CONF_FLUSH_INTERVAL, DEFAULT_FLUSH_INTERVAL
            ),
            CONF_QUEUE_MAX_SIZE: entry.options.get(
                CONF_QUEUE_MAX_SIZE, DEFAULT_QUEUE_MAX_SIZE
            ),
        },
        "coordinator": {
            "is_running": coordinator.is_running,
            "queue_size": coordinator.queue_size,
            "flush_interval": coordinator.flush_interval,
            "last_flush": (
                coordinator.last_flush.isoformat()
                if coordinator.last_flush is not None
                else None
            ),
        },
    }
