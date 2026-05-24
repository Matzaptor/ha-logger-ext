from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.core import HomeAssistant

from .storage.base import ObservationRecord, StorageBackend
from .storage.serialization import serialize, values_equal
from .storage.uuid7 import uuid7

_LOGGER = logging.getLogger(__name__)

_CHUNK_DAYS = 7


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
) -> dict[str, list[tuple[datetime, Any]]]:
    """Build a per-field list of (timestamp, value) pairs from a state list."""
    fields: dict[str, list[tuple[datetime, Any]]] = {}
    for state in states:
        ts = _ensure_utc(state.last_updated)
        fields.setdefault("state", []).append((ts, state.state))
        for attr_name, attr_value in state.attributes.items():
            fields.setdefault(attr_name, []).append((ts, attr_value))
    return fields


class RecorderImporter:
    """Imports historical HA recorder data into the ha_logger_ext backend.

    Idempotent: intervals that already exist in the backend are skipped.
    Resumable: if interrupted, the next run skips time ranges already covered.
    """

    def __init__(self, hass: HomeAssistant, backend: StorageBackend) -> None:
        self._hass = hass
        self._backend = backend

    async def _fetch_all_entity_ids(self) -> list[str]:
        """Return all entity IDs that have states in the recorder.

        Subclass or patch this method in tests to avoid a real recorder dependency.
        """
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.db_schema import States
            from sqlalchemy import distinct, select
        except ImportError:
            return []

        recorder = get_instance(self._hass)

        def _query() -> list[str]:
            with recorder.get_session() as session:
                return [
                    row[0]
                    for row in session.execute(
                        select(distinct(States.entity_id))
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
            _LOGGER.error("import_from_recorder: recorder component is not available")
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
    ) -> int:
        """Run the import. Returns the total number of intervals inserted."""
        if entity_ids is None:
            entity_ids = await self._fetch_all_entity_ids()
            if not entity_ids:
                _LOGGER.info("import_from_recorder: no entities found in recorder, nothing to import")
                return 0

        if start_time is None:
            start_time = await self._fetch_earliest_state_time()
            if start_time is None:
                _LOGGER.info("import_from_recorder: recorder has no states, nothing to import")
                return 0

        _LOGGER.info(
            "import_from_recorder: starting — %d entities, window %s → %s",
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
                "import_from_recorder: chunk %s → %s",
                chunk_start.date(),
                chunk_end.date(),
            )
            states_map = await self._fetch_states(chunk_start, chunk_end, entity_ids)
            if not states_map:
                _LOGGER.debug(
                    "import_from_recorder: chunk %s → %s — no data returned by recorder",
                    chunk_start.date(),
                    chunk_end.date(),
                )
            for entity_id, states in states_map.items():
                ins, skp = await self._import_entity_states(entity_id, states)
                total_inserted += ins
                total_skipped += skp
            chunk_start = chunk_end

        _LOGGER.info(
            "import_from_recorder: complete — %d inserted, %d skipped",
            total_inserted,
            total_skipped,
        )
        return total_inserted

    async def _import_entity_states(
        self, entity_id: str, states: list[Any]
    ) -> tuple[int, int]:
        """Reconstruct validity intervals for one entity and insert gaps.

        Returns (inserted, skipped).
        """
        if not states:
            return 0, 0

        domain = entity_id.split(".")[0]
        ts0 = _ensure_utc(states[0].last_updated)
        entity_pk = await self._backend.get_or_create_entity(entity_id, domain, ts0)

        field_values = _collect_field_values(states)
        _LOGGER.debug(
            "import_from_recorder: processing %s (%d states, %d fields)",
            entity_id,
            len(states),
            len(field_values),
        )

        inserted = 0
        skipped = 0

        for field_name, values in field_values.items():
            for interval in _rle_compress(values):
                if await self._backend.has_observations_in_range(
                    entity_pk, field_name, interval.first_seen, interval.last_seen
                ):
                    skipped += 1
                    continue
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

        _LOGGER.debug(
            "import_from_recorder: %s — %d inserted, %d skipped",
            entity_id,
            inserted,
            skipped,
        )
        return inserted, skipped
