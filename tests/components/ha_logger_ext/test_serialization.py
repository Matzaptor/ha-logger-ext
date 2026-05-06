from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from custom_components.ha_logger_ext.storage.serialization import serialize, values_equal


class TestSerialize:
    def test_none(self):
        assert serialize(None) == {"value_type": "null"}

    def test_bool_true(self):
        assert serialize(True) == {"value_type": "bool", "value_bool": 1}

    def test_bool_false(self):
        assert serialize(False) == {"value_type": "bool", "value_bool": 0}

    def test_bool_not_serialized_as_int(self):
        # bool is a subclass of int; must be caught before int
        assert serialize(True)["value_type"] == "bool"
        assert serialize(1)["value_type"] == "int"

    def test_int(self):
        assert serialize(42) == {"value_type": "int", "value_int": 42}

    def test_int_negative(self):
        assert serialize(-5) == {"value_type": "int", "value_int": -5}

    def test_float(self):
        assert serialize(3.14) == {"value_type": "float", "value_float": 3.14}

    def test_float_nan_becomes_null(self):
        assert serialize(float("nan")) == {"value_type": "null"}

    def test_float_inf_becomes_null(self):
        assert serialize(float("inf")) == {"value_type": "null"}
        assert serialize(float("-inf")) == {"value_type": "null"}

    def test_str(self):
        assert serialize("hello") == {"value_type": "str", "value_str": "hello"}

    def test_str_empty(self):
        assert serialize("") == {"value_type": "str", "value_str": ""}

    def test_datetime_aware(self):
        dt = datetime(2026, 5, 6, 10, 0, 0, tzinfo=timezone.utc)
        result = serialize(dt)
        assert result["value_type"] == "datetime"
        assert "2026-05-06T10:00:00" in result["value_datetime"]
        assert "+00:00" in result["value_datetime"]

    def test_datetime_naive_assumed_utc(self):
        dt = datetime(2026, 5, 6, 10, 0, 0)
        result = serialize(dt)
        assert result["value_type"] == "datetime"
        assert "+00:00" in result["value_datetime"]

    def test_date(self):
        assert serialize(date(2026, 5, 6)) == {
            "value_type": "date",
            "value_date": "2026-05-06",
        }

    def test_time(self):
        result = serialize(time(10, 30, 0))
        assert result["value_type"] == "time"
        assert result["value_time"] == "10:30:00"

    def test_timedelta_stored_as_float_seconds(self):
        result = serialize(timedelta(hours=1, seconds=30))
        assert result["value_type"] == "timedelta"
        assert result["value_float"] == 3630.0

    def test_list(self):
        result = serialize([1, 2, 3])
        assert result["value_type"] == "json"
        assert "1" in result["value_json"]

    def test_dict(self):
        result = serialize({"a": 1})
        assert result["value_type"] == "json"
        assert '"a"' in result["value_json"]

    def test_unknown_type_becomes_json_string(self):
        class _Foo:
            def __str__(self) -> str:
                return "foo"

        result = serialize(_Foo())
        assert result["value_type"] == "json"
        assert "foo" in result["value_json"]


class TestValuesEqual:
    def test_both_null(self):
        assert values_equal({"value_type": "null"}, {"value_type": "null"})

    def test_same_str(self):
        a = {"value_type": "str", "value_str": "hello"}
        assert values_equal(a, a.copy())

    def test_different_str(self):
        a = {"value_type": "str", "value_str": "hello"}
        b = {"value_type": "str", "value_str": "world"}
        assert not values_equal(a, b)

    def test_different_type_same_repr(self):
        a = {"value_type": "str", "value_str": "1"}
        b = {"value_type": "int", "value_int": 1}
        assert not values_equal(a, b)

    def test_same_int(self):
        a = {"value_type": "int", "value_int": 42}
        assert values_equal(a, a.copy())

    def test_same_float(self):
        a = {"value_type": "float", "value_float": 3.14}
        assert values_equal(a, a.copy())

    def test_timedelta_compared_via_value_float(self):
        a = {"value_type": "timedelta", "value_float": 3600.0}
        assert values_equal(a, a.copy())

    def test_timedelta_different(self):
        a = {"value_type": "timedelta", "value_float": 3600.0}
        b = {"value_type": "timedelta", "value_float": 7200.0}
        assert not values_equal(a, b)

    def test_same_bool(self):
        a = {"value_type": "bool", "value_bool": 1}
        assert values_equal(a, a.copy())

    def test_different_bool(self):
        a = {"value_type": "bool", "value_bool": 1}
        b = {"value_type": "bool", "value_bool": 0}
        assert not values_equal(a, b)
