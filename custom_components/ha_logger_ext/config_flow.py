from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_DB_PATH,
    CONF_DB_TYPE,
    DB_TYPE_SQLITE,
    DEFAULT_DB_PATH,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# MySQL and PostgreSQL will be unlocked in a future release.
_AVAILABLE_DB_TYPES = [DB_TYPE_SQLITE]


class HaLoggerExtConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            return self.async_create_entry(
                title="HA Logger Extended",
                data=user_input,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DB_TYPE, default=DB_TYPE_SQLITE): vol.In(
                        _AVAILABLE_DB_TYPES
                    ),
                    vol.Optional(CONF_DB_PATH, default=DEFAULT_DB_PATH): str,
                }
            ),
            errors=errors,
        )
