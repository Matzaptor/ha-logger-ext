from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.ha_recorder_ext.external_recorder_reader import (
    SQLiteExternalRecorderReader,
)
from custom_components.ha_recorder_ext.importer import (
    ExternalRecorderImporter,
    RecorderImporter,
    _collect_field_values,
    _rle_compress,
)
from custom_components.ha_recorder_ext.storage.sqlite import SQLiteBackend

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
        earliest: datetime | None = None,
        all_entity_ids: list[str] | None = None,
    ) -> None:
        super().__init__(hass, backend)
        self._states_map = states_map
        self._earliest = earliest
        self._all_entity_ids = all_entity_ids if all_entity_ids is not None else list(states_map)

    async def _fetch_states(
        self,
        start: datetime,
        end: datetime,
        entity_ids: list[str],
    ) -> dict[str, list[Any]]:
        return self._states_map

    async def _fetch_earliest_state_time(self) -> datetime | None:
        return self._earliest

    async def _fetch_all_entity_ids(self) -> list[str]:
        return self._all_entity_ids


class _FakeExternalImporter(ExternalRecorderImporter):
    """ExternalRecorderImporter variant that returns a fixed states map, no real reader."""

    def __init__(
        self,
        hass: Any,
        backend: SQLiteBackend,
        states_map: dict[str, list[Any]],
        earliest: datetime | None = None,
        all_entity_ids: list[str] | None = None,
    ) -> None:
        super().__init__(hass, backend, reader=MagicMock())
        self._states_map = states_map
        self._earliest = earliest
        self._all_entity_ids = all_entity_ids if all_entity_ids is not None else list(states_map)

    async def _fetch_states(
        self,
        start: datetime,
        end: datetime,
        entity_ids: list[str],
    ) -> dict[str, list[Any]]:
        return self._states_map

    async def _fetch_earliest_state_time(self) -> datetime | None:
        return self._earliest

    async def _fetch_all_entity_ids(self) -> list[str]:
        return self._all_entity_ids


class _FailingFetchImporter(_FakeImporter):
    """_FakeImporter variant whose _fetch_states always raises."""

    async def _fetch_states(
        self,
        start: datetime,
        end: datetime,
        entity_ids: list[str],
    ) -> dict[str, list[Any]]:
        raise RuntimeError("boom")


class _RecordingFakeImporter(_FakeImporter):
    """_FakeImporter variant that records every (start, end) window passed to _fetch_states."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fetch_windows: list[tuple[datetime, datetime]] = []

    async def _fetch_states(
        self,
        start: datetime,
        end: datetime,
        entity_ids: list[str],
    ) -> dict[str, list[Any]]:
        self.fetch_windows.append((start, end))
        return await super()._fetch_states(start, end, entity_ids)


class TestRecorderImporterChunking:
    async def test_multi_day_window_splits_into_daily_chunks(
        self, backend: SQLiteBackend
    ) -> None:
        start = TS0
        end = TS0 + timedelta(days=3)
        hass = MagicMock()
        importer = _RecordingFakeImporter(hass, backend, {})
        await importer.run(start, end, entity_ids=["sensor.temp"])

        assert len(importer.fetch_windows) == 3
        for window_start, window_end in importer.fetch_windows:
            assert window_end - window_start == timedelta(days=1)
        assert importer.fetch_windows[0][0] == start
        assert importer.fetch_windows[-1][1] == end


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

    async def test_none_entity_ids_fetches_all_from_recorder(self, backend: SQLiteBackend) -> None:
        states = [_state("sensor.temp", "20", TS0)]
        hass = MagicMock()
        importer = _FakeImporter(hass, backend, {"sensor.temp": states}, earliest=TS0)
        # entity_ids=None → resolved via _fetch_all_entity_ids → ["sensor.temp"]
        inserted = await importer.run(TS0, TS1, entity_ids=None)
        assert inserted == 1

    async def test_none_entity_ids_with_empty_recorder_returns_zero(
        self, backend: SQLiteBackend
    ) -> None:
        hass = MagicMock()
        importer = _FakeImporter(hass, backend, {}, all_entity_ids=[])
        inserted = await importer.run(TS0, TS1, entity_ids=None)
        assert inserted == 0

    async def test_none_start_uses_earliest_recorder_state(self, backend: SQLiteBackend) -> None:
        states = [_state("sensor.temp", "20", TS0)]
        hass = MagicMock()
        importer = _FakeImporter(hass, backend, {"sensor.temp": states}, earliest=TS0)
        inserted = await importer.run(None, TS1)
        assert inserted == 1

    async def test_none_start_with_empty_recorder_returns_zero(
        self, backend: SQLiteBackend
    ) -> None:
        hass = MagicMock()
        importer = _FakeImporter(hass, backend, {}, earliest=None)
        inserted = await importer.run(None, TS1)
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


# ---------------------------------------------------------------------------
# Logging tests
# ---------------------------------------------------------------------------

_IMPORTER_LOGGER = "custom_components.ha_recorder_ext.importer"


class _EmptyChunkImporter(_FakeImporter):
    """_FakeImporter variant that always returns an empty states map from _fetch_states."""

    async def _fetch_states(
        self,
        start: datetime,
        end: datetime,
        entity_ids: list[str],
    ) -> dict[str, list[Any]]:
        return {}


class TestImporterLogging:
    async def test_info_log_at_start(
        self, backend: SQLiteBackend, caplog: pytest.LogCaptureFixture
    ) -> None:
        states = [_state("sensor.temp", "20", TS0)]
        hass = MagicMock()
        importer = _FakeImporter(hass, backend, {"sensor.temp": states})
        with caplog.at_level(logging.INFO, logger=_IMPORTER_LOGGER):
            await importer.run(TS0, TS1)
        assert "starting" in caplog.text
        assert "1 entities" in caplog.text

    async def test_info_log_at_completion(
        self, backend: SQLiteBackend, caplog: pytest.LogCaptureFixture
    ) -> None:
        states = [_state("sensor.temp", "20", TS0)]
        hass = MagicMock()
        importer = _FakeImporter(hass, backend, {"sensor.temp": states})
        with caplog.at_level(logging.INFO, logger=_IMPORTER_LOGGER):
            await importer.run(TS0, TS1)
        assert "complete" in caplog.text
        assert "inserted" in caplog.text
        assert "skipped" in caplog.text

    async def test_info_progress_log_per_chunk(
        self, backend: SQLiteBackend, caplog: pytest.LogCaptureFixture
    ) -> None:
        states = [_state("sensor.temp", "20", TS0)]
        hass = MagicMock()
        importer = _FakeImporter(hass, backend, {"sensor.temp": states})
        start = TS0
        end = TS0 + timedelta(days=3)
        with caplog.at_level(logging.INFO, logger=_IMPORTER_LOGGER):
            await importer.run(start, end)
        # One progress line per processed day-sized chunk (3 days → 3 lines).
        assert caplog.text.count("progress —") == 3
        assert "inserted" in caplog.text
        assert "skipped" in caplog.text
        # Per-entity detail must stay DEBUG-only, not leak into the INFO capture.
        assert "processing sensor.temp" not in caplog.text

    async def test_debug_log_processing_entity(
        self, backend: SQLiteBackend, caplog: pytest.LogCaptureFixture
    ) -> None:
        states = [_state("sensor.temp", "20", TS0), _state("sensor.temp", "21", TS1)]
        hass = MagicMock()
        importer = _FakeImporter(hass, backend, {"sensor.temp": states})
        with caplog.at_level(logging.DEBUG, logger=_IMPORTER_LOGGER):
            await importer.run(TS0, TS2)
        assert "processing sensor.temp" in caplog.text
        assert "states" in caplog.text

    async def test_debug_log_entity_result(
        self, backend: SQLiteBackend, caplog: pytest.LogCaptureFixture
    ) -> None:
        states = [_state("sensor.temp", "20", TS0), _state("sensor.temp", "21", TS1)]
        hass = MagicMock()
        importer = _FakeImporter(hass, backend, {"sensor.temp": states})
        with caplog.at_level(logging.DEBUG, logger=_IMPORTER_LOGGER):
            await importer.run(TS0, TS2)
        assert "sensor.temp" in caplog.text
        assert "inserted" in caplog.text
        assert "skipped" in caplog.text

    async def test_debug_log_skipped_on_second_run(
        self, backend: SQLiteBackend, caplog: pytest.LogCaptureFixture
    ) -> None:
        states = [_state("sensor.temp", "20", TS0)]
        hass = MagicMock()
        importer = _FakeImporter(hass, backend, {"sensor.temp": states})
        await importer.run(TS0, TS1)
        caplog.clear()
        with caplog.at_level(logging.DEBUG, logger=_IMPORTER_LOGGER):
            await importer.run(TS0, TS1)
        assert "1 skipped" in caplog.text

    async def test_debug_log_empty_chunk(
        self, backend: SQLiteBackend, caplog: pytest.LogCaptureFixture
    ) -> None:
        hass = MagicMock()
        importer = _EmptyChunkImporter(
            hass, backend, {}, all_entity_ids=["sensor.temp"]
        )
        with caplog.at_level(logging.DEBUG, logger=_IMPORTER_LOGGER):
            await importer.run(TS0, TS1, entity_ids=["sensor.temp"])
        assert "0 entities with data" in caplog.text

    async def test_log_prefix_reflects_which_service_is_running(
        self, backend: SQLiteBackend, caplog: pytest.LogCaptureFixture
    ) -> None:
        states = [_state("sensor.temp", "20", TS0)]
        hass = MagicMock()
        importer = _FakeExternalImporter(hass, backend, {"sensor.temp": states})
        with caplog.at_level(logging.INFO, logger=_IMPORTER_LOGGER):
            await importer.run(TS0, TS1)
        assert "import_from_external_db: starting" in caplog.text
        assert "import_from_recorder:" not in caplog.text

    async def test_entity_progress_log_within_heavy_chunk(
        self, backend: SQLiteBackend, caplog: pytest.LogCaptureFixture
    ) -> None:
        hass = MagicMock()
        states_map = {
            f"sensor.e{i}": [_state(f"sensor.e{i}", "20", TS0)] for i in range(30)
        }
        importer = _FakeImporter(hass, backend, states_map)
        with caplog.at_level(logging.INFO, logger=_IMPORTER_LOGGER):
            await importer.run(TS0, TS1)
        assert "25/30 entities" in caplog.text

    async def test_no_entity_progress_log_below_threshold(
        self, backend: SQLiteBackend, caplog: pytest.LogCaptureFixture
    ) -> None:
        hass = MagicMock()
        states_map = {
            f"sensor.e{i}": [_state(f"sensor.e{i}", "20", TS0)] for i in range(5)
        }
        importer = _FakeImporter(hass, backend, states_map)
        with caplog.at_level(logging.INFO, logger=_IMPORTER_LOGGER):
            await importer.run(TS0, TS1)
        # The "starting — N entities, window ..." line is expected; only the
        # per-entity "processed/total entities" progress line must be absent.
        assert re.search(r"\d+/\d+ entities", caplog.text) is None

    async def test_fetch_states_failure_is_logged_with_chunk_context(
        self, backend: SQLiteBackend, caplog: pytest.LogCaptureFixture
    ) -> None:
        hass = MagicMock()
        importer = _FailingFetchImporter(hass, backend, {}, all_entity_ids=["sensor.temp"])
        with caplog.at_level(logging.ERROR, logger=_IMPORTER_LOGGER):
            with pytest.raises(RuntimeError):
                await importer.run(TS0, TS1, entity_ids=["sensor.temp"])
        assert "failed fetching states for chunk" in caplog.text

    async def test_import_entity_failure_is_logged_with_entity_context(
        self, backend: SQLiteBackend, caplog: pytest.LogCaptureFixture
    ) -> None:
        hass = MagicMock()
        importer = _FakeImporter(hass, backend, {"sensor.bad": [object()]})
        with caplog.at_level(logging.ERROR, logger=_IMPORTER_LOGGER):
            with pytest.raises(AttributeError):
                await importer.run(TS0, TS1)
        assert "failed importing entity sensor.bad" in caplog.text

    async def test_interval_heartbeat_log_for_slow_entity(
        self, backend: SQLiteBackend, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A single entity with enough intervals to take a long time must
        show periodic INFO-level progress instead of going silent between
        its "processing" and "inserted/skipped" DEBUG lines — the only two
        lines a high-cardinality entity would otherwise ever produce, even
        if working through it takes hours."""
        states = [
            _state("sensor.noisy", "0", TS0),
            _state("sensor.noisy", "1", TS1),
            _state("sensor.noisy", "2", TS2),
        ]
        hass = MagicMock()
        importer = _FakeImporter(hass, backend, {"sensor.noisy": states})

        # monotonic() is called once before the loop (baseline) and once per
        # interval (3 intervals here). Simulate 35s elapsed by the 2nd
        # interval, crossing the 30s heartbeat threshold exactly once.
        # Patched as the name imported into importer.py (not the global
        # `time` module), so this doesn't touch asyncio's own clock calls.
        with patch(
            "custom_components.ha_recorder_ext.importer.monotonic",
            side_effect=[0.0, 5.0, 35.0, 36.0],
        ):
            with caplog.at_level(logging.INFO, logger=_IMPORTER_LOGGER):
                await importer.run(TS0, TS3)

        assert "still processing, 2 intervals so far" in caplog.text
        assert caplog.text.count("still processing") == 1


# ---------------------------------------------------------------------------
# Integration tests — ExternalRecorderImporter (real SQLite reader + real backend)
# ---------------------------------------------------------------------------


async def _create_external_recorder_db(
    path: Path, rows: list[tuple[str, str, datetime, dict | None]]
) -> None:
    """Build a minimal, modern-schema (HA 2023.4+) recorder sqlite DB for tests."""
    import aiosqlite

    async with aiosqlite.connect(path) as conn:
        await conn.execute(
            "CREATE TABLE states_meta ("
            "metadata_id INTEGER PRIMARY KEY, entity_id TEXT UNIQUE)"
        )
        await conn.execute(
            "CREATE TABLE state_attributes ("
            "attributes_id INTEGER PRIMARY KEY, shared_attrs TEXT)"
        )
        await conn.execute(
            "CREATE TABLE states ("
            "state_id INTEGER PRIMARY KEY, metadata_id INTEGER, state TEXT, "
            "attributes_id INTEGER, last_updated_ts REAL)"
        )
        metadata_ids: dict[str, int] = {}
        attribute_ids: dict[str, int] = {}
        for entity_id, state, ts, attrs in rows:
            if entity_id not in metadata_ids:
                cur = await conn.execute(
                    "INSERT INTO states_meta (entity_id) VALUES (?)", (entity_id,)
                )
                metadata_ids[entity_id] = cur.lastrowid
            attributes_id = None
            if attrs is not None:
                key = json.dumps(attrs, sort_keys=True)
                if key not in attribute_ids:
                    cur = await conn.execute(
                        "INSERT INTO state_attributes (shared_attrs) VALUES (?)", (key,)
                    )
                    attribute_ids[key] = cur.lastrowid
                attributes_id = attribute_ids[key]
            await conn.execute(
                "INSERT INTO states (metadata_id, state, attributes_id, last_updated_ts) "
                "VALUES (?, ?, ?, ?)",
                (metadata_ids[entity_id], state, attributes_id, ts.timestamp()),
            )
        await conn.commit()


class TestExternalRecorderImporter:
    async def test_seam_methods_delegate_to_reader(self, backend: SQLiteBackend) -> None:
        reader = MagicMock()
        reader.fetch_all_entity_ids = AsyncMock(return_value=["sensor.a"])
        reader.fetch_earliest_state_time = AsyncMock(return_value=TS0)
        reader.fetch_states = AsyncMock(return_value={})
        hass = MagicMock()

        importer = ExternalRecorderImporter(hass, backend, reader)

        assert await importer._fetch_all_entity_ids() == ["sensor.a"]
        assert await importer._fetch_earliest_state_time() == TS0
        assert await importer._fetch_states(TS0, TS1, ["sensor.a"]) == {}
        reader.fetch_states.assert_awaited_once_with(TS0, TS1, ["sensor.a"])

    async def test_end_to_end_import_from_sqlite_recorder_backup(
        self, backend: SQLiteBackend, tmp_path: Path
    ) -> None:
        recorder_db = tmp_path / "recorder_backup.db"
        await _create_external_recorder_db(
            recorder_db,
            [
                ("sensor.temp", "20", TS0, {"unit": "°C"}),
                ("sensor.temp", "20", TS1, {"unit": "°C"}),
                ("sensor.temp", "21", TS2, {"unit": "°C"}),
            ],
        )
        reader = SQLiteExternalRecorderReader(recorder_db)
        await reader.connect()
        hass = MagicMock()
        try:
            importer = ExternalRecorderImporter(hass, backend, reader)
            inserted = await importer.run(TS0, TS3)
        finally:
            await reader.close()

        # "state" field: 20→21 = 2 intervals; "unit" field: constant = 1 interval
        assert inserted == 3

    async def test_end_to_end_is_idempotent(
        self, backend: SQLiteBackend, tmp_path: Path
    ) -> None:
        recorder_db = tmp_path / "recorder_backup.db"
        await _create_external_recorder_db(
            recorder_db, [("sensor.temp", "20", TS0, None)]
        )
        hass = MagicMock()

        reader1 = SQLiteExternalRecorderReader(recorder_db)
        await reader1.connect()
        try:
            first = await ExternalRecorderImporter(hass, backend, reader1).run(TS0, TS1)
        finally:
            await reader1.close()

        reader2 = SQLiteExternalRecorderReader(recorder_db)
        await reader2.connect()
        try:
            second = await ExternalRecorderImporter(hass, backend, reader2).run(TS0, TS1)
        finally:
            await reader2.close()

        assert first == 1
        assert second == 0
