from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from custom_components.ha_logger_ext.storage.base import ObservationRecord
from custom_components.ha_logger_ext.storage.serialization import serialize, values_equal
from custom_components.ha_logger_ext.storage.sqlite import SQLiteBackend
from custom_components.ha_logger_ext.storage.uuid7 import uuid7

TS = datetime(2026, 5, 6, 10, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
async def backend(tmp_path: Path) -> SQLiteBackend:
    b = SQLiteBackend(tmp_path / "test.db")
    await b.initialize()
    yield b
    await b.close()


def _obs(entity_pk, field, value_type, **kwargs) -> ObservationRecord:
    return ObservationRecord(
        id=uuid7(),
        entity_pk=entity_pk,
        field_name=field,
        value_type=value_type,
        first_seen=TS,
        last_seen=TS,
        **kwargs,
    )


class TestEntityManagement:
    async def test_creates_entity(self, backend: SQLiteBackend) -> None:
        pk = await backend.get_or_create_entity("sensor.temp", "sensor", TS)
        assert pk is not None

    async def test_same_entity_id_returns_same_pk(self, backend: SQLiteBackend) -> None:
        pk1 = await backend.get_or_create_entity("sensor.temp", "sensor", TS)
        pk2 = await backend.get_or_create_entity("sensor.temp", "sensor", TS)
        assert pk1 == pk2

    async def test_different_entities_have_different_pks(self, backend: SQLiteBackend) -> None:
        pk1 = await backend.get_or_create_entity("sensor.temp", "sensor", TS)
        pk2 = await backend.get_or_create_entity("sensor.humidity", "sensor", TS)
        assert pk1 != pk2


class TestObservations:
    async def test_no_observation_returns_none(self, backend: SQLiteBackend) -> None:
        pk = await backend.get_or_create_entity("sensor.temp", "sensor", TS)
        assert await backend.get_latest_observation(pk, "state") is None

    async def test_insert_and_retrieve_float(self, backend: SQLiteBackend) -> None:
        pk = await backend.get_or_create_entity("sensor.temp", "sensor", TS)
        obs = _obs(pk, "state", "float", value_float=20.5)
        await backend.insert_observation(obs)

        latest = await backend.get_latest_observation(pk, "state")
        assert latest is not None
        assert latest.value_type == "float"
        assert latest.value_float == 20.5

    async def test_insert_and_retrieve_str(self, backend: SQLiteBackend) -> None:
        pk = await backend.get_or_create_entity("sensor.temp", "sensor", TS)
        obs = _obs(pk, "unit_of_measurement", "str", value_str="°C")
        await backend.insert_observation(obs)

        latest = await backend.get_latest_observation(pk, "unit_of_measurement")
        assert latest.value_str == "°C"

    async def test_update_last_seen(self, backend: SQLiteBackend) -> None:
        pk = await backend.get_or_create_entity("sensor.temp", "sensor", TS)
        obs = _obs(pk, "state", "float", value_float=20.5)
        await backend.insert_observation(obs)

        new_ts = TS + timedelta(minutes=5)
        await backend.update_last_seen(obs.id, new_ts)

        latest = await backend.get_latest_observation(pk, "state")
        assert latest.first_seen == TS
        assert latest.last_seen == new_ts

    async def test_different_fields_are_independent(self, backend: SQLiteBackend) -> None:
        pk = await backend.get_or_create_entity("sensor.temp", "sensor", TS)
        await backend.insert_observation(_obs(pk, "state", "float", value_float=20.5))
        await backend.insert_observation(_obs(pk, "unit_of_measurement", "str", value_str="°C"))

        state = await backend.get_latest_observation(pk, "state")
        unit = await backend.get_latest_observation(pk, "unit_of_measurement")

        assert state.value_float == 20.5
        assert unit.value_str == "°C"

    async def test_get_latest_returns_most_recent(self, backend: SQLiteBackend) -> None:
        pk = await backend.get_or_create_entity("sensor.temp", "sensor", TS)
        obs1 = ObservationRecord(
            id=uuid7(), entity_pk=pk, field_name="state", value_type="float",
            value_float=20.0, first_seen=TS, last_seen=TS,
        )
        obs2 = ObservationRecord(
            id=uuid7(), entity_pk=pk, field_name="state", value_type="float",
            value_float=21.0,
            first_seen=TS + timedelta(minutes=1),
            last_seen=TS + timedelta(minutes=1),
        )
        await backend.insert_observation(obs1)
        await backend.insert_observation(obs2)

        latest = await backend.get_latest_observation(pk, "state")
        assert latest.value_float == 21.0


class TestDeduplicationLogic:
    """Test the deduplication pattern used by LoggerCoordinator._record_field."""

    async def _record(
        self, backend: SQLiteBackend, entity_pk, field: str, raw_value, ts: datetime
    ) -> None:
        serialized = serialize(raw_value)
        latest = await backend.get_latest_observation(entity_pk, field)
        if latest is not None:
            latest_dict = {
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
                await backend.update_last_seen(latest.id, ts)
                return
        obs = ObservationRecord(
            id=uuid7(),
            entity_pk=entity_pk,
            field_name=field,
            value_type=serialized["value_type"],
            value_float=serialized.get("value_float"),
            value_str=serialized.get("value_str"),
            first_seen=ts,
            last_seen=ts,
        )
        await backend.insert_observation(obs)

    async def test_repeated_value_extends_last_seen(self, backend: SQLiteBackend) -> None:
        pk = await backend.get_or_create_entity("sensor.temp", "sensor", TS)
        t0, t1, t2 = TS, TS + timedelta(minutes=1), TS + timedelta(minutes=2)

        await self._record(backend, pk, "state", 20.0, t0)
        await self._record(backend, pk, "state", 20.0, t1)
        await self._record(backend, pk, "state", 20.0, t2)

        latest = await backend.get_latest_observation(pk, "state")
        assert latest.first_seen == t0   # original first_seen preserved
        assert latest.last_seen == t2    # last_seen updated

    async def test_value_change_opens_new_interval(self, backend: SQLiteBackend) -> None:
        # 20 → 21 → 20 must produce 3 rows, not 2
        pk = await backend.get_or_create_entity("sensor.temp", "sensor", TS)
        t0 = TS
        t1 = TS + timedelta(minutes=1)
        t2 = TS + timedelta(minutes=2)

        await self._record(backend, pk, "state", 20.0, t0)
        await self._record(backend, pk, "state", 21.0, t1)
        await self._record(backend, pk, "state", 20.0, t2)

        # latest row is the second "20", opened at t2
        latest = await backend.get_latest_observation(pk, "state")
        assert latest.value_float == 20.0
        assert latest.first_seen == t2   # new interval, not the first one

    async def test_type_change_opens_new_interval(self, backend: SQLiteBackend) -> None:
        pk = await backend.get_or_create_entity("sensor.door", "binary_sensor", TS)
        t0 = TS
        t1 = TS + timedelta(seconds=10)

        await self._record(backend, pk, "state", "on", t0)
        await self._record(backend, pk, "state", "off", t1)

        latest = await backend.get_latest_observation(pk, "state")
        assert latest.value_str == "off"
        assert latest.first_seen == t1
