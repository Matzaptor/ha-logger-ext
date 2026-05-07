from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_logger_ext.const import (
    CONF_DB_PATH,
    CONF_DB_TYPE,
    CONF_EXCLUDE_ATTRIBUTES,
    CONF_EXCLUDE_DOMAINS,
    CONF_EXCLUDE_ENTITIES,
    CONF_FLUSH_INTERVAL,
    CONF_QUEUE_MAX_SIZE,
    DB_TYPE_SQLITE,
    DEFAULT_FLUSH_INTERVAL,
    DEFAULT_QUEUE_MAX_SIZE,
    DOMAIN,
)
from custom_components.ha_logger_ext.diagnostics import (
    async_get_config_entry_diagnostics,
)

_TS = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)


def _make_entry(hass: HomeAssistant, options: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_DB_TYPE: DB_TYPE_SQLITE,
            CONF_DB_PATH: "ha_logger_ext/ha_logger_ext.db",
            CONF_EXCLUDE_DOMAINS: ["sun"],
            CONF_EXCLUDE_ENTITIES: [],
            CONF_EXCLUDE_ATTRIBUTES: ["icon"],
        },
        options=options or {},
    )
    entry.add_to_hass(hass)
    return entry


def _mock_backend() -> AsyncMock:
    backend = AsyncMock()
    backend.get_latest_observation.return_value = None
    backend.get_or_create_entity.return_value = uuid.uuid4()
    return backend


async def test_diagnostics_returns_config(hass: HomeAssistant) -> None:
    entry = _make_entry(hass)
    with patch(
        "custom_components.ha_logger_ext.create_backend",
        return_value=_mock_backend(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert diag["config"][CONF_DB_TYPE] == DB_TYPE_SQLITE
    assert diag["config"][CONF_DB_PATH] == "ha_logger_ext/ha_logger_ext.db"
    assert diag["config"][CONF_EXCLUDE_DOMAINS] == ["sun"]
    assert diag["config"][CONF_EXCLUDE_ATTRIBUTES] == ["icon"]


async def test_diagnostics_returns_default_options(hass: HomeAssistant) -> None:
    entry = _make_entry(hass)
    with patch(
        "custom_components.ha_logger_ext.create_backend",
        return_value=_mock_backend(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert diag["options"][CONF_FLUSH_INTERVAL] == DEFAULT_FLUSH_INTERVAL
    assert diag["options"][CONF_QUEUE_MAX_SIZE] == DEFAULT_QUEUE_MAX_SIZE


async def test_diagnostics_returns_saved_options(hass: HomeAssistant) -> None:
    entry = _make_entry(
        hass,
        options={CONF_FLUSH_INTERVAL: 60, CONF_QUEUE_MAX_SIZE: 5000},
    )
    with patch(
        "custom_components.ha_logger_ext.create_backend",
        return_value=_mock_backend(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert diag["options"][CONF_FLUSH_INTERVAL] == 60
    assert diag["options"][CONF_QUEUE_MAX_SIZE] == 5000


async def test_diagnostics_coordinator_running(hass: HomeAssistant) -> None:
    entry = _make_entry(hass)
    with patch(
        "custom_components.ha_logger_ext.create_backend",
        return_value=_mock_backend(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert diag["coordinator"]["is_running"] is True
    assert isinstance(diag["coordinator"]["queue_size"], int)
    assert diag["coordinator"]["flush_interval"] == DEFAULT_FLUSH_INTERVAL


async def test_diagnostics_last_flush_none_before_first_flush(
    hass: HomeAssistant,
) -> None:
    entry = _make_entry(hass)
    with patch(
        "custom_components.ha_logger_ext.create_backend",
        return_value=_mock_backend(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert diag["coordinator"]["last_flush"] is None


async def test_diagnostics_last_flush_set_after_flush(hass: HomeAssistant) -> None:
    entry = _make_entry(hass)
    with patch(
        "custom_components.ha_logger_ext.create_backend",
        return_value=_mock_backend(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = entry.runtime_data
        coordinator._last_flush = _TS

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert diag["coordinator"]["last_flush"] == _TS.isoformat()
