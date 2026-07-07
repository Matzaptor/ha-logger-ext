from __future__ import annotations

from typing import ClassVar

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import RecorderCoordinator
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: RecorderCoordinator = entry.runtime_data
    async_add_entities(
        [
            RecordingActiveSensor(coordinator, entry),
            ImportProgressSensor(coordinator, entry),
        ]
    )


class _CoordinatorBinarySensor(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _unique_id_suffix: ClassVar[str]

    def __init__(self, coordinator: RecorderCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_{self._unique_id_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Matzaptor",
            model="HA External Recorder",
        )

    async def async_added_to_hass(self) -> None:
        self._coordinator.register_own_entity(self.entity_id)
        self.async_on_remove(
            self._coordinator.async_add_listener(self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class RecordingActiveSensor(_CoordinatorBinarySensor):
    """Binary sensor: True while the coordinator flush loop is running."""

    _unique_id_suffix = "recording"
    _attr_name = "Recording"
    _attr_icon = "mdi:database-clock"

    @property
    def is_on(self) -> bool:
        return self._coordinator.is_running

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {
            "queue_size": self._coordinator.queue_size,
            "last_flush": (
                self._coordinator.last_flush.isoformat()
                if self._coordinator.last_flush
                else None
            ),
            "flush_interval_seconds": self._coordinator.flush_interval,
        }


class ImportProgressSensor(_CoordinatorBinarySensor):
    """Binary sensor: True while an import_from_recorder task is running."""

    _unique_id_suffix = "import_in_progress"
    _attr_name = "Import in progress"
    _attr_icon = "mdi:database-import"

    @property
    def is_on(self) -> bool:
        return self._coordinator.is_importing

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        attrs: dict[str, object] = {}
        if self._coordinator.last_import_count is not None:
            attrs["last_import_intervals_inserted"] = self._coordinator.last_import_count
        return attrs
