"""Validate optional cached history separately from live telemetry and controls."""

from __future__ import annotations

import math
import re
from pathlib import Path
import time
from typing import Any

HISTORY_POLL_SECONDS = 30
HISTORY_DISCOVERY_SECONDS = 300
MAX_HISTORY_AGE = 90
TOTAL_KEYS = (
    "ac_input_energy_kwh_estimate", "ac_output_energy_kwh_estimate",
    "ac_input_coverage_seconds", "ac_output_coverage_seconds", "gap_count",
)
_MODELS = {"c300", "c1000", "c1000_gen2", "c2000_gen2"}
_PROTOCOLS = {"legacy", "prime", "native_mqtt"}
_MAX_EPOCH = 253402300799


class HistoryValidationError(ValueError):
    """A fixed public error without rejected values or identifiers."""

    def __init__(self) -> None:
        super().__init__("Invalid gateway history summary")


def _number(value: Any, low: float, high: float) -> float:
    if type(value) not in (int, float) or not low <= value <= high:
        raise HistoryValidationError()
    if not math.isfinite(value):
        raise HistoryValidationError()
    return float(value)


def _timestamp(value: Any) -> float | None:
    return None if value is None else _number(value, 0, _MAX_EPOCH)


def _name(value: Any) -> bool:
    return (isinstance(value, str) and 1 <= len(value) <= 64
            and value.isprintable() and bool(value.strip()))


def _station(raw: Any, updated: float | None, max_power: float) -> dict:
    if (not isinstance(raw, dict) or not _name(raw.get("name"))
            or not {"collection_start", "last_seen_timestamp", "gap_open"} <= raw.keys()):
        raise HistoryValidationError()
    model, protocol = raw.get("model"), raw.get("protocol")
    if (not isinstance(model, str) or model not in _MODELS
            or not isinstance(protocol, str) or protocol not in _PROTOCOLS
            or type(raw.get("gap_open")) is not bool):
        raise HistoryValidationError()
    start, seen = _timestamp(raw.get("collection_start")), _timestamp(raw.get("last_seen_timestamp"))
    if (seen is not None and updated is not None and seen > updated + 5
            or start is not None and (seen is None or start > seen)):
        raise HistoryValidationError()
    raw_totals = raw.get("lifetime_totals")
    if not isinstance(raw_totals, dict):
        raise HistoryValidationError()
    totals = {key: _number(raw_totals.get(key), 0, 1e12) for key in TOTAL_KEYS[:-1]}
    gaps = raw_totals.get("gap_count")
    if type(gaps) is not int or not 0 <= gaps <= 2**53 - 1:
        raise HistoryValidationError()
    totals["gap_count"] = gaps
    if seen is None and any(totals.values()):
        raise HistoryValidationError()
    for channel in ("input", "output"):
        covered = totals[f"ac_{channel}_coverage_seconds"]
        energy = totals[f"ac_{channel}_energy_kwh_estimate"]
        if start is not None and covered > seen - start + 1e-6:
            raise HistoryValidationError()
        maximum = max_power * covered / 3600000
        if covered == 0 and energy != 0 or energy > maximum + max(1e-9, maximum * 1e-9):
            raise HistoryValidationError()
    result = {"name": raw["name"], "model": model, "protocol": protocol,
            "collection_start": start, "last_seen_timestamp": seen,
            "lifetime_totals": totals, "gap_open": raw["gap_open"]}
    if "generation" in raw:
        if type(raw["generation"]) is not str or not re.fullmatch(r"[0-9a-f]{32}", raw["generation"]):
            raise HistoryValidationError()
        result["generation"] = raw["generation"]
    return result


def parse_history_summary(raw: Any, *, now: float | None = None) -> dict:
    """Drop extra fields; isolate bad station records and duplicate names.

    A malformed envelope invalidates the history response. A malformed station
    invalidates that station only, including both copies of a duplicate name.
    """
    if (not isinstance(raw, dict) or type(raw.get("schema_version")) is not int
            or raw["schema_version"] != 1 or type(raw.get("enabled")) is not bool
            or raw.get("estimated") is not True or raw.get("read_only") is not True):
        raise HistoryValidationError()
    if not raw["enabled"]:
        return {"enabled": False, "stations": {}}
    if raw.get("recording") is not True or "updated_at" not in raw:
        raise HistoryValidationError()
    current = _number(time.time() if now is None else now, 0, _MAX_EPOCH)
    updated = _timestamp(raw.get("updated_at"))
    if updated is not None and not -5 <= current - updated <= MAX_HISTORY_AGE:
        raise HistoryValidationError()
    limits = raw.get("limits")
    stations = raw.get("stations")
    if not isinstance(limits, dict) or not isinstance(stations, list) or len(stations) > 32:
        raise HistoryValidationError()
    max_power = _number(limits.get("max_power_w"), 1, 10000)
    max_gap = _number(limits.get("max_gap_seconds"), 0.001, 60)
    result: dict[str, dict] = {}
    invalid_names = set()
    for item in stations:
        name = item.get("name") if isinstance(item, dict) else None
        if _name(name):
            if name in result or name in invalid_names:
                result.pop(name, None)
                invalid_names.add(name)
                continue
        try:
            parsed = _station(item, updated, max_power)
        except HistoryValidationError:
            if _name(name):
                invalid_names.add(name)
                result.pop(name, None)
            continue
        result[parsed["name"]] = parsed
    return {"enabled": True, "updated_at": updated, "stations": result,
            "max_gap_seconds": max_gap}


def history_available(history: Any, snapshot: dict, *, now: float | None = None) -> bool:
    """Freshness belongs to the recorder, independently of an offline station."""
    if not isinstance(history, dict):
        return False
    try:
        updated = _timestamp(history.get("updated_at"))
        current = _number(time.time() if now is None else now, 0, _MAX_EPOCH)
    except HistoryValidationError:
        return False
    return (updated is not None and -5 <= current - updated <= MAX_HISTORY_AGE
            and history.get("model") == snapshot.get("model")
            and history.get("protocol") == snapshot.get("protocol"))


def history_nonregressing(previous: dict | None, current: dict) -> bool:
    """Accept a new database only after the previous observation ended.

    A copied/rolled-back database must not establish a fresh statistics epoch.
    Legacy endpoints can still supply diagnostics without a generation.
    """
    if previous is None:
        return True
    if (previous["model"], previous["protocol"]) != (current["model"], current["protocol"]):
        return False
    before_generation, after_generation = previous.get("generation"), current.get("generation")
    if before_generation is not None:
        if after_generation is None:
            return False
        if before_generation != after_generation:
            return (current["collection_start"] is not None
                    and previous["last_seen_timestamp"] is not None
                    and current["collection_start"] > previous["last_seen_timestamp"]
                    and current.get("updated_at") is not None
                    and previous.get("updated_at") is not None
                    and current["updated_at"] >= previous["updated_at"])
        pending = (previous["collection_start"] is None and previous["last_seen_timestamp"] is None
                   and not any(previous["lifetime_totals"].values()))
        if previous["collection_start"] != current["collection_start"] and not pending:
            return False
    elif previous["collection_start"] != current["collection_start"]:
        return True
    for key in ("updated_at", "last_seen_timestamp"):
        before, after = previous.get(key), current.get(key)
        if before is not None and (after is None or after < before):
            return False
    return all(current["lifetime_totals"][key] >= previous["lifetime_totals"][key]
               for key in TOTAL_KEYS)


def parse_history_highwater(raw: Any) -> dict[str, dict]:
    """Validate persisted continuity without making old observations fresh."""
    if not isinstance(raw, dict) or set(raw) != {"stations"} or not isinstance(raw["stations"], list) or len(raw["stations"]) > 32:
        raise HistoryValidationError()
    result = {}
    for item in raw["stations"]:
        if not isinstance(item, dict):
            raise HistoryValidationError()
        updated = _timestamp(item.get("updated_at"))
        station = _station(item, updated, 10000)
        if station["name"] in result:
            raise HistoryValidationError()
        result[station["name"]] = {**station, "updated_at": updated}
    return result


def history_storage_evidence(path: str) -> bool:
    """HA can rename corrupt JSON and return None; that is not a fresh baseline."""
    target = Path(path)
    return target.exists() or any(target.parent.glob(target.name + ".corrupt.*"))
