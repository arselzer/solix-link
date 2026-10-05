"""Bounded saved TOU readback, independent of general telemetry freshness."""

from __future__ import annotations

import math
import time

from .protocol import Model
from .tou import TouPeriod, periods_from_d9, validate_periods


def validate_plan_readback(value: object) -> dict | None:
    """Return a detached public value; malformed/unknown plans are unavailable."""
    if (type(value) is not dict or set(value) != {
            "schema_version", "enabled", "periods", "reported_at", "source"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or type(value["enabled"]) is not bool or value["source"] != "status_d9"
            or type(value["reported_at"]) not in (int, float)
            or not 0 <= value["reported_at"] <= 253402300799
            or not math.isfinite(value["reported_at"])
            or type(value["periods"]) is not list or len(value["periods"]) > 6):
        return None
    try:
        periods = []
        for item in value["periods"]:
            if type(item) is not dict or set(item) != {"tariff", "start_hour", "end_hour"}:
                return None
            periods.append(TouPeriod(**item))
        validate_periods(periods)
        if value["enabled"] and not periods:
            return None
    except (ValueError, TypeError):
        return None
    return {"schema_version": 1, "enabled": value["enabled"],
            "periods": [period.to_dict() for period in periods],
            "reported_at": value["reported_at"], "source": "status_d9"}


def plan_from_d9(value: bytes, model: Model, reported_at: float) -> dict | None:
    """Accept the exact established Gen 2 D9 shape, including its tail length."""
    if model not in (Model.C1000_GEN2, Model.C2000_GEN2):
        return None
    try:
        periods = periods_from_d9(value)
        if value[2] not in (0, 1):
            return None
        return validate_plan_readback({"schema_version": 1, "enabled": bool(value[2]),
            "periods": [period.to_dict() for period in periods],
            "reported_at": reported_at, "source": "status_d9"})
    except (ValueError, TypeError, IndexError):
        return None


def plan_is_fresh(snapshot: dict, *, now: float | None = None) -> bool:
    """Other status tags cannot refresh a saved plan's timestamp."""
    plan = validate_plan_readback(snapshot.get("tou_plan_readback"))
    now = time.time() if now is None else now
    if (plan is None or snapshot.get("model") not in ("c1000_gen2", "c2000_gen2")
            or snapshot.get("protocol") != "native_mqtt"
            or snapshot.get("connected") is not True or snapshot.get("available") is not True
            or type(now) not in (int, float) or not 0 <= now <= 253402300799 or not math.isfinite(now)):
        return False
    seen = snapshot.get("last_seen_timestamp")
    return (type(seen) in (int, float) and math.isfinite(seen)
            and -5 <= now - seen < 30 and -5 <= now - plan["reported_at"] < 30)
