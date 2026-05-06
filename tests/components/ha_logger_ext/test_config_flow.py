from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.ha_logger_ext.const import (
    CONF_DB_PATH,
    CONF_DB_TYPE,
    DB_TYPE_SQLITE,
    DEFAULT_DB_PATH,
    DOMAIN,
)


async def test_user_step_shows_form(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}


async def test_creates_entry_with_sqlite_defaults(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_DB_TYPE: DB_TYPE_SQLITE, CONF_DB_PATH: DEFAULT_DB_PATH},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DB_TYPE] == DB_TYPE_SQLITE
    assert result["data"][CONF_DB_PATH] == DEFAULT_DB_PATH


async def test_creates_entry_with_custom_db_path(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_DB_TYPE: DB_TYPE_SQLITE, CONF_DB_PATH: "custom/path.db"},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DB_PATH] == "custom/path.db"
