"""Public, bounded native energy values. No device requests or group aggregation.

Wire counters are not lifetime meters. kWh values are nominal conversions only;
physical units, reset semantics and C2000 scaling remain unverified.
"""

from __future__ import annotations

import math
import re
import time

GROUP_NAMES = ("time_of_use", "standard", "backup_variant_1", "backup_variant_2")
ENERGY_CHANNELS = ("ac_input", "ac_output", "dc_input", "other_output")
RAW_KEYS = tuple(f"{key}_energy_raw" for key in ENERGY_CHANNELS) + (
    "ac_output_duration_raw", "ac_charge_duration_raw", "other_output_duration_raw", "dc_charge_duration_raw")
MAX_REPORT_AGE = 1800
MAX_INTEGER = 2**53 - 1
SOURCE = "device_energy_report"


def _timestamp(value: object) -> bool:
    return type(value) in (int, float) and 0 < value < 253402300800 and math.isfinite(value)


def energy_groups(value: object) -> dict | None:
    """Whitelist recovered integer fields; absent channels stay absent."""
    if type(value) is not dict or not 1 <= len(value) <= 4:
        return None
    groups = {}
    for group, raw in value.items():
        if group not in GROUP_NAMES or type(raw) is not dict:
            return None
        counters = {key: number for key, number in raw.items() if key in RAW_KEYS}
        if any(type(number) is not int or not 0 <= number <= MAX_INTEGER
                               for number in counters.values()):
            return None
        groups[group] = counters
    return groups if any(groups.values()) else None


def validate_native_energy(value: object, *, model: str | None = None,
                           now: float | None = None) -> dict | None:
    """Rebuild the public envelope, including derived kWh and independent freshness.

    Never trust incoming conversions, availability, arbitrary nested keys or a
    claim of calibrated units. This function also validates persisted state.
    """
    if type(value) is not dict:
        return None
    if (type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or value.get("source") != SOURCE or value.get("units_verified") is not False
            or value.get("model") not in ("c1000_gen2", "c2000_gen2")
            or model is not None and value["model"] != model
            or not _timestamp(value.get("reported_at"))
            or not _timestamp(value.get("counter_epoch_started_at"))
            or value["counter_epoch_started_at"] > value["reported_at"]
            or value.get("continuity") not in ("first_report", "increasing", "counter_decreased", "batch_order_unknown")
            or any(type(value.get(key)) is not int or not 1 <= value[key] <= MAX_INTEGER
                   for key in ("received_reports", "counter_epoch", "batch_reports"))
            or value["batch_reports"] > 32 or value["batch_reports"] > value["received_reports"]):
        return None
    firmware = value.get("firmware_version")
    if firmware is not None and (type(firmware) is not str or not re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,3}){1,5}", firmware)):
        return None
    raw_groups = value.get("groups")
    if type(raw_groups) is not dict:
        return None
    raw = energy_groups({key: group.get("raw") if type(group) is dict else None
                         for key, group in raw_groups.items()})
    if raw is None:
        return None
    result = {key: value[key] for key in ("schema_version", "source", "model", "units_verified", "reported_at",
        "counter_epoch_started_at", "counter_epoch", "received_reports", "batch_reports", "continuity")}
    result["firmware_version"] = firmware
    result["conversion_basis"] = "nominal_wh" if value["model"] == "c1000_gen2" and firmware == "1.1.4.9" else "assumed_wh"
    result["groups"] = {key: {"raw": counters, "energy_kwh": {
        channel: counters[f"{channel}_energy_raw"] / 1000 for channel in ENERGY_CHANNELS
        if f"{channel}_energy_raw" in counters}} for key, counters in raw.items()}
    clock = time.time() if now is None else now
    result["available"] = _timestamp(clock) and -5 <= clock - result["reported_at"] < MAX_REPORT_AGE
    result["max_report_age_seconds"] = MAX_REPORT_AGE
    return result


def native_energy_rows(value: object, *, now: float | None = None) -> list[tuple[str, str, str, str]]:
    """Presentation rows shared by CLI and terminal UI, including raw durations."""
    report = validate_native_energy(value, now=now)
    if report is None:
        return []
    rows = []
    for group in GROUP_NAMES:
        if group not in report["groups"]:
            continue
        values = report["groups"][group]
        for key in RAW_KEYS:
            if key not in values["raw"]:
                continue
            channel = key.removesuffix("_energy_raw")
            rows.append((group.replace("_", " ").title(), key.removesuffix("_raw").replace("_", " "),
                         str(values["raw"][key]), f"{values['energy_kwh'][channel]:.6f}" if channel in ENERGY_CHANNELS else "—"))
    return rows
