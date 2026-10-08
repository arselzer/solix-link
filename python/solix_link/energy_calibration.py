"""Sanitized offline counter/reference observations; never certify physical units."""

from __future__ import annotations

from copy import deepcopy
import math
import re
import statistics

from .energy_values import ENERGY_CHANNELS, validate_native_energy
from .original_counters import COUNTERS, validate_original_counters

MODELS = ("c1000", "c1000_gen2", "c2000_gen2")
GEN2_COUNTERS = tuple(f"{group}.{channel}_energy_raw" for group in ("standard", "time_of_use")
                      for channel in ENERGY_CHANNELS)
BOUNDARIES = ("none", "device_restart", "meter_reset", "mode_change", "unknown")
MODES = ("standard", "time_of_use", "unknown")
OBSERVATION_KEYS = {"captured_at", "reported_at", "event_timestamp", "firmware_version", "raw",
                    "counter_epoch", "counter_epoch_started_at", "mode", "reference_kwh",
                    "reference_timestamp", "boundary"}


def number(value: object, low=0, high=253402300799) -> bool:
    try:
        return type(value) in (int, float) and low <= value <= high and math.isfinite(value)
    except OverflowError:
        return False


def new_session(model: str, counter: str, reference: str, *, now: float) -> dict:
    result = {"schema_version": 1, "model": model, "counter": counter,
              "reference_location": reference, "created_at": now, "observations": []}
    return validate_session(result)


def validate_session(value: object) -> dict:
    if (type(value) is not dict or set(value) != {"schema_version", "model", "counter",
            "reference_location", "created_at", "observations"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["model"] not in MODELS
            or value["counter"] not in (COUNTERS if value["model"] == "c1000" else GEN2_COUNTERS)
            or value["reference_location"] not in ("ac_input", "ac_output")
            or not number(value["created_at"]) or type(value["observations"]) is not list
            or len(value["observations"]) > 256):
        raise ValueError("Invalid energy calibration session")
    previous = value["created_at"] - 1
    for item in value["observations"]:
        if (type(item) is not dict or set(item) != OBSERVATION_KEYS
                or any(not number(item[k]) for k in ("captured_at", "reported_at", "reference_timestamp"))
                or item["captured_at"] <= previous or item["captured_at"] < value["created_at"]
                or not -5 <= item["captured_at"] - item["reported_at"] < 30
                or item["event_timestamp"] is not None and not number(item["event_timestamp"])
                or type(item["firmware_version"]) is not str
                or not re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,3}){1,5}", item["firmware_version"])
                or type(item["raw"]) is not int or not 0 <= item["raw"] < 2**64
                or item["counter_epoch"] is not None and (type(item["counter_epoch"]) is not int or item["counter_epoch"] < 1)
                or item["counter_epoch_started_at"] is not None and not number(item["counter_epoch_started_at"])
                or item["mode"] not in MODES or item["boundary"] not in BOUNDARIES
                or not number(item["reference_kwh"], 0, 1e9)):
            raise ValueError("Invalid energy calibration observation")
        previous = item["captured_at"]
    return deepcopy(value)


def append_observation(session: dict, snapshot: dict, *, meter_kwh: float,
                       meter_timestamp: float, now: float, boundary: str = "none") -> dict:
    session = validate_session(session)
    if (type(snapshot) is not dict or snapshot.get("model") != session["model"]
            or snapshot.get("protocol") != "native_mqtt" or snapshot.get("available") is not True
            or snapshot.get("connected") is not True or not number(now)
            or not number(snapshot.get("last_seen_timestamp"))
            or not -5 <= now - snapshot["last_seen_timestamp"] < 30):
        raise ValueError("A fresh matching native MQTT snapshot is required")
    model = session["model"]
    metrics = snapshot.get("metrics", {})
    epoch = started = None
    if model == "c1000":
        report = validate_original_counters(snapshot.get("original_counters"), model=model, protocol="native_mqtt", now=now)
        if report is None or report["batch_order_unknown"]:
            raise ValueError("One fresh original counter event is required")
        raw = report["reports"][0]["counters"].get(session["counter"])
        stamp = report["reports"][0]["event_timestamp"]
    else:
        report = validate_native_energy(snapshot.get("native_energy"), model=model, now=now)
        if report is None or report["batch_reports"] != 1 or report["continuity"] == "batch_order_unknown":
            raise ValueError("One fresh Gen 2 counter event is required")
        group, counter = session["counter"].split(".")
        raw = report["groups"].get(group, {}).get("raw", {}).get(counter)
        stamp = report["event_timestamp"]
        epoch, started = report["counter_epoch"], report["counter_epoch_started_at"]
    if (raw is None or not -5 <= now - report["reported_at"] < 30
            or session["observations"] and report["reported_at"] <= session["observations"][-1]["reported_at"]):
        raise ValueError("Wait for a new counter upload less than 30 seconds old")
    version = metrics.get("software_version")
    if report.get("firmware_version") != version:
        raise ValueError("Counter and telemetry firmware must agree")
    mode = metrics.get("usage_mode")
    if mode not in MODES:
        mode = "unknown"
    item = {"captured_at": now, "reported_at": report["reported_at"], "event_timestamp": stamp,
            "firmware_version": version, "raw": raw, "counter_epoch": epoch,
            "counter_epoch_started_at": started, "mode": mode,
            "reference_kwh": meter_kwh, "reference_timestamp": meter_timestamp, "boundary": boundary}
    session["observations"].append(item)
    return validate_session(session)


def calibration_report(session: dict, *, max_report_gap: int = 3600, max_timing_offset: int = 30,
                       candidate_wh_per_unit: float | None = None) -> dict:
    session = validate_session(session)
    if (type(max_report_gap) is not int or not 1 <= max_report_gap <= 86400
            or type(max_timing_offset) is not int or not 0 <= max_timing_offset <= 300
            or candidate_wh_per_unit is not None and not number(candidate_wh_per_unit, 1e-12, 1e9)):
        raise ValueError("Invalid energy comparison limits")
    intervals, ratios = [], []
    for index, (before, after) in enumerate(zip(session["observations"], session["observations"][1:]), 1):
        reasons = []
        if after["boundary"] != "none":
            reasons.append("explicit_" + after["boundary"])
        if before["firmware_version"] != after["firmware_version"]:
            reasons.append("firmware_changed")
        if any(before[k] != after[k] for k in ("counter_epoch", "counter_epoch_started_at")):
            reasons.append("counter_epoch_changed")
        if before["mode"] != after["mode"]:
            reasons.append("observed_mode_changed")
        if session["model"] != "c1000" and any(p["mode"] != session["counter"].split(".")[0] for p in (before, after)):
            reasons.append("counter_mode_not_active")
        if after["raw"] < before["raw"]:
            reasons.append("counter_decreased_reset_wrap_or_reorder")
        elapsed = after["reported_at"] - before["reported_at"]
        if not 0 < elapsed <= max_report_gap:
            reasons.append("report_gap_or_clock_regression")
        if any(p["event_timestamp"] is None for p in (before, after)):
            reasons.append("event_timestamp_missing")
        elif after["event_timestamp"] <= before["event_timestamp"]:
            reasons.append("event_clock_nonincreasing")
        if any(abs(p["reference_timestamp"] - p["reported_at"]) > max_timing_offset
               or p["event_timestamp"] is not None and abs(p["event_timestamp"] - p["reported_at"]) > max_timing_offset
               for p in (before, after)):
            reasons.append("endpoint_timing_mismatch")
        if after["reference_timestamp"] <= before["reference_timestamp"]:
            reasons.append("reference_clock_nonincreasing")
        if after["reference_kwh"] < before["reference_kwh"]:
            reasons.append("reference_meter_decreased")
        raw = after["raw"] - before["raw"]
        wh = (after["reference_kwh"] - before["reference_kwh"]) * 1000
        usable = not reasons and raw > 0 and wh > 0
        ratio = wh / raw if usable else None
        if ratio is not None:
            ratios.append(ratio)
        item = {"ending_observation_index": index, "boundary_reasons": sorted(set(reasons)),
                "comparison_available": not reasons, "raw_delta": raw if not reasons else None,
                "reference_energy_wh": wh if not reasons else None, "candidate_wh_per_raw_unit": ratio}
        if candidate_wh_per_unit is not None:
            item["tested_candidate"] = {"wh_per_unit": candidate_wh_per_unit,
                "difference_to_reference_wh": raw * candidate_wh_per_unit - wh if not reasons else None}
        intervals.append(item)
    return {"schema_version": 1, "model": session["model"], "counter": session["counter"],
            "reference_location": session["reference_location"], "observations": len(session["observations"]),
            "intervals": intervals, "usable_ratio_intervals": len(ratios),
            "candidate_scale": {"median_wh_per_raw_unit": statistics.median(ratios),
                                "minimum": min(ratios), "maximum": max(ratios)} if ratios else None,
            "physical_units_verified": False, "channel_mapping_verified": False,
            "mcu_snapshot_timing_verified": False, "mode_path_verified": False,
            "restart_retention_verified": False, "battery_energy_estimate": False,
            "lifetime_total_available": False, "changes_runtime_conversions": False}
