"""Persist the last accepted native meter epoch before publishing HA statistics."""

from __future__ import annotations

from copy import deepcopy
import math
import re

from .native_energy import MAX_INTEGER, validate_native_energy

QUALIFIED = {"c1000_gen2": "1.1.4.9", "c2000_gen2": "2.1.6.4"}
CHANNELS = ("ac_input", "ac_output")
KEYS = {"model", "firmware_version", "generation", "started_at", "last_event_timestamp",
        "last_receipt_at", "accepted_reports", "channels"}
REASONS = {"none", "storage_unavailable", "storage_error", "meter_unavailable", "meter_quarantined",
           "source_changed", "epoch_regressed", "counter_regressed", "clock_regressed", "station_limit"}


def validate_record(value: object) -> dict:
    if (type(value) is not dict or set(value) != KEYS
            or type(value["model"]) is not str or value["model"] not in QUALIFIED
            or value["firmware_version"] != QUALIFIED[value["model"]]
            or type(value["generation"]) is not str or not re.fullmatch(r"[0-9a-f]{32}", value["generation"])
            or any(type(value[k]) not in (int, float) or not 0 < value[k] < 253402300800
                   or not math.isfinite(value[k]) for k in ("started_at", "last_event_timestamp", "last_receipt_at"))
            or value["started_at"] > value["last_receipt_at"]
            or not -5 <= value["last_receipt_at"] - value["last_event_timestamp"] < 1800
            or type(value["accepted_reports"]) is not int or not 1 <= value["accepted_reports"] <= MAX_INTEGER
            or type(value["channels"]) is not dict or set(value["channels"]) != set(CHANNELS)):
        raise ValueError("Invalid native energy continuity")
    for row in value["channels"].values():
        if (type(row) is not dict or set(row) != {"counter_raw", "total_raw"}
                or type(row["counter_raw"]) is not int or not 0 <= row["counter_raw"] < 2**32
                or type(row["total_raw"]) is not int or not 0 <= row["total_raw"] <= MAX_INTEGER):
            raise ValueError("Invalid native energy continuity")
    return deepcopy(value)


def validate_store(value: object) -> dict:
    if value is None:
        return {}
    if (type(value) is not dict or set(value) != {"stations"} or type(value["stations"]) is not dict
            or len(value["stations"]) > 32
            or any(type(k) is not str or not 0 < len(k) <= 64 or not k.isprintable()
                   for k in value["stations"])):
        raise ValueError("Invalid native energy continuity store")
    return {name: validate_record(record) for name, record in value["stations"].items()}


def record_from_snapshot(snapshot: dict, *, now: float) -> dict | None:
    if snapshot.get("protocol") != "native_mqtt":
        return None
    energy = validate_native_energy(snapshot.get("native_energy"), model=snapshot.get("model"), now=now)
    meter = energy.get("meter") if energy else None
    if not meter or not meter["available"]:
        return None
    return validate_record({"model": energy["model"], "firmware_version": energy["firmware_version"],
                            **{key: meter[key] for key in KEYS - {"model", "firmware_version"}}})


def continuity_reason(previous: dict | None, current: dict) -> str:
    """Do not accept a restored old generation as a new statistics reset."""
    current = validate_record(current)
    if previous is None:
        return "none"
    previous = validate_record(previous)
    if any(previous[k] != current[k] for k in ("model", "firmware_version")):
        return "source_changed"
    if previous["generation"] != current["generation"]:
        return "none" if (current["started_at"] > max(previous["last_receipt_at"], previous["last_event_timestamp"])
                          and current["last_event_timestamp"] > previous["last_event_timestamp"]) else "epoch_regressed"
    if previous["started_at"] != current["started_at"]:
        return "epoch_regressed"
    if any(current[k] < previous[k] for k in ("last_receipt_at", "last_event_timestamp", "accepted_reports")):
        return "clock_regressed"
    if any(current["channels"][c][k] < previous["channels"][c][k]
           for c in CHANNELS for k in ("counter_raw", "total_raw")):
        return "counter_regressed"
    return "none"
