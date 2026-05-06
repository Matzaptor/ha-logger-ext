from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_DB_PATH,
    CONF_DB_TYPE,
    CONF_EXCLUDE_ATTRIBUTES,
    CONF_EXCLUDE_DOMAINS,
    CONF_EXCLUDE_ENTITIES,
    DB_TYPE_SQLITE,
    DEFAULT_DB_PATH,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# MySQL and PostgreSQL will be unlocked in a future release.
_AVAILABLE_DB_TYPES = [DB_TYPE_SQLITE]


def _parse_csv(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


class HaLoggerExtConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            data = {
                CONF_DB_TYPE: user_input[CONF_DB_TYPE],
                CONF_DB_PATH: user_input.get(CONF_DB_PATH, DEFAULT_DB_PATH),
                CONF_EXCLUDE_DOMAINS: _parse_csv(
                    user_input.get(CONF_EXCLUDE_DOMAINS, "")
                ),
                CONF_EXCLUDE_ENTITIES: _parse_csv(
                    user_input.get(CONF_EXCLUDE_ENTITIES, "")
                ),
                CONF_EXCLUDE_ATTRIBUTES: _parse_csv(
                    user_input.get(CONF_EXCLUDE_ATTRIBUTES, "")
                ),
            }
            return self.async_create_entry(title="HA Logger Extended", data=data)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DB_TYPE, default=DB_TYPE_SQLITE): vol.In(
                        _AVAILABLE_DB_TYPES
                    ),
                    vol.Optional(CONF_DB_PATH, default=DEFAULT_DB_PATH): str,
                    vol.Optional(CONF_EXCLUDE_DOMAINS, default=""): str,
                    vol.Optional(CONF_EXCLUDE_ENTITIES, default=""): str,
                    vol.Optional(CONF_EXCLUDE_ATTRIBUTES, default=""): str,
                }
            ),
            errors=errors,
        )
