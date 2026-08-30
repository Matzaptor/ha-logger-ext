from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_DB_HOST,
    CONF_DB_NAME,
    CONF_DB_PASSWORD,
    CONF_DB_PATH,
    CONF_DB_PORT,
    CONF_DB_TYPE,
    CONF_DB_USERNAME,
    CONF_EXCLUDE_ATTRIBUTES,
    CONF_EXCLUDE_DOMAINS,
    CONF_EXCLUDE_ENTITIES,
    CONF_FLUSH_INTERVAL,
    CONF_QUEUE_MAX_SIZE,
    DB_TYPE_DUCKDB,
    DB_TYPE_EMBEDDED,
    DB_TYPE_MYSQL,
    DB_TYPE_POSTGRESQL,
    DB_TYPE_SQLITE,
    DEFAULT_DB_HOST,
    DEFAULT_DB_NAME,
    DEFAULT_DB_PATH,
    DEFAULT_DB_TYPE,
    DEFAULT_DUCKDB_PATH,
    DEFAULT_FLUSH_INTERVAL,
    DEFAULT_MYSQL_PORT,
    DEFAULT_POSTGRESQL_PORT,
    DEFAULT_QUEUE_MAX_SIZE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

_ALL_DB_TYPES = [DB_TYPE_SQLITE, DB_TYPE_MYSQL, DB_TYPE_POSTGRESQL]

_DEFAULT_PORT: dict[str, int] = {
    DB_TYPE_MYSQL: DEFAULT_MYSQL_PORT,
    DB_TYPE_POSTGRESQL: DEFAULT_POSTGRESQL_PORT,
}


async def _test_backend(hass: HomeAssistant, data: dict) -> str | None:
    """Try to initialise the backend; return an error key or None on success."""
    from .storage.factory import create_backend
    backend = create_backend(data, hass.config.config_dir)
    try:
        await backend.initialize()
        await backend.close()
    except Exception:
        _LOGGER.exception("Database initialisation failed during config flow validation")
        return "cannot_connect"
    return None


def _default_path(db_type: str) -> str:
    return DEFAULT_DUCKDB_PATH if db_type == DB_TYPE_DUCKDB else DEFAULT_DB_PATH


def _build_embedded_data(db_type: str, user_input: dict) -> dict:
    return {
        CONF_DB_TYPE: db_type,
        CONF_DB_PATH: user_input.get(CONF_DB_PATH, _default_path(db_type)),
        CONF_EXCLUDE_DOMAINS: user_input.get(CONF_EXCLUDE_DOMAINS, []),
        CONF_EXCLUDE_ENTITIES: user_input.get(CONF_EXCLUDE_ENTITIES, []),
        CONF_EXCLUDE_ATTRIBUTES: user_input.get(CONF_EXCLUDE_ATTRIBUTES, []),
    }


def _build_server_data(db_type: str, user_input: dict) -> dict:
    return {
        CONF_DB_TYPE: db_type,
        CONF_DB_HOST: user_input[CONF_DB_HOST],
        CONF_DB_PORT: user_input[CONF_DB_PORT],
        CONF_DB_NAME: user_input[CONF_DB_NAME],
        CONF_DB_USERNAME: user_input[CONF_DB_USERNAME],
        CONF_DB_PASSWORD: user_input[CONF_DB_PASSWORD],
        CONF_EXCLUDE_DOMAINS: user_input.get(CONF_EXCLUDE_DOMAINS, []),
        CONF_EXCLUDE_ENTITIES: user_input.get(CONF_EXCLUDE_ENTITIES, []),
        CONF_EXCLUDE_ATTRIBUTES: user_input.get(CONF_EXCLUDE_ATTRIBUTES, []),
    }


def _embedded_schema(
    db_path: str = DEFAULT_DB_PATH,
    exclude_domains: list[str] | None = None,
    exclude_entities: list[str] | None = None,
    exclude_attributes: list[str] | None = None,
) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(CONF_DB_PATH, default=db_path): str,
            vol.Optional(
                CONF_EXCLUDE_DOMAINS, default=exclude_domains or []
            ): TextSelector(TextSelectorConfig(multiple=True)),
            vol.Optional(
                CONF_EXCLUDE_ENTITIES, default=exclude_entities or []
            ): EntitySelector(EntitySelectorConfig(multiple=True)),
            vol.Optional(
                CONF_EXCLUDE_ATTRIBUTES, default=exclude_attributes or []
            ): TextSelector(TextSelectorConfig(multiple=True)),
        }
    )


def _server_schema(
    db_type: str = DB_TYPE_MYSQL,
    db_host: str = DEFAULT_DB_HOST,
    db_port: int | None = None,
    db_name: str = DEFAULT_DB_NAME,
    db_username: str = "",
    exclude_domains: list[str] | None = None,
    exclude_entities: list[str] | None = None,
    exclude_attributes: list[str] | None = None,
    password_required: bool = True,
) -> vol.Schema:
    port = db_port if db_port is not None else _DEFAULT_PORT.get(db_type, DEFAULT_MYSQL_PORT)
    # Never prefilled, even on reconfigure: only the marker (Required vs
    # Optional) changes, so the stored secret is never echoed back into the
    # browser. See async_step_reconfigure for how an empty submission falls
    # back to the currently stored password instead of overwriting it.
    password_marker = vol.Required if password_required else vol.Optional
    return vol.Schema(
        {
            vol.Required(CONF_DB_HOST, default=db_host): str,
            vol.Required(CONF_DB_PORT, default=port): vol.All(int, vol.Range(min=1, max=65535)),
            vol.Required(CONF_DB_NAME, default=db_name): str,
            vol.Required(CONF_DB_USERNAME, default=db_username): str,
            password_marker(CONF_DB_PASSWORD, default=""): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
            vol.Optional(
                CONF_EXCLUDE_DOMAINS, default=exclude_domains or []
            ): TextSelector(TextSelectorConfig(multiple=True)),
            vol.Optional(
                CONF_EXCLUDE_ENTITIES, default=exclude_entities or []
            ): EntitySelector(EntitySelectorConfig(multiple=True)),
            vol.Optional(
                CONF_EXCLUDE_ATTRIBUTES, default=exclude_attributes or []
            ): TextSelector(TextSelectorConfig(multiple=True)),
        }
    )


class HaRecorderExtConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._db_type: str = DEFAULT_DB_TYPE

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> HaRecorderExtOptionsFlow:
        return HaRecorderExtOptionsFlow(config_entry)

    # ------------------------------------------------------------------
    # Step 1: choose DB type
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> FlowResult:
        if user_input is not None:
            self._db_type = user_input[CONF_DB_TYPE]
            if self._db_type in DB_TYPE_EMBEDDED:
                return await self.async_step_embedded()
            return await self.async_step_server()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_DB_TYPE, default=DEFAULT_DB_TYPE): vol.In(_ALL_DB_TYPES)}
            ),
            errors={},
        )

    # ------------------------------------------------------------------
    # Step 2a: embedded (SQLite / DuckDB)
    # ------------------------------------------------------------------

    async def async_step_embedded(
        self, user_input: dict | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            data = _build_embedded_data(self._db_type, user_input)
            error = await _test_backend(self.hass, data)
            if error:
                errors["base"] = error
            else:
                return self.async_create_entry(title="HA External Recorder", data=data)

        return self.async_show_form(
            step_id="embedded",
            data_schema=_embedded_schema(db_path=_default_path(self._db_type)),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 2b: server (MySQL / PostgreSQL)
    # ------------------------------------------------------------------

    async def async_step_server(
        self, user_input: dict | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            data = _build_server_data(self._db_type, user_input)
            error = await _test_backend(self.hass, data)
            if error:
                errors["base"] = error
            else:
                return self.async_create_entry(title="HA External Recorder", data=data)

        return self.async_show_form(
            step_id="server",
            data_schema=_server_schema(db_type=self._db_type),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Reconfigure
    # ------------------------------------------------------------------

    async def async_step_reconfigure(
        self, user_input: dict | None = None
    ) -> FlowResult:
        entry = self._get_reconfigure_entry()
        self._db_type = entry.data.get(CONF_DB_TYPE, DEFAULT_DB_TYPE)
        errors: dict[str, str] = {}

        if user_input is not None:
            if self._db_type in DB_TYPE_EMBEDDED:
                data = _build_embedded_data(self._db_type, user_input)
            else:
                if not user_input.get(CONF_DB_PASSWORD):
                    # Left blank: keep the password already on file instead of
                    # overwriting it with an empty string. The field is never
                    # prefilled with the stored value (see _server_schema), so
                    # this is the only way to "not change" it on reconfigure.
                    user_input = {
                        **user_input,
                        CONF_DB_PASSWORD: entry.data.get(CONF_DB_PASSWORD, ""),
                    }
                data = _build_server_data(self._db_type, user_input)
            error = await _test_backend(self.hass, data)
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(entry, data_updates=data)

        current = entry.data
        if self._db_type in DB_TYPE_EMBEDDED:
            schema = _embedded_schema(
                db_path=current.get(CONF_DB_PATH, _default_path(self._db_type)),
                exclude_domains=current.get(CONF_EXCLUDE_DOMAINS, []),
                exclude_entities=current.get(CONF_EXCLUDE_ENTITIES, []),
                exclude_attributes=current.get(CONF_EXCLUDE_ATTRIBUTES, []),
            )
        else:
            schema = _server_schema(
                db_type=self._db_type,
                db_host=current.get(CONF_DB_HOST, DEFAULT_DB_HOST),
                db_port=current.get(CONF_DB_PORT),
                db_name=current.get(CONF_DB_NAME, DEFAULT_DB_NAME),
                db_username=current.get(CONF_DB_USERNAME, ""),
                exclude_domains=current.get(CONF_EXCLUDE_DOMAINS, []),
                exclude_entities=current.get(CONF_EXCLUDE_ENTITIES, []),
                exclude_attributes=current.get(CONF_EXCLUDE_ATTRIBUTES, []),
                password_required=False,
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
        )


class HaRecorderExtOptionsFlow(config_entries.OptionsFlowWithReload):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> FlowResult:
        if user_input is not None:
            new_options: dict[str, Any] = {
                CONF_FLUSH_INTERVAL: user_input[CONF_FLUSH_INTERVAL],
                CONF_QUEUE_MAX_SIZE: user_input[CONF_QUEUE_MAX_SIZE],
            }
            # The form promises "leave empty to keep the current setting" for
            # each exclude field. An empty list is still a present key, so
            # writing it unconditionally would make it win over the
            # setup-time value in _effective() below instead of falling back
            # to it — silently disabling a filter the user never asked to
            # clear. Omitting the key when the submitted value is empty is
            # what actually keeps that promise.
            for key in (
                CONF_EXCLUDE_DOMAINS,
                CONF_EXCLUDE_ENTITIES,
                CONF_EXCLUDE_ATTRIBUTES,
            ):
                value = user_input.get(key)
                if value:
                    new_options[key] = value
            return self.async_create_entry(data=new_options)

        opts = self._config_entry.options
        data = self._config_entry.data

        def _effective(key: str) -> list[str]:
            return opts.get(key, data.get(key, []))

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_FLUSH_INTERVAL,
                        default=opts.get(CONF_FLUSH_INTERVAL, DEFAULT_FLUSH_INTERVAL),
                    ): vol.All(int, vol.Range(min=5, max=300)),
                    vol.Optional(
                        CONF_QUEUE_MAX_SIZE,
                        default=opts.get(CONF_QUEUE_MAX_SIZE, DEFAULT_QUEUE_MAX_SIZE),
                    ): vol.All(int, vol.Range(min=100, max=100_000)),
                    vol.Optional(
                        CONF_EXCLUDE_DOMAINS,
                        default=_effective(CONF_EXCLUDE_DOMAINS),
                    ): TextSelector(TextSelectorConfig(multiple=True)),
                    vol.Optional(
                        CONF_EXCLUDE_ENTITIES,
                        default=_effective(CONF_EXCLUDE_ENTITIES),
                    ): EntitySelector(EntitySelectorConfig(multiple=True)),
                    vol.Optional(
                        CONF_EXCLUDE_ATTRIBUTES,
                        default=_effective(CONF_EXCLUDE_ATTRIBUTES),
                    ): TextSelector(TextSelectorConfig(multiple=True)),
                }
            ),
        )
