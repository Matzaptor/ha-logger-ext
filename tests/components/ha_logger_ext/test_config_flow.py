from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
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
    DEFAULT_DB_PATH,
    DEFAULT_FLUSH_INTERVAL,
    DEFAULT_QUEUE_MAX_SIZE,
    DOMAIN,
)


# ---------------------------------------------------------------------------
# Config flow — initial setup
# ---------------------------------------------------------------------------

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


async def test_csv_fields_parsed_to_lists(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_DB_TYPE: DB_TYPE_SQLITE,
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
# Options flow
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
    """Filters set at setup time appear pre-filled in the options form."""
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
    """Options-level filter takes precedence over setup-time data filter."""
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
            CONF_EXCLUDE_DOMAINS: ["automation"],  # overrides data
            CONF_EXCLUDE_ENTITIES: [],
            CONF_EXCLUDE_ATTRIBUTES: [],
        },
    )
    entry.add_to_hass(hass)

    with patch("custom_components.ha_logger_ext.create_backend") as mock_factory:
        from unittest.mock import AsyncMock
        import uuid
        backend = AsyncMock()
        backend.get_latest_observation.return_value = None
        backend.get_or_create_entity.return_value = uuid.uuid4()
        mock_factory.return_value = backend

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = entry.runtime_data
    # options says exclude automation, not sun
    assert not coordinator._should_track_entity("automation.test")
    assert coordinator._should_track_entity("sun.sun")


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
async def test_reconfigure_updates_and_reloads(hass: HomeAssistant) -> None:
    entry = _entry_with_options(hass)

    with patch(
        "custom_components.ha_logger_ext.create_backend"
    ) as mock_factory:
        backend = AsyncMock()
        backend.get_latest_observation.return_value = None
        backend.get_or_create_entity.return_value = __import__("uuid").uuid4()
        mock_factory.return_value = backend

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": _SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_DB_TYPE: DB_TYPE_SQLITE,
                CONF_DB_PATH: "new_path.db",
                CONF_EXCLUDE_DOMAINS: "sun",
            },
        )
        # Wait for the reload triggered by async_update_reload_and_abort to
        # complete while the mock is still active. Without this, the real
        # SQLiteBackend would be created in the reload, leaving an aiosqlite
        # worker thread alive and causing HA's cleanup fixture to fail.
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_DB_PATH] == "new_path.db"
    assert entry.data[CONF_EXCLUDE_DOMAINS] == ["sun"]


# ---------------------------------------------------------------------------
# Config flow — DB validation (exception-translations)
# ---------------------------------------------------------------------------

async def test_user_step_shows_error_on_db_failure(hass: HomeAssistant) -> None:
    """cannot_connect error is shown when the backend fails to initialise."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(
        "custom_components.ha_logger_ext.config_flow._test_backend",
        return_value="cannot_connect",
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_DB_TYPE: DB_TYPE_SQLITE, CONF_DB_PATH: DEFAULT_DB_PATH},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_step_creates_entry_after_db_success(hass: HomeAssistant) -> None:
    """Entry is created when backend initialises successfully."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(
        "custom_components.ha_logger_ext.config_flow._test_backend",
        return_value=None,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_DB_TYPE: DB_TYPE_SQLITE, CONF_DB_PATH: DEFAULT_DB_PATH},
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY


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
