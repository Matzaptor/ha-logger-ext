from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN, FLUSH_INTERVAL, QUEUE_MAX_SIZE
from .storage.base import ObservationRecord, StorageBackend
from .storage.factory import create_backend
from .storage.serialization import serialize, values_equal
from .storage.uuid7 import uuid7

_LOGGER = logging.getLogger(__name__)


@dataclass
class _StateSnapshot:
    entity_id: str
    state: str
    attributes: dict[str, Any]
    ts: datetime


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    backend = create_backend(entry.data, hass.config.config_dir)
    try:
        await backend.initialize()
    except Exception:
        _LOGGER.exception("Failed to initialize storage backend")
        return False

    coordinator = LoggerCoordinator(hass, backend)
    await coordinator.start()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator: LoggerCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
    await coordinator.stop()
    return True


class LoggerCoordinator:
    def __init__(self, hass: HomeAssistant, backend: StorageBackend) -> None:
        self._hass = hass
        self._backend = backend
        self._queue: asyncio.Queue[_StateSnapshot] = asyncio.Queue(maxsize=QUEUE_MAX_SIZE)
        self._flush_task: asyncio.Task | None = None
        self._unsub: Any = None

    async def start(self) -> None:
        for state in self._hass.states.async_all():
            snap = _StateSnapshot(
                entity_id=state.entity_id,
                state=state.state,
                attributes=dict(state.attributes),
                ts=state.last_updated,
            )
            try:
                self._queue.put_nowait(snap)
            except asyncio.QueueFull:
                _LOGGER.warning(
                    "Queue full during initial snapshot, skipping %s", state.entity_id
                )

        self._unsub = self._hass.bus.async_listen(
            EVENT_STATE_CHANGED, self._on_state_changed
        )
        self._flush_task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        if self._flush_task is not None:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None
        await self._flush()
        await self._backend.close()

    @callback
    def _on_state_changed(self, event: Event) -> None:
        new_state = event.data.get("new_state")
        if new_state is None:
            return
        snap = _StateSnapshot(
            entity_id=new_state.entity_id,
            state=new_state.state,
            attributes=dict(new_state.attributes),
            ts=new_state.last_updated,
        )
        try:
            self._queue.put_nowait(snap)
        except asyncio.QueueFull:
            _LOGGER.warning(
                "Observation queue full, dropping state change for %s",
                new_state.entity_id,
            )

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_INTERVAL)
            await self._flush()

    async def _flush(self) -> None:
        if self._queue.empty():
            return
        snapshots: list[_StateSnapshot] = []
        while not self._queue.empty():
            try:
                snapshots.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        for snap in snapshots:
            try:
                await self._process_snapshot(snap)
            except Exception:
                _LOGGER.exception("Failed to process observation for %s", snap.entity_id)

    async def _process_snapshot(self, snap: _StateSnapshot) -> None:
        domain = snap.entity_id.split(".")[0]
        entity_pk = await self._backend.get_or_create_entity(
            snap.entity_id, domain, snap.ts
        )
        await self._record_field(entity_pk, "state", snap.state, snap.ts)
        for attr_name, attr_value in snap.attributes.items():
            await self._record_field(entity_pk, attr_name, attr_value, snap.ts)

    async def _record_field(
        self,
        entity_pk: Any,
        field_name: str,
        raw_value: Any,
        ts: datetime,
    ) -> None:
        serialized = serialize(raw_value)
        latest = await self._backend.get_latest_observation(entity_pk, field_name)

        if latest is not None:
            latest_dict: dict[str, Any] = {
                "value_type": latest.value_type,
                "value_str": latest.value_str,
                "value_int": latest.value_int,
                "value_float": latest.value_float,
                "value_bool": latest.value_bool,
                "value_datetime": latest.value_datetime,
                "value_date": latest.value_date,
                "value_time": latest.value_time,
                "value_json": latest.value_json,
            }
            if values_equal(latest_dict, serialized):
                await self._backend.update_last_seen(latest.id, ts)
                return

        obs = ObservationRecord(
            id=uuid7(),
            entity_pk=entity_pk,
            field_name=field_name,
            value_type=serialized["value_type"],
            value_str=serialized.get("value_str"),
            value_int=serialized.get("value_int"),
            value_float=serialized.get("value_float"),
            value_bool=serialized.get("value_bool"),
            value_datetime=serialized.get("value_datetime"),
            value_date=serialized.get("value_date"),
            value_time=serialized.get("value_time"),
            value_json=serialized.get("value_json"),
            first_seen=ts,
            last_seen=ts,
        )
        await self._backend.insert_observation(obs)
