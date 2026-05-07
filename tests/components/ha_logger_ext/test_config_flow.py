from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_logger_ext.const import (
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
    DB_TYPE_MYSQL,
    DB_TYPE_POSTGRESQL,
    DB_TYPE_SQLITE,
    DEFAULT_DB_PATH,
    DEFAULT_DUCKDB_PATH,
    DEFAULT_FLUSH_INTERVAL,
    DEFAULT_MYSQL_PORT,
    DEFAULT_POSTGRESQL_PORT,
    DEFAULT_QUEUE_MAX_SIZE,
    DOMAIN,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entry_with_options(hass: HomeAssistant, options: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_DB_TYPE: DB_TYPE_SQLITE,
            CONF_DB_PATH: DEFAULT_DB_PATH,
            CONF_EXCLUDE_DOMAINS: [],
            CONF_EXCLUDE_ENTITIES: [],
            CONF_EXCLUDE_ATTRIBUTES: [],
        },
        options=options or {},
    )
    entry.add_to_hass(hass)
    return entry


# ---------------------------------------------------------------------------
# Config flow — step 1 (user: DB type selection)
# ---------------------------------------------------------------------------

async def test_user_step_shows_form(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}


async def test_sqlite_step_user_routes_to_embedded(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_DB_TYPE: DB_TYPE_SQLITE},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "embedded"


async def test_duckdb_step_user_routes_to_embedded(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_DB_TYPE: DB_TYPE_DUCKDB},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "embedded"


async def test_mysql_step_user_routes_to_server(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_DB_TYPE: DB_TYPE_MYSQL},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "server"


async def test_postgresql_step_user_routes_to_server(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_DB_TYPE: DB_TYPE_POSTGRESQL},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "server"


# ---------------------------------------------------------------------------
# Config flow — SQLite (embedded) full flow
# ---------------------------------------------------------------------------

async def test_creates_entry_with_sqlite_defaults(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_DB_TYPE: DB_TYPE_SQLITE},
    )
    with patch(
        "custom_components.ha_logger_ext.config_flow._test_backend",
        new=AsyncMock(return_value=None),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_DB_PATH: DEFAULT_DB_PATH},
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
        user_input={CONF_DB_TYPE: DB_TYPE_SQLITE},
    )
    with patch(
        "custom_components.ha_logger_ext.config_flow._test_backend",
        new=AsyncMock(return_value=None),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_DB_PATH: "custom/path.db"},
        )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DB_PATH] == "custom/path.db"


async def test_csv_fields_parsed_to_lists(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_DB_TYPE: DB_TYPE_SQLITE},
    )
    with patch(
        "custom_components.ha_logger_ext.config_flow._test_backend",
        new=AsyncMock(return_value=None),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_DB_PATH: DEFAULT_DB_PATH,
                CONF_EXCLUDE_DOMAINS: "automation, sun",
                CONF_EXCLUDE_ENTITIES: "sensor.noisy",
                CONF_EXCLUDE_ATTRIBUTES: "icon, entity_picture",
            },
        )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_EXCLUDE_DOMAINS] == ["automation", "sun"]
    assert result["data"][CONF_EXCLUDE_ENTITIES] == ["sensor.noisy"]
    assert result["data"][CONF_EXCLUDE_ATTRIBUTES] == ["icon", "entity_picture"]


# ---------------------------------------------------------------------------
# Config flow — DuckDB (embedded) full flow
# ---------------------------------------------------------------------------

async def test_duckdb_creates_entry_with_default_path(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_DB_TYPE: DB_TYPE_DUCKDB},
    )
    with patch(
        "custom_components.ha_logger_ext.config_flow._test_backend",
        new=AsyncMock(return_value=None),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_DB_PATH: DEFAULT_DUCKDB_PATH},
        )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DB_TYPE] == DB_TYPE_DUCKDB
    assert result["data"][CONF_DB_PATH] == DEFAULT_DUCKDB_PATH


# ---------------------------------------------------------------------------
# Config flow — MySQL (server) full flow
# ---------------------------------------------------------------------------

async def test_mysql_creates_entry_with_server_params(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_DB_TYPE: DB_TYPE_MYSQL},
    )
    with patch(
        "custom_components.ha_logger_ext.config_flow._test_backend",
        new=AsyncMock(return_value=None),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_DB_HOST: "db.local",
                CONF_DB_PORT: DEFAULT_MYSQL_PORT,
                CONF_DB_NAME: "ha_logger",
                CONF_DB_USERNAME: "ha_user",
                CONF_DB_PASSWORD: "secret",
            },
        )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DB_TYPE] == DB_TYPE_MYSQL
    assert result["data"][CONF_DB_HOST] == "db.local"
    assert result["data"][CONF_DB_PORT] == DEFAULT_MYSQL_PORT
    assert result["data"][CONF_DB_NAME] == "ha_logger"
    assert result["data"][CONF_DB_USERNAME] == "ha_user"
    assert result["data"][CONF_DB_PASSWORD] == "secret"


async def test_server_step_shows_error_on_db_failure(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_DB_TYPE: DB_TYPE_MYSQL},
    )
    with patch(
        "custom_components.ha_logger_ext.config_flow._test_backend",
        new=AsyncMock(return_value="cannot_connect"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_DB_HOST: "bad.host",
                CONF_DB_PORT: DEFAULT_MYSQL_PORT,
                CONF_DB_NAME: "ha_logger",
                CONF_DB_USERNAME: "ha_user",
                CONF_DB_PASSWORD: "secret",
            },
        )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "server"
    assert result["errors"] == {"base": "cannot_connect"}


# ---------------------------------------------------------------------------
# Config flow — PostgreSQL (server) full flow
# ---------------------------------------------------------------------------

async def test_postgresql_creates_entry_with_server_params(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_DB_TYPE: DB_TYPE_POSTGRESQL},
    )
    with patch(
        "custom_components.ha_logger_ext.config_flow._test_backend",
        new=AsyncMock(return_value=None),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_DB_HOST: "pg.local",
                CONF_DB_PORT: DEFAULT_POSTGRESQL_PORT,
                CONF_DB_NAME: "ha_logger",
                CONF_DB_USERNAME: "pg_user",
                CONF_DB_PASSWORD: "pgpass",
            },
        )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DB_TYPE] == DB_TYPE_POSTGRESQL
    assert result["data"][CONF_DB_HOST] == "pg.local"
    assert result["data"][CONF_DB_PORT] == DEFAULT_POSTGRESQL_PORT
    assert result["data"][CONF_DB_NAME] == "ha_logger"
    assert result["data"][CONF_DB_USERNAME] == "pg_user"
    assert result["data"][CONF_DB_PASSWORD] == "pgpass"


# ---------------------------------------------------------------------------
# Config flow — DB validation (exception-translations)
# ---------------------------------------------------------------------------

async def test_user_step_shows_error_on_db_failure(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_DB_TYPE: DB_TYPE_SQLITE},
    )
    with patch(
        "custom_components.ha_logger_ext.config_flow._test_backend",
        new=AsyncMock(return_value="cannot_connect"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_DB_PATH: DEFAULT_DB_PATH},
        )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "embedded"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_step_creates_entry_after_db_success(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_DB_TYPE: DB_TYPE_SQLITE},
    )
    with patch(
        "custom_components.ha_logger_ext.config_flow._test_backend",
        new=AsyncMock(return_value=None),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_DB_PATH: DEFAULT_DB_PATH},
        )
    assert result["type"] == FlowResultType.CREATE_ENTRY


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------

async def test_options_flow_shows_form(hass: HomeAssistant) -> None:
    entry = _entry_with_options(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"


async def test_options_flow_shows_current_defaults(hass: HomeAssistant) -> None:
    entry = _entry_with_options(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"].schema
    keys = {k.schema: k.default() for k in schema}
    assert keys[CONF_FLUSH_INTERVAL] == DEFAULT_FLUSH_INTERVAL
    assert keys[CONF_QUEUE_MAX_SIZE] == DEFAULT_QUEUE_MAX_SIZE


async def test_options_flow_shows_saved_values(hass: HomeAssistant) -> None:
    entry = _entry_with_options(
        hass, options={CONF_FLUSH_INTERVAL: 60, CONF_QUEUE_MAX_SIZE: 5000}
    )
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"].schema
    keys = {k.schema: k.default() for k in schema}
    assert keys[CONF_FLUSH_INTERVAL] == 60
    assert keys[CONF_QUEUE_MAX_SIZE] == 5000


async def test_options_flow_saves_values(hass: HomeAssistant) -> None:
    entry = _entry_with_options(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_FLUSH_INTERVAL: 90, CONF_QUEUE_MAX_SIZE: 2000},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_FLUSH_INTERVAL] == 90
    assert entry.options[CONF_QUEUE_MAX_SIZE] == 2000


async def test_options_flow_saves_filter_as_list(hass: HomeAssistant) -> None:
    entry = _entry_with_options(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_FLUSH_INTERVAL: DEFAULT_FLUSH_INTERVAL,
            CONF_QUEUE_MAX_SIZE: DEFAULT_QUEUE_MAX_SIZE,
            CONF_EXCLUDE_DOMAINS: "automation, sun",
            CONF_EXCLUDE_ENTITIES: "",
            CONF_EXCLUDE_ATTRIBUTES: "icon",
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_EXCLUDE_DOMAINS] == ["automation", "sun"]
    assert entry.options[CONF_EXCLUDE_ENTITIES] == []
    assert entry.options[CONF_EXCLUDE_ATTRIBUTES] == ["icon"]


async def test_options_flow_prefills_filters_from_data(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_DB_TYPE: DB_TYPE_SQLITE,
            CONF_DB_PATH: DEFAULT_DB_PATH,
            CONF_EXCLUDE_DOMAINS: ["sun"],
            CONF_EXCLUDE_ENTITIES: [],
            CONF_EXCLUDE_ATTRIBUTES: ["icon"],
        },
        options={},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"].schema
    defaults = {k.schema: k.default() for k in schema}
    assert "sun" in defaults[CONF_EXCLUDE_DOMAINS]
    assert "icon" in defaults[CONF_EXCLUDE_ATTRIBUTES]


async def test_options_filter_overrides_data_filter(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_DB_TYPE: DB_TYPE_SQLITE,
            CONF_DB_PATH: DEFAULT_DB_PATH,
            CONF_EXCLUDE_DOMAINS: ["sun"],
            CONF_EXCLUDE_ENTITIES: [],
            CONF_EXCLUDE_ATTRIBUTES: [],
        },
        options={
            CONF_FLUSH_INTERVAL: DEFAULT_FLUSH_INTERVAL,
            CONF_QUEUE_MAX_SIZE: DEFAULT_QUEUE_MAX_SIZE,
            CONF_EXCLUDE_DOMAINS: ["automation"],
            CONF_EXCLUDE_ENTITIES: [],
            CONF_EXCLUDE_ATTRIBUTES: [],
        },
    )
    entry.add_to_hass(hass)

    with patch("custom_components.ha_logger_ext.create_backend") as mock_factory:
        backend = AsyncMock()
        backend.get_latest_observation.return_value = None
        backend.get_or_create_entity.return_value = uuid.uuid4()
        mock_factory.return_value = backend

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = entry.runtime_data
    assert not coordinator._should_track_entity("automation.test")
    assert coordinator._should_track_entity("sun.sun")


# ---------------------------------------------------------------------------
# Options flow — range validation
# ---------------------------------------------------------------------------

async def test_options_flow_rejects_flush_interval_below_minimum(
    hass: HomeAssistant,
) -> None:
    entry = _entry_with_options(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(Exception):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_FLUSH_INTERVAL: 1, CONF_QUEUE_MAX_SIZE: DEFAULT_QUEUE_MAX_SIZE},
        )


async def test_options_flow_rejects_queue_size_above_maximum(
    hass: HomeAssistant,
) -> None:
    entry = _entry_with_options(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(Exception):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_FLUSH_INTERVAL: DEFAULT_FLUSH_INTERVAL, CONF_QUEUE_MAX_SIZE: 999_999},
        )


# ---------------------------------------------------------------------------
# Reconfigure flow
# SOURCE_RECONFIGURE was introduced in HA 2024.x; skip gracefully on older
# test fixture versions.
# ---------------------------------------------------------------------------

_SOURCE_RECONFIGURE = getattr(config_entries, "SOURCE_RECONFIGURE", None)
_reconfigure_available = pytest.mark.skipif(
    _SOURCE_RECONFIGURE is None,
    reason="SOURCE_RECONFIGURE not available in this HA version",
)


@_reconfigure_available
async def test_reconfigure_shows_form(hass: HomeAssistant) -> None:
    entry = _entry_with_options(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": _SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"


@_reconfigure_available
async def test_reconfigure_prefills_current_values(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_DB_TYPE: DB_TYPE_SQLITE,
            CONF_DB_PATH: "mydata.db",
            CONF_EXCLUDE_DOMAINS: ["sun", "automation"],
            CONF_EXCLUDE_ENTITIES: [],
            CONF_EXCLUDE_ATTRIBUTES: ["icon"],
        },
        options={},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": _SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    schema = result["data_schema"].schema
    defaults = {k.schema: k.default() for k in schema}
    assert defaults[CONF_DB_PATH] == "mydata.db"
    assert "sun" in defaults[CONF_EXCLUDE_DOMAINS]
    assert "icon" in defaults[CONF_EXCLUDE_ATTRIBUTES]


@_reconfigure_available
async def test_reconfigure_embedded_updates_and_reloads(hass: HomeAssistant) -> None:
    entry = _entry_with_options(hass)

    with patch("custom_components.ha_logger_ext.create_backend") as mock_factory:
        backend = AsyncMock()
        backend.get_latest_observation.return_value = None
        backend.get_or_create_entity.return_value = uuid.uuid4()
        mock_factory.return_value = backend

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": _SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
        )
        with patch(
            "custom_components.ha_logger_ext.config_flow._test_backend",
            new=AsyncMock(return_value=None),
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={
                    CONF_DB_PATH: "new_path.db",
                    CONF_EXCLUDE_DOMAINS: "sun",
                },
            )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_DB_PATH] == "new_path.db"
    assert entry.data[CONF_EXCLUDE_DOMAINS] == ["sun"]


@_reconfigure_available
async def test_reconfigure_server_entry_prefills_host(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_DB_TYPE: DB_TYPE_MYSQL,
            CONF_DB_HOST: "db.local",
            CONF_DB_PORT: DEFAULT_MYSQL_PORT,
            CONF_DB_NAME: "ha_logger",
            CONF_DB_USERNAME: "ha_user",
            CONF_DB_PASSWORD: "secret",
            CONF_EXCLUDE_DOMAINS: [],
            CONF_EXCLUDE_ENTITIES: [],
            CONF_EXCLUDE_ATTRIBUTES: [],
        },
        options={},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": _SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    schema = result["data_schema"].schema
    defaults = {k.schema: k.default() for k in schema}
    assert defaults[CONF_DB_HOST] == "db.local"
    assert defaults[CONF_DB_PORT] == DEFAULT_MYSQL_PORT
