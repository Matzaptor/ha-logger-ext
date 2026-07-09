from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Any, ClassVar

from homeassistant.core import HomeAssistant

from .external_recorder_reader import ExternalRecorderReader
from .storage.base import ObservationRecord, StorageBackend
from .storage.serialization import serialize, values_equal
from .storage.uuid7 import uuid7

_LOGGER = logging.getLogger(__name__)

_CHUNK_DAYS = 1
# Log a progress line every N entities processed within a single chunk, so a
# calendar day with heavy volume shows visible forward progress instead of
# going quiet until the whole chunk finishes.
_ENTITY_PROGRESS_LOG_INTERVAL = 25
# Log a heartbeat at most this often while working through one entity's
# intervals. A single high-cardinality entity (e.g. a noisy BLE/GPS tracker
# whose value changes on nearly every reading, defeating RLE compression)
# can have hundreds of thousands of intervals, each needing its own
# sequential read+write round trip — that can legitimately take hours with
# nothing else to show for it, since the per-entity log lines only print
# before the first interval and after the very last one. Time-based (not
# count-based) so it stays quiet for normal-sized entities but never goes
# fully silent for a long time regardless of how large one entity turns out
# to be.
_INTERVAL_PROGRESS_LOG_SECONDS = 30


@dataclass
class _Interval:
    serialized: dict[str, Any]
    first_seen: datetime
    last_seen: datetime


def _rle_compress(values: list[tuple[datetime, Any]]) -> list[_Interval]:
    """Compress a chronological time series into non-overlapping validity intervals."""
    intervals: list[_Interval] = []
    current: _Interval | None = None
    for ts, value in values:
        ser = serialize(value)
        if current is None:
            current = _Interval(ser, ts, ts)
        elif values_equal(current.serialized, ser):
            current.last_seen = ts
        else:
            intervals.append(current)
            current = _Interval(ser, ts, ts)
    if current is not None:
        intervals.append(current)
    return intervals


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _collect_field_values(
    states: list[Any],
    exclude_attributes: frozenset[str] = frozenset(),
) -> dict[str, list[tuple[datetime, Any]]]:
    """Build a per-field list of (timestamp, value) pairs from a state list."""
    fields: dict[str, list[tuple[datetime, Any]]] = {}
    for state in states:
        ts = _ensure_utc(state.last_updated)
        fields.setdefault("state", []).append((ts, state.state))
        for attr_name, attr_value in state.attributes.items():
            if attr_name in exclude_attributes:
                continue
            fields.setdefault(attr_name, []).append((ts, attr_value))
    return fields


class RecorderImporter:
    """Imports historical HA recorder data into the ha_recorder_ext backend.

    Idempotent: intervals that already exist in the backend are skipped.
    Resumable: if interrupted, the next run skips time ranges already covered.
    """

    # Overridden by subclasses so log lines identify which service is
    # actually running instead of always reading "import_from_recorder".
    _LOG_PREFIX: ClassVar[str] = "import_from_recorder"

    def __init__(
        self,
        hass: HomeAssistant,
        backend: StorageBackend,
        configured_exclude_domains: frozenset[str] | None = None,
        configured_exclude_entities: frozenset[str] | None = None,
        configured_exclude_attributes: frozenset[str] | None = None,
    ) -> None:
        self._hass = hass
        self._backend = backend
        self._configured_exclude_domains = configured_exclude_domains or frozenset()
        self._configured_exclude_entities = configured_exclude_entities or frozenset()
        self._configured_exclude_attributes = (
            configured_exclude_attributes or frozenset()
        )

    async def _fetch_all_entity_ids(self) -> list[str]:
        """Return all entity IDs that have states in the recorder.

        Subclass or patch this method in tests to avoid a real recorder dependency.
        """
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.db_schema import StatesMeta
            from sqlalchemy import distinct, select
        except ImportError:
            _LOGGER.error(
                "%s: recorder component is not available; treating as "
                "'no entities found' — this is very likely wrong, not an "
                "actually empty recorder",
                self._LOG_PREFIX,
            )
            return []

        recorder = get_instance(self._hass)

        def _query() -> list[str]:
            with recorder.get_session() as session:
                return [
                    row[0]
                    for row in session.execute(
                        select(distinct(StatesMeta.entity_id))
                    ).fetchall()
                ]

        return await recorder.async_add_executor_job(_query)

    async def _fetch_earliest_state_time(self) -> datetime | None:
        """Return the oldest state timestamp from the recorder, or None if unavailable.

        Subclass or patch this method in tests to avoid a real recorder dependency.
        """
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.db_schema import States
            from sqlalchemy import func, select
        except ImportError:
            _LOGGER.error(
                "%s: recorder component is not available; treating as "
                "'recorder has no states' — this is very likely wrong, not "
                "an actually empty recorder",
                self._LOG_PREFIX,
            )
            return None

        recorder = get_instance(self._hass)

        def _query() -> float | None:
            with recorder.get_session() as session:
                return session.execute(
                    select(func.min(States.last_updated_ts))
                ).scalar()

        result: float | None = await recorder.async_add_executor_job(_query)
        if result is None:
            return None
        return datetime.fromtimestamp(float(result), tz=timezone.utc)

    async def _fetch_states(
        self,
        start: datetime,
        end: datetime,
        entity_ids: list[str],
    ) -> dict[str, list[Any]]:
        """Query the HA recorder for states in [start, end].

        Subclass or patch this method in tests to avoid a real recorder dependency.
        """
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder import history as recorder_history
        except ImportError:
            _LOGGER.error("%s: recorder component is not available", self._LOG_PREFIX)
            return {}

        recorder = get_instance(self._hass)
        return await recorder.async_add_executor_job(  # type: ignore[no-any-return]
            recorder_history.get_significant_states,
            self._hass,
            start,
            end,
            entity_ids,
            None,   # filters
            True,   # include_start_time_state
            False,  # significant_changes_only
        )

    async def run(
        self,
        start_time: datetime | None,
        end_time: datetime,
        entity_ids: list[str] | None = None,
        exclude_entities: list[str] | None = None,
        exclude_attributes: list[str] | None = None,
    ) -> int:
        """Run the import. Returns the total number of intervals inserted."""
        # entity_ids explicitly passed by the caller is treated as a deliberate
        # scope and bypasses the configured exclude_domains/exclude_entities
        # (only exclude_entities from this same call can still trim it). When
        # entity_ids is omitted, the configured exclude_domains/exclude_entities
        # apply exactly as they do for live acquisition.
        apply_configured_entity_filters = entity_ids is None
        if entity_ids is None:
            entity_ids = await self._fetch_all_entity_ids()
            if not entity_ids:
                _LOGGER.info(
                    "%s: no entities found in recorder, nothing to import", self._LOG_PREFIX
                )
                return 0

        effective_exclude_entities = set(exclude_entities or [])
        if apply_configured_entity_filters:
            effective_exclude_entities |= self._configured_exclude_entities

        if apply_configured_entity_filters and self._configured_exclude_domains:
            before = len(entity_ids)
            entity_ids = [
                e
                for e in entity_ids
                if e.split(".")[0] not in self._configured_exclude_domains
            ]
            if len(entity_ids) != before:
                _LOGGER.info(
                    "%s: excluding %d entities by configured domain (%d remaining)",
                    self._LOG_PREFIX,
                    before - len(entity_ids),
                    len(entity_ids),
                )

        if effective_exclude_entities:
            before = len(entity_ids)
            entity_ids = [e for e in entity_ids if e not in effective_exclude_entities]
            _LOGGER.info(
                "%s: excluding %d entities (%d remaining)",
                self._LOG_PREFIX,
                before - len(entity_ids),
                len(entity_ids),
            )

        if not entity_ids:
            _LOGGER.info(
                "%s: all entities excluded, nothing to import", self._LOG_PREFIX
            )
            return 0

        effective_exclude_attributes = (
            self._configured_exclude_attributes | set(exclude_attributes or [])
        )

        if start_time is None:
            start_time = await self._fetch_earliest_state_time()
            if start_time is None:
                _LOGGER.info("%s: recorder has no states, nothing to import", self._LOG_PREFIX)
                return 0

        _LOGGER.info(
            "%s: starting — %d entities, window %s → %s",
            self._LOG_PREFIX,
            len(entity_ids),
            start_time.date(),
            end_time.date(),
        )

        total_inserted = 0
        total_skipped = 0
        chunk_start = start_time
        while chunk_start < end_time:
            chunk_end = min(chunk_start + timedelta(days=_CHUNK_DAYS), end_time)
            _LOGGER.debug(
                "%s: chunk %s → %s — fetching states",
                self._LOG_PREFIX,
                chunk_start.date(),
                chunk_end.date(),
            )
            try:
                states_map = await self._fetch_states(chunk_start, chunk_end, entity_ids)
            except Exception:
                _LOGGER.exception(
                    "%s: failed fetching states for chunk %s → %s",
                    self._LOG_PREFIX,
                    chunk_start.date(),
                    chunk_end.date(),
                )
                raise
            _LOGGER.debug(
                "%s: chunk %s → %s — %d entities with data",
                self._LOG_PREFIX,
                chunk_start.date(),
                chunk_end.date(),
                len(states_map),
            )

            entity_count = len(states_map)
            for processed, (entity_id, states) in enumerate(states_map.items(), start=1):
                try:
                    ins, skp = await self._import_entity_states(
                        entity_id, states, effective_exclude_attributes
                    )
                except Exception:
                    _LOGGER.exception(
                        "%s: failed importing entity %s in chunk %s → %s",
                        self._LOG_PREFIX,
                        entity_id,
                        chunk_start.date(),
                        chunk_end.date(),
                    )
                    raise
                total_inserted += ins
                total_skipped += skp
                if (
                    entity_count > _ENTITY_PROGRESS_LOG_INTERVAL
                    and processed % _ENTITY_PROGRESS_LOG_INTERVAL == 0
                ):
                    _LOGGER.info(
                        "%s: chunk %s → %s — %d/%d entities, %d inserted, %d skipped so far",
                        self._LOG_PREFIX,
                        chunk_start.date(),
                        chunk_end.date(),
                        processed,
                        entity_count,
                        total_inserted,
                        total_skipped,
                    )

            _LOGGER.info(
                "%s: progress — through %s, %d inserted, %d skipped so far",
                self._LOG_PREFIX,
                chunk_end.date(),
                total_inserted,
                total_skipped,
            )
            chunk_start = chunk_end

        _LOGGER.info(
            "%s: complete — %d inserted, %d skipped",
            self._LOG_PREFIX,
            total_inserted,
            total_skipped,
        )
        return total_inserted

    async def _import_entity_states(
        self,
        entity_id: str,
        states: list[Any],
        exclude_attributes: frozenset[str] = frozenset(),
    ) -> tuple[int, int]:
        """Reconstruct validity intervals for one entity and insert gaps.

        Returns (inserted, skipped).
        """
        if not states:
            return 0, 0

        domain = entity_id.split(".")[0]
        ts0 = _ensure_utc(states[0].last_updated)
        entity_pk = await self._backend.get_or_create_entity(entity_id, domain, ts0)

        field_values = _collect_field_values(states, exclude_attributes)
        _LOGGER.debug(
            "%s: processing %s (%d states, %d fields)",
            self._LOG_PREFIX,
            entity_id,
            len(states),
            len(field_values),
        )

        inserted = 0
        skipped = 0
        processed = 0
        last_progress_log = monotonic()

        for field_name, values in field_values.items():
            for interval in _rle_compress(values):
                processed += 1
                if await self._backend.has_observations_in_range(
                    entity_pk, field_name, interval.first_seen, interval.last_seen
                ):
                    skipped += 1
                else:
                    obs = ObservationRecord(
                        id=uuid7(),
                        entity_pk=entity_pk,
                        field_name=field_name,
                        value_type=interval.serialized["value_type"],
                        value_str=interval.serialized.get("value_str"),
                        value_int=interval.serialized.get("value_int"),
                        value_float=interval.serialized.get("value_float"),
                        value_bool=interval.serialized.get("value_bool"),
                        value_datetime=interval.serialized.get("value_datetime"),
                        value_date=interval.serialized.get("value_date"),
                        value_time=interval.serialized.get("value_time"),
                        value_json=interval.serialized.get("value_json"),
                        first_seen=interval.first_seen,
                        last_seen=interval.last_seen,
                    )
                    await self._backend.insert_observation(obs)
                    inserted += 1

                now = monotonic()
                if now - last_progress_log >= _INTERVAL_PROGRESS_LOG_SECONDS:
                    _LOGGER.info(
                        "%s: %s — still processing, %d intervals so far "
                        "(%d inserted, %d skipped)",
                        self._LOG_PREFIX,
                        entity_id,
                        processed,
                        inserted,
                        skipped,
                    )
                    last_progress_log = now

        _LOGGER.debug(
            "%s: %s — %d inserted, %d skipped",
            self._LOG_PREFIX,
            entity_id,
            inserted,
            skipped,
        )
        return inserted, skipped


class ExternalRecorderImporter(RecorderImporter):
    """Imports historical data from an external database holding an HA recorder backup.

    Reuses RecorderImporter's chunking, RLE compression, and idempotent-insert
    logic unchanged, delegating state retrieval to an ExternalRecorderReader
    instead of the local HA recorder. The caller owns the reader's connection
    lifecycle (connect() before running, close() after).
    """

    _LOG_PREFIX: ClassVar[str] = "import_from_external_db"

    def __init__(
        self,
        hass: HomeAssistant,
        backend: StorageBackend,
        reader: ExternalRecorderReader,
        configured_exclude_domains: frozenset[str] | None = None,
        configured_exclude_entities: frozenset[str] | None = None,
        configured_exclude_attributes: frozenset[str] | None = None,
    ) -> None:
        super().__init__(
            hass,
            backend,
            configured_exclude_domains,
            configured_exclude_entities,
            configured_exclude_attributes,
        )
        self._reader = reader

    async def _fetch_all_entity_ids(self) -> list[str]:
        return await self._reader.fetch_all_entity_ids()

    async def _fetch_earliest_state_time(self) -> datetime | None:
        return await self._reader.fetch_earliest_state_time()

    async def _fetch_states(
        self,
        start: datetime,
        end: datetime,
        entity_ids: list[str],
    ) -> dict[str, list[Any]]:
        return await self._reader.fetch_states(start, end, entity_ids)
