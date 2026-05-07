from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_EXCLUDE_ATTRIBUTES,
    CONF_EXCLUDE_DOMAINS,
    CONF_EXCLUDE_ENTITIES,
    CONF_FLUSH_INTERVAL,
    CONF_QUEUE_MAX_SIZE,
    DEFAULT_FLUSH_INTERVAL,
    DEFAULT_QUEUE_MAX_SIZE,
    DOMAIN,
)
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
    except Exception as err:
        raise ConfigEntryNotReady(f"Failed to initialize storage backend: {err}") from err

    coordinator = LoggerCoordinator(hass, backend, entry.data, entry.options)
    await coordinator.start()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options_change))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator: LoggerCoordinator = entry.runtime_data
    await coordinator.stop()
    return True


async def _async_reload_on_options_change(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


class LoggerCoordinator:
    def __init__(
        self,
        hass: HomeAssistant,
        backend: StorageBackend,
        data: dict[str, Any],
        options: dict[str, Any] | None = None,
    ) -> None:
        self._hass = hass
        self._backend = backend
        self._stopped = False

        opts = options or {}
        self._flush_interval: int = opts.get(CONF_FLUSH_INTERVAL, DEFAULT_FLUSH_INTERVAL)
        queue_max: int = opts.get(CONF_QUEUE_MAX_SIZE, DEFAULT_QUEUE_MAX_SIZE)

        self._queue: asyncio.Queue[_StateSnapshot] = asyncio.Queue(maxsize=queue_max)
        self._flush_task: asyncio.Task | None = None
        self._unsub_states: Any = None
        self._unsub_stop: Any = None
        self._last_flush: datetime | None = None

        # Options take precedence over data so filters can be updated via
        # the options flow without re-adding the integration.
        self._exclude_domains: frozenset[str] = frozenset(
            opts.get(CONF_EXCLUDE_DOMAINS, data.get(CONF_EXCLUDE_DOMAINS, []))
        )
        self._exclude_entities: frozenset[str] = frozenset(
            opts.get(CONF_EXCLUDE_ENTITIES, data.get(CONF_EXCLUDE_ENTITIES, []))
        )
        self._exclude_attributes: frozenset[str] = frozenset(
            opts.get(CONF_EXCLUDE_ATTRIBUTES, data.get(CONF_EXCLUDE_ATTRIBUTES, []))
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    @property
    def is_running(self) -> bool:
        return not self._stopped

    @property
    def last_flush(self) -> datetime | None:
        return self._last_flush

    @property
    def flush_interval(self) -> int:
        return self._flush_interval

    def _should_track_entity(self, entity_id: str) -> bool:
        domain = entity_id.split(".")[0]
        return (
            domain not in self._exclude_domains
            and entity_id not in self._exclude_entities
        )

    def _should_track_attribute(self, attr_name: str) -> bool:
        return attr_name not in self._exclude_attributes

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        for state in self._hass.states.async_all():
            if not self._should_track_entity(state.entity_id):
                continue
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

        self._unsub_states = self._hass.bus.async_listen(
            EVENT_STATE_CHANGED, self._on_state_changed
        )
        self._unsub_stop = self._hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, self._on_ha_stop
        )
        self._flush_task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True

        if self._unsub_states is not None:
            self._unsub_states()
            self._unsub_states = None
        if self._unsub_stop is not None:
            self._unsub_stop()
            self._unsub_stop = None
        if self._flush_task is not None:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None

        await self._flush()
        await self._backend.close()

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    @callback
    def _on_state_changed(self, event: Event) -> None:
        new_state = event.data.get("new_state")
        if new_state is None:
            return
        if not self._should_track_entity(new_state.entity_id):
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

    @callback
    def _on_ha_stop(self, event: Event) -> None:
        # The one-time listener has already removed itself; clear our reference
        # so stop() does not try to cancel it again.
        self._unsub_stop = None
        self._hass.async_create_task(self._flush())

    # ------------------------------------------------------------------
    # Flush loop
    # ------------------------------------------------------------------

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self._flush_interval)
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

        await self._backend.begin()
        failed = 0
        for snap in snapshots:
            try:
                await self._process_snapshot(snap)
            except Exception:
                failed += 1
                _LOGGER.exception("Failed to process snapshot for %s", snap.entity_id)
        try:
            await self._backend.commit()
            self._last_flush = datetime.now(timezone.utc)
        except Exception:
            _LOGGER.exception("Flush commit failed, attempting rollback")
            await self._backend.rollback()

        if failed:
            _LOGGER.warning(
                "Flush completed with %d failed snapshot(s) out of %d",
                failed,
                len(snapshots),
            )

    async def _process_snapshot(self, snap: _StateSnapshot) -> None:
        domain = snap.entity_id.split(".")[0]
        entity_pk = await self._backend.get_or_create_entity(
            snap.entity_id, domain, snap.ts
        )
        await self._record_field(entity_pk, "state", snap.state, snap.ts)
        for attr_name, attr_value in snap.attributes.items():
            if self._should_track_attribute(attr_name):
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
