from __future__ import annotations

import json
import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Any


def serialize(value: Any) -> dict[str, Any]:
    """Serialize a Python value to a dict with value_type and the active column."""
    match value:
        case None:
            return {"value_type": "null"}
        case bool():
            # bool must come before int: bool is a subclass of int in Python
            return {"value_type": "bool", "value_bool": int(value)}
        case int():
            return {"value_type": "int", "value_int": value}
        case float() if math.isnan(value) or math.isinf(value):
            # NaN and Inf are not representable in the DB; treat as null
            return {"value_type": "null"}
        case float():
            return {"value_type": "float", "value_float": value}
        case str():
            return {"value_type": "str", "value_str": value}
        case datetime():
            dt = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
            return {"value_type": "datetime", "value_datetime": dt.isoformat()}
        case date():
            return {"value_type": "date", "value_date": value.isoformat()}
        case time():
            return {"value_type": "time", "value_time": value.isoformat()}
        case timedelta():
            return {"value_type": "timedelta", "value_float": value.total_seconds()}
        case list() | dict():
            return {"value_type": "json", "value_json": json.dumps(value, default=str)}
        case _:
            return {"value_type": "json", "value_json": json.dumps(str(value))}


def values_equal(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Return True if two serialized observations represent the same value."""
    if a.get("value_type") != b.get("value_type"):
        return False
    col = _active_column(a["value_type"])
    if col is None:
        return True  # both null
    return a.get(col) == b.get(col)


def _active_column(value_type: str) -> str | None:
    return {
        "null": None,
        "str": "value_str",
        "int": "value_int",
        "float": "value_float",
        "bool": "value_bool",
        "datetime": "value_datetime",
        "date": "value_date",
        "time": "value_time",
        "timedelta": "value_float",  # stored as total seconds in value_float
        "json": "value_json",
    }.get(value_type)
