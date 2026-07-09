from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, ClassVar

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv

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
    DB_TYPE_MYSQL,
    DB_TYPE_POSTGRESQL,
    DB_TYPE_SQLITE,
    DEFAULT_FLUSH_INTERVAL,
    DEFAULT_QUEUE_MAX_SIZE,
    DOMAIN,
    SERVICE_IMPORT_FROM_EXTERNAL_DB,
    SERVICE_IMPORT_FROM_RECORDER,
)
from .storage.base import ObservationRecord, StorageBackend
from .storage.factory import create_backend
from .storage.serialization import serialize, values_equal
from .storage.uuid7 import uuid7

PLATFORMS: list[str] = ["binary_sensor"]

_SERVICE_IMPORT_SCHEMA = vol.Schema(
    {
        vol.Optional("start_date"): cv.string,
        vol.Optional("end_date"): cv.string,
        vol.Optional("entity_ids"): vol.All(cv.ensure_list, [cv.entity_id]),
        vol.Optional("exclude_entities"): vol.All(cv.ensure_list, [cv.entity_id]),
        vol.Optional("exclude_attributes"): vol.All(cv.ensure_list, [cv.string]),
    }
)

_SERVICE_IMPORT_EXTERNAL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DB_TYPE): vol.In([DB_TYPE_SQLITE, DB_TYPE_MYSQL, DB_TYPE_POSTGRESQL]),
        vol.Optional(CONF_DB_PATH): cv.string,
        vol.Optional(CONF_DB_HOST): cv.string,
        vol.Optional(CONF_DB_PORT): cv.port,
        vol.Optional(CONF_DB_NAME): cv.string,
        vol.Optional(CONF_DB_USERNAME): cv.string,
        vol.Optional(CONF_DB_PASSWORD): cv.string,
        vol.Optional("start_date"): cv.string,
        vol.Optional("end_date"): cv.string,
        vol.Optional("entity_ids"): vol.All(cv.ensure_list, [cv.entity_id]),
        vol.Optional("exclude_entities"): vol.All(cv.ensure_list, [cv.entity_id]),
        vol.Optional("exclude_attributes"): vol.All(cv.ensure_list, [cv.string]),
    }
)

_EXTERNAL_SERVER_REQUIRED_FIELDS = (
    CONF_DB_HOST,
    CONF_DB_PORT,
    CONF_DB_NAME,
    CONF_DB_USERNAME,
    CONF_DB_PASSWORD,
)

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

    coordinator = RecorderCoordinator(hass, backend, entry.data, entry.options)
    await coordinator.start()
    entry.runtime_data = coordinator

    async def _handle_import(call: ServiceCall) -> None:
        from .importer import RecorderImporter

        if coordinator.is_importing:
            raise ServiceValidationError(
                "An import is already in progress; wait for it to finish "
                "(see the Import in progress sensor) before starting another."
            )

        now = datetime.now(timezone.utc)
        raw_start: str | None = call.data.get("start_date")
        raw_end: str | None = call.data.get("end_date")
        entity_ids: list[str] | None = call.data.get("entity_ids")
        exclude_entities: list[str] | None = call.data.get("exclude_entities")
        exclude_attributes: list[str] | None = call.data.get("exclude_attributes")

        start_time: datetime | None = (
            datetime.fromisoformat(raw_start).replace(tzinfo=timezone.utc)
            if raw_start
            else None
        )
        end_time = (
            datetime.fromisoformat(raw_end).replace(tzinfo=timezone.utc)
            if raw_end
            else now
        )

        importer = RecorderImporter(
            hass,
            coordinator.backend,
            coordinator.exclude_domains,
            coordinator.exclude_entities,
            coordinator.exclude_attributes,
        )

        # Set synchronously (no `await` between the is_importing check above and
        # this call) so two rapid, back-to-back service calls can't both pass
        # the check before either one claims the lock.
        coordinator.async_set_importing(True)

        async def _run() -> None:
            try:
                count = await importer.run(
                    start_time,
                    end_time,
                    entity_ids or None,
                    exclude_entities,
                    exclude_attributes,
                )
                coordinator.async_set_importing(False, count)
            except Exception:
                _LOGGER.exception("import_from_recorder task failed")
                coordinator.async_set_importing(False)

        hass.async_create_task(_run())

    hass.services.async_register(
        DOMAIN,
        SERVICE_IMPORT_FROM_RECORDER,
        _handle_import,
        schema=_SERVICE_IMPORT_SCHEMA,
    )
    entry.async_on_unload(
        lambda: hass.services.async_remove(DOMAIN, SERVICE_IMPORT_FROM_RECORDER)
    )

    async def _handle_import_external(call: ServiceCall) -> None:
        from .external_recorder_reader import create_external_recorder_reader
        from .importer import ExternalRecorderImporter

        if coordinator.is_importing:
            raise ServiceValidationError(
                "An import is already in progress; wait for it to finish "
                "(see the Import in progress sensor) before starting another."
            )

        db_type = call.data[CONF_DB_TYPE]
        if db_type == DB_TYPE_SQLITE and not call.data.get(CONF_DB_PATH):
            raise ServiceValidationError("db_path is required when db_type is 'sqlite'")
        if db_type in (DB_TYPE_MYSQL, DB_TYPE_POSTGRESQL):
            missing = [
                field
                for field in _EXTERNAL_SERVER_REQUIRED_FIELDS
                if not call.data.get(field)
            ]
            if missing:
                raise ServiceValidationError(
                    f"Missing required field(s) for db_type={db_type!r}: "
                    f"{', '.join(missing)}"
                )

        now = datetime.now(timezone.utc)
        raw_start: str | None = call.data.get("start_date")
        raw_end: str | None = call.data.get("end_date")
        entity_ids: list[str] | None = call.data.get("entity_ids")
        exclude_entities: list[str] | None = call.data.get("exclude_entities")
        exclude_attributes: list[str] | None = call.data.get("exclude_attributes")

        start_time: datetime | None = (
            datetime.fromisoformat(raw_start).replace(tzinfo=timezone.utc)
            if raw_start
            else None
        )
        end_time = (
            datetime.fromisoformat(raw_end).replace(tzinfo=timezone.utc)
            if raw_end
            else now
        )

        reader = create_external_recorder_reader(db_type, dict(call.data))
        importer = ExternalRecorderImporter(
            hass,
            coordinator.backend,
            reader,
            coordinator.exclude_domains,
            coordinator.exclude_entities,
            coordinator.exclude_attributes,
        )

        # Set synchronously (no `await` between the is_importing check above and
        # this call) so two rapid, back-to-back service calls can't both pass
        # the check before either one claims the lock.
        coordinator.async_set_importing(True)

        async def _run() -> None:
            try:
                await reader.connect()
                try:
                    count = await importer.run(
                        start_time,
                        end_time,
                        entity_ids or None,
                        exclude_entities,
                        exclude_attributes,
                    )
                    coordinator.async_set_importing(False, count)
                finally:
                    await reader.close()
            except Exception:
                _LOGGER.exception("import_from_external_db task failed")
                coordinator.async_set_importing(False)

        hass.async_create_task(_run())

    hass.services.async_register(
        DOMAIN,
        SERVICE_IMPORT_FROM_EXTERNAL_DB,
        _handle_import_external,
        schema=_SERVICE_IMPORT_EXTERNAL_SCHEMA,
    )
    entry.async_on_unload(
        lambda: hass.services.async_remove(DOMAIN, SERVICE_IMPORT_FROM_EXTERNAL_DB)
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator: RecorderCoordinator = entry.runtime_data
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await coordinator.stop()
    return unloaded


class RecorderCoordinator:
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
        self._unsub_states: Callable[[], None] | None = None
        self._unsub_stop: Callable[[], None] | None = None
        self._last_flush: datetime | None = None
        self._is_importing: bool = False
        self._last_import_count: int | None = None
        self._listeners: set[Callable[[], None]] = set()
        self._own_entity_ids: set[str] = set()

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
    def backend(self) -> StorageBackend:
        return self._backend

    @property
    def is_importing(self) -> bool:
        return self._is_importing

    @property
    def last_import_count(self) -> int | None:
        return self._last_import_count

    def async_add_listener(
        self, update_callback: Callable[[], None]
    ) -> Callable[[], None]:
        """Register a listener for coordinator state changes. Returns unsubscribe callable."""
        self._listeners.add(update_callback)

        @callback
        def _remove() -> None:
            self._listeners.discard(update_callback)

        return _remove

    @callback
    def _async_notify_listeners(self) -> None:
        for listener in list(self._listeners):
            listener()

    @callback
    def async_set_importing(self, importing: bool, count: int | None = None) -> None:
        self._is_importing = importing
        if count is not None:
            self._last_import_count = count
        self._async_notify_listeners()

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

    @property
    def exclude_domains(self) -> frozenset[str]:
        return self._exclude_domains

    @property
    def exclude_entities(self) -> frozenset[str]:
        return self._exclude_entities

    @property
    def exclude_attributes(self) -> frozenset[str]:
        return self._exclude_attributes

    def register_own_entity(self, entity_id: str) -> None:
        """Exclude an entity owned by this integration from being logged."""
        self._own_entity_ids.add(entity_id)

    def _should_track_entity(self, entity_id: str) -> bool:
        domain = entity_id.split(".")[0]
        return (
            domain not in self._exclude_domains
            and entity_id not in self._exclude_entities
            and entity_id not in self._own_entity_ids
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
        self._async_notify_listeners()

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
            try:
                await self._flush()
            except Exception:
                # Never let a flush failure kill this loop: it runs unattended
                # in the background, so an uncaught exception here would
                # silently stop all future flushes (and live recording with
                # them) until the next HA restart, with nothing beyond a
                # generic "Task exception was never retrieved" asyncio warning
                # to explain why.
                _LOGGER.exception(
                    "Flush loop iteration failed; will retry on the next interval"
                )

    async def _flush(self) -> None:
        if self._queue.empty():
            return

        snapshots: list[_StateSnapshot] = []
        while not self._queue.empty():
            try:
                snapshots.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        try:
            await self._backend.begin()
        except Exception:
            _LOGGER.exception(
                "Flush begin() failed; re-queueing %d snapshot(s) for the next cycle",
                len(snapshots),
            )
            self._requeue(snapshots)
            return

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
            try:
                await self._backend.rollback()
            except Exception:
                _LOGGER.exception("Flush rollback also failed")

        if failed:
            _LOGGER.warning(
                "Flush completed with %d failed snapshot(s) out of %d",
                failed,
                len(snapshots),
            )

    def _requeue(self, snapshots: list[_StateSnapshot]) -> None:
        """Put snapshots back on the queue after a failed flush, dropping any that don't fit."""
        dropped = 0
        for snap in snapshots:
            try:
                self._queue.put_nowait(snap)
            except asyncio.QueueFull:
                dropped += 1
        if dropped:
            _LOGGER.warning(
                "Observation queue full while re-queueing after a failed flush, "
                "dropped %d snapshot(s)",
                dropped,
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
