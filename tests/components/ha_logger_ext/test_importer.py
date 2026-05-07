from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.ha_logger_ext.importer import (
    RecorderImporter,
    _collect_field_values,
    _rle_compress,
)
from custom_components.ha_logger_ext.storage.sqlite import SQLiteBackend

TS0 = datetime(2022, 1, 1, 0, 0, tzinfo=timezone.utc)
TS1 = datetime(2022, 1, 1, 1, 0, tzinfo=timezone.utc)
TS2 = datetime(2022, 1, 1, 2, 0, tzinfo=timezone.utc)
TS3 = datetime(2022, 1, 1, 3, 0, tzinfo=timezone.utc)


@pytest.fixture
async def backend(tmp_path: Path) -> SQLiteBackend:
    b = SQLiteBackend(tmp_path / "test.db")
    await b.initialize()
    yield b
    await b.close()


def _state(entity_id: str, state: str, ts: datetime, attrs: dict | None = None) -> MagicMock:
    s = MagicMock()
    s.entity_id = entity_id
    s.state = state
    s.attributes = attrs or {}
    s.last_updated = ts
    return s


# ---------------------------------------------------------------------------
# Unit tests — _rle_compress
# ---------------------------------------------------------------------------


class TestRLECompress:
    def test_single_value_is_one_interval(self) -> None:
        intervals = _rle_compress([(TS0, "on")])
        assert len(intervals) == 1
        assert intervals[0].first_seen == TS0
        assert intervals[0].last_seen == TS0

    def test_equal_values_extend_interval(self) -> None:
        intervals = _rle_compress([(TS0, "on"), (TS1, "on"), (TS2, "on")])
        assert len(intervals) == 1
        assert intervals[0].first_seen == TS0
        assert intervals[0].last_seen == TS2

    def test_value_change_creates_new_interval(self) -> None:
        intervals = _rle_compress([(TS0, "on"), (TS1, "off")])
        assert len(intervals) == 2
        assert intervals[0].last_seen == TS0
        assert intervals[1].first_seen == TS1

    def test_same_value_after_change_is_new_interval(self) -> None:
        # on → off → on must produce 3 separate intervals, not merge first and last
        intervals = _rle_compress([(TS0, "on"), (TS1, "off"), (TS2, "on")])
        assert len(intervals) == 3
        assert intervals[0].first_seen == TS0
        assert intervals[1].first_seen == TS1
        assert intervals[2].first_seen == TS2

    def test_empty_input_returns_empty(self) -> None:
        assert _rle_compress([]) == []

    def test_numeric_values_deduplicated(self) -> None:
        intervals = _rle_compress([(TS0, 20.0), (TS1, 20.0), (TS2, 21.0)])
        assert len(intervals) == 2
        assert intervals[0].last_seen == TS1
        assert intervals[1].first_seen == TS2

    def test_none_values_deduplicated(self) -> None:
        intervals = _rle_compress([(TS0, None), (TS1, None)])
        assert len(intervals) == 1


# ---------------------------------------------------------------------------
# Unit tests — _collect_field_values
# ---------------------------------------------------------------------------


class TestCollectFieldValues:
    def test_state_field_extracted(self) -> None:
        states = [_state("sensor.t", "20", TS0)]
        fields = _collect_field_values(states)
        assert "state" in fields
        assert fields["state"] == [(TS0, "20")]

    def test_attributes_extracted(self) -> None:
        states = [_state("sensor.t", "20", TS0, attrs={"unit": "°C", "friendly_name": "Temp"})]
        fields = _collect_field_values(states)
        assert "unit" in fields
        assert fields["unit"] == [(TS0, "°C")]

    def test_multiple_states_accumulate(self) -> None:
        states = [
            _state("sensor.t", "20", TS0),
            _state("sensor.t", "21", TS1),
        ]
        fields = _collect_field_values(states)
        assert fields["state"] == [(TS0, "20"), (TS1, "21")]


# ---------------------------------------------------------------------------
# Integration tests — RecorderImporter with mocked recorder
# ---------------------------------------------------------------------------


class _FakeImporter(RecorderImporter):
    """Importer subclass that returns a fixed states map instead of querying recorder."""

    def __init__(
        self,
        hass: Any,
        backend: SQLiteBackend,
        states_map: dict[str, list[Any]],
    ) -> None:
        super().__init__(hass, backend)
        self._states_map = states_map

    async def _fetch_states(
        self,
        start: datetime,
        end: datetime,
        entity_ids: list[str] | None,
    ) -> dict[str, list[Any]]:
        return self._states_map


class TestRecorderImporter:
    async def test_inserts_intervals_on_first_import(self, backend: SQLiteBackend) -> None:
        states = [
            _state("sensor.temp", "20", TS0),
            _state("sensor.temp", "20", TS1),  # same → extends interval
            _state("sensor.temp", "21", TS2),  # change → new interval
        ]
        hass = MagicMock()
        importer = _FakeImporter(hass, backend, {"sensor.temp": states})
        inserted = await importer.run(TS0, TS3)
        assert inserted == 2  # one interval for "20" + one for "21", per "state" field

    async def test_skips_already_imported_data(self, backend: SQLiteBackend) -> None:
        states = [_state("sensor.temp", "20", TS0)]
        hass = MagicMock()
        importer = _FakeImporter(hass, backend, {"sensor.temp": states})

        first = await importer.run(TS0, TS1)
        assert first == 1

        # Running again: interval already in DB → skipped
        second = await importer.run(TS0, TS1)
        assert second == 0

    async def test_resumability_after_partial_import(self, backend: SQLiteBackend) -> None:
        # Simulate: first run imports TS0-TS1, second run extends to TS3
        states_first = [_state("sensor.temp", "20", TS0)]
        states_second = [
            _state("sensor.temp", "20", TS0),  # already imported → skipped
            _state("sensor.temp", "21", TS2),  # new → inserted
        ]
        hass = MagicMock()

        importer1 = _FakeImporter(hass, backend, {"sensor.temp": states_first})
        await importer1.run(TS0, TS1)

        importer2 = _FakeImporter(hass, backend, {"sensor.temp": states_second})
        inserted = await importer2.run(TS0, TS3)
        # TS0 interval is covered; TS2 interval is new
        assert inserted == 1

    async def test_import_handles_multiple_entities(self, backend: SQLiteBackend) -> None:
        states_map = {
            "sensor.temp": [_state("sensor.temp", "20", TS0)],
            "sensor.humidity": [_state("sensor.humidity", "60", TS0)],
        }
        hass = MagicMock()
        importer = _FakeImporter(hass, backend, states_map)
        inserted = await importer.run(TS0, TS1)
        assert inserted == 2

    async def test_import_attributes_tracked_separately(self, backend: SQLiteBackend) -> None:
        states = [
            _state("sensor.temp", "20", TS0, attrs={"unit": "°C"}),
            _state("sensor.temp", "21", TS1, attrs={"unit": "°C"}),
        ]
        hass = MagicMock()
        importer = _FakeImporter(hass, backend, {"sensor.temp": states})
        inserted = await importer.run(TS0, TS2)
        # "state" field: 20→21 = 2 intervals; "unit" field: °C→°C = 1 interval
        assert inserted == 3

    async def test_empty_states_map_inserts_nothing(self, backend: SQLiteBackend) -> None:
        hass = MagicMock()
        importer = _FakeImporter(hass, backend, {})
        inserted = await importer.run(TS0, TS1)
        assert inserted == 0


# ---------------------------------------------------------------------------
# Integration tests — has_observations_in_range on SQLite backend
# ---------------------------------------------------------------------------


class TestHasObservationsInRange:
    async def test_returns_false_when_empty(self, backend: SQLiteBackend) -> None:
        pk = await backend.get_or_create_entity("sensor.t", "sensor", TS0)
        result = await backend.has_observations_in_range(pk, "state", TS0, TS2)
        assert result is False

    async def test_returns_true_when_interval_overlaps(self, backend: SQLiteBackend) -> None:
        states = [_state("sensor.t", "20", TS0), _state("sensor.t", "20", TS2)]
        hass = MagicMock()
        importer = _FakeImporter(hass, backend, {"sensor.t": states})
        await importer.run(TS0, TS3)

        pk = await backend.get_or_create_entity("sensor.t", "sensor", TS0)
        # Our DB has an interval [TS0, TS2]; asking about [TS1, TS3] overlaps
        result = await backend.has_observations_in_range(pk, "state", TS1, TS3)
        assert result is True

    async def test_returns_false_for_non_overlapping_range(
        self, backend: SQLiteBackend
    ) -> None:
        states = [_state("sensor.t", "20", TS0)]
        hass = MagicMock()
        importer = _FakeImporter(hass, backend, {"sensor.t": states})
        await importer.run(TS0, TS1)

        pk = await backend.get_or_create_entity("sensor.t", "sensor", TS0)
        # Our DB has an interval [TS0, TS0]; asking about [TS2, TS3] does not overlap
        result = await backend.has_observations_in_range(pk, "state", TS2, TS3)
        assert result is False

    async def test_different_field_returns_false(self, backend: SQLiteBackend) -> None:
        states = [_state("sensor.t", "20", TS0)]
        hass = MagicMock()
        importer = _FakeImporter(hass, backend, {"sensor.t": states})
        await importer.run(TS0, TS1)

        pk = await backend.get_or_create_entity("sensor.t", "sensor", TS0)
        result = await backend.has_observations_in_range(pk, "unit", TS0, TS2)
        assert result is False
