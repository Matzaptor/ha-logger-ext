from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_DB_PATH,
    CONF_DB_TYPE,
    CONF_EXCLUDE_ATTRIBUTES,
    CONF_EXCLUDE_DOMAINS,
    CONF_EXCLUDE_ENTITIES,
    CONF_FLUSH_INTERVAL,
    CONF_QUEUE_MAX_SIZE,
    DB_TYPE_SQLITE,
    DEFAULT_DB_PATH,
    DEFAULT_FLUSH_INTERVAL,
    DEFAULT_QUEUE_MAX_SIZE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# MySQL and PostgreSQL will be unlocked in a future release.
_AVAILABLE_DB_TYPES = [DB_TYPE_SQLITE]


def _parse_csv(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


def _user_schema(
    db_type: str = DB_TYPE_SQLITE,
    db_path: str = DEFAULT_DB_PATH,
    exclude_domains: str = "",
    exclude_entities: str = "",
    exclude_attributes: str = "",
) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_DB_TYPE, default=db_type): vol.In(_AVAILABLE_DB_TYPES),
            vol.Optional(CONF_DB_PATH, default=db_path): str,
            vol.Optional(CONF_EXCLUDE_DOMAINS, default=exclude_domains): str,
            vol.Optional(CONF_EXCLUDE_ENTITIES, default=exclude_entities): str,
            vol.Optional(CONF_EXCLUDE_ATTRIBUTES, default=exclude_attributes): str,
        }
    )


def _build_data(user_input: dict) -> dict:
    return {
        CONF_DB_TYPE: user_input[CONF_DB_TYPE],
        CONF_DB_PATH: user_input.get(CONF_DB_PATH, DEFAULT_DB_PATH),
        CONF_EXCLUDE_DOMAINS: _parse_csv(user_input.get(CONF_EXCLUDE_DOMAINS, "")),
        CONF_EXCLUDE_ENTITIES: _parse_csv(user_input.get(CONF_EXCLUDE_ENTITIES, "")),
        CONF_EXCLUDE_ATTRIBUTES: _parse_csv(user_input.get(CONF_EXCLUDE_ATTRIBUTES, "")),
    }


class HaLoggerExtConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> HaLoggerExtOptionsFlow:
        return HaLoggerExtOptionsFlow()

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            return self.async_create_entry(
                title="HA Logger Extended",
                data=_build_data(user_input),
            )

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict | None = None
    ) -> FlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            return self.async_update_reload_and_abort(
                entry, data_updates=_build_data(user_input)
            )

        current = entry.data
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_user_schema(
                db_type=current.get(CONF_DB_TYPE, DB_TYPE_SQLITE),
                db_path=current.get(CONF_DB_PATH, DEFAULT_DB_PATH),
                exclude_domains=", ".join(current.get(CONF_EXCLUDE_DOMAINS, [])),
                exclude_entities=", ".join(current.get(CONF_EXCLUDE_ENTITIES, [])),
                exclude_attributes=", ".join(current.get(CONF_EXCLUDE_ATTRIBUTES, [])),
            ),
            errors=errors,
        )


class HaLoggerExtOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(
        self, user_input: dict | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_FLUSH_INTERVAL,
                        default=current.get(CONF_FLUSH_INTERVAL, DEFAULT_FLUSH_INTERVAL),
                    ): vol.All(int, vol.Range(min=5, max=300)),
                    vol.Optional(
                        CONF_QUEUE_MAX_SIZE,
                        default=current.get(CONF_QUEUE_MAX_SIZE, DEFAULT_QUEUE_MAX_SIZE),
                    ): vol.All(int, vol.Range(min=100, max=100_000)),
                }
            ),
        )
