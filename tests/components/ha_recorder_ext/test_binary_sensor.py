from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_recorder_ext import RecorderCoordinator
from custom_components.ha_recorder_ext.const import (
    CONF_DB_PATH,
    CONF_DB_TYPE,
    DB_TYPE_SQLITE,
    DOMAIN,
)

_ENTITY_RECORDING = "binary_sensor.ha_external_recorder_recording"
_ENTITY_IMPORT = "binary_sensor.ha_external_recorder_import_in_progress"


def _make_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="HA External Recorder",
        data={
            CONF_DB_TYPE: DB_TYPE_SQLITE,
            CONF_DB_PATH: "ha_recorder_ext/ha_recorder_ext.db",
        },
    )
    entry.add_to_hass(hass)
    return entry


def _mock_backend() -> AsyncMock:
    backend = AsyncMock()
    backend.get_latest_observation.return_value = None
    backend.get_or_create_entity.return_value = uuid.uuid4()
    backend.has_observations_in_range.return_value = False
    return backend


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    with patch(
        "custom_components.ha_recorder_ext.create_backend",
        return_value=_mock_backend(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


class TestRecordingActiveSensor:
    async def test_entity_created(self, hass: HomeAssistant) -> None:
        entry = _make_entry(hass)
        await _setup(hass, entry)
        assert hass.states.get(_ENTITY_RECORDING) is not None

    async def test_on_while_coordinator_running(self, hass: HomeAssistant) -> None:
        entry = _make_entry(hass)
        await _setup(hass, entry)
        assert hass.states.get(_ENTITY_RECORDING).state == "on"

    async def test_has_queue_size_attribute(self, hass: HomeAssistant) -> None:
        entry = _make_entry(hass)
        await _setup(hass, entry)
        assert "queue_size" in hass.states.get(_ENTITY_RECORDING).attributes

    async def test_has_flush_interval_attribute(self, hass: HomeAssistant) -> None:
        entry = _make_entry(hass)
        await _setup(hass, entry)
        assert "flush_interval_seconds" in hass.states.get(_ENTITY_RECORDING).attributes

    async def test_turns_off_when_coordinator_stops(self, hass: HomeAssistant) -> None:
        entry = _make_entry(hass)
        await _setup(hass, entry)

        coordinator: RecorderCoordinator = entry.runtime_data
        # stop() sets _stopped, notifies listeners, and cancels the flush task
        await coordinator.stop()
        await hass.async_block_till_done()

        assert hass.states.get(_ENTITY_RECORDING).state == "off"


class TestImportProgressSensor:
    async def test_entity_created(self, hass: HomeAssistant) -> None:
        entry = _make_entry(hass)
        await _setup(hass, entry)
        assert hass.states.get(_ENTITY_IMPORT) is not None

    async def test_off_initially(self, hass: HomeAssistant) -> None:
        entry = _make_entry(hass)
        await _setup(hass, entry)
        assert hass.states.get(_ENTITY_IMPORT).state == "off"

    async def test_turns_on_when_import_starts(self, hass: HomeAssistant) -> None:
        entry = _make_entry(hass)
        await _setup(hass, entry)

        coordinator: RecorderCoordinator = entry.runtime_data
        coordinator.async_set_importing(True)
        await hass.async_block_till_done()

        assert hass.states.get(_ENTITY_IMPORT).state == "on"

    async def test_turns_off_when_import_ends(self, hass: HomeAssistant) -> None:
        entry = _make_entry(hass)
        await _setup(hass, entry)

        coordinator: RecorderCoordinator = entry.runtime_data
        coordinator.async_set_importing(True)
        coordinator.async_set_importing(False, count=42)
        await hass.async_block_till_done()

        assert hass.states.get(_ENTITY_IMPORT).state == "off"

    async def test_last_import_count_in_attributes(self, hass: HomeAssistant) -> None:
        entry = _make_entry(hass)
        await _setup(hass, entry)

        coordinator: RecorderCoordinator = entry.runtime_data
        coordinator.async_set_importing(False, count=42)
        await hass.async_block_till_done()

        attrs = hass.states.get(_ENTITY_IMPORT).attributes
        assert attrs["last_import_intervals_inserted"] == 42

    async def test_no_count_attribute_before_first_import(
        self, hass: HomeAssistant
    ) -> None:
        entry = _make_entry(hass)
        await _setup(hass, entry)

        attrs = hass.states.get(_ENTITY_IMPORT).attributes
        assert "last_import_intervals_inserted" not in attrs
