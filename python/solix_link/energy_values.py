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
METER_REASONS = ("none", "counter_decreased", "batch_order_unknown", "clock_conflict", "timestamp_missing",
                 "timestamp_outside_window", "coverage_changed", "firmware_changed", "report_gap",
                 "implausible_delta", "total_overflow")


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


def validate_energy_meter(value: object, *, model: str, firmware: str | None,
                          reported_at: float, now: float) -> dict | None:
    """Whitelist persisted observed deltas and recompute their nominal kWh.

    This is a gateway meter generation, not the device's boot/reset epoch.
    Only Standard AC on A1763 1.1.4.9 has the current scaling qualification.
    """
    if (type(value) is not dict or model != "c1000_gen2"
            or type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or type(value.get("generation")) is not str or not re.fullmatch(r"[0-9a-f]{32}", value["generation"])
            or value.get("status") not in ("tracking", "quarantined") or value.get("reason") not in METER_REASONS
            or (value["status"] == "tracking") != (value["reason"] == "none")
            or value["status"] == "tracking" and firmware != "1.1.4.9"
            or any(not _timestamp(value.get(key)) for key in ("started_at", "last_event_timestamp", "last_receipt_at"))
            or not value["started_at"] <= value["last_receipt_at"] <= reported_at
            or not -5 <= value["last_receipt_at"] - value["last_event_timestamp"] < MAX_REPORT_AGE
            or type(value.get("accepted_reports")) is not int or not 1 <= value["accepted_reports"] <= MAX_INTEGER
            or type(value.get("rejected_reports")) is not int or not 0 <= value["rejected_reports"] <= MAX_INTEGER):
        return None
    channels = value.get("channels")
    if type(channels) is not dict or set(channels) != {"ac_input", "ac_output"}:
        return None
    clean = {}
    for channel, counters in channels.items():
        if (type(counters) is not dict or type(counters.get("counter_raw")) is not int
                or not 0 <= counters["counter_raw"] < 2**32 or type(counters.get("total_raw")) is not int
                or not 0 <= counters["total_raw"] <= MAX_INTEGER):
            return None
        clean[channel] = {key: counters[key] for key in ("counter_raw", "total_raw")}
    result = {key: value[key] for key in ("schema_version", "generation", "started_at", "last_event_timestamp",
        "last_receipt_at", "status", "reason", "accepted_reports", "rejected_reports")}
    result.update(channels=clean, energy_kwh={key: row["total_raw"] / 1000 for key, row in clean.items()},
                  conversion_basis="nominal_wh", estimated=True, units_verified=False,
                  includes_bypass=True, group="standard", max_report_age_seconds=MAX_REPORT_AGE)
    result["available"] = (value["status"] == "tracking" and _timestamp(now)
        and -5 <= now - value["last_receipt_at"] < MAX_REPORT_AGE
        and -5 <= now - value["last_event_timestamp"] < MAX_REPORT_AGE)
    return result


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
    timestamp = value.get("event_timestamp")
    if timestamp is not None and not _timestamp(timestamp):
        return None
    result["event_timestamp"] = timestamp
    result["event_timestamp_basis"] = "radio_request_construction"
    result["conversion_basis"] = "nominal_wh" if value["model"] == "c1000_gen2" and firmware == "1.1.4.9" else "assumed_wh"
    result["groups"] = {key: {"raw": counters, "energy_kwh": {
        channel: counters[f"{channel}_energy_raw"] / 1000 for channel in ENERGY_CHANNELS
        if f"{channel}_energy_raw" in counters}} for key, counters in raw.items()}
    clock = time.time() if now is None else now
    result["available"] = _timestamp(clock) and -5 <= clock - result["reported_at"] < MAX_REPORT_AGE
    result["max_report_age_seconds"] = MAX_REPORT_AGE
    if "meter" in value:
        meter = validate_energy_meter(value["meter"], model=value["model"], firmware=firmware,
                                      reported_at=value["reported_at"], now=clock)
        if meter is None:
            return None
        result["meter"] = meter
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
    meter = report.get("meter")
    if meter is not None:
        for channel, counters in meter["channels"].items():
            rows.append(("Observed Standard", f"{channel.replace('_', ' ')} estimate ({meter['status']})",
                         str(counters["total_raw"]), f"{meter['energy_kwh'][channel]:.6f}"))
    return rows
