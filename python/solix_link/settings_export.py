"""Sanitized partial preference export; never a restorable device backup."""

from __future__ import annotations

import math
import re
import time

from .plan_readback import plan_is_fresh, validate_plan_readback

COMMON = {
    "ac_charging_power_limit_w", "display_timeout_seconds", "ac_fast_charge_enabled",
    "temperature_unit_fahrenheit", "ac_power_saving_mode_enabled", "dc_power_saving_mode_enabled",
}
FIELDS = {
    "c300": COMMON | {"light_mode"},
    "c1000": COMMON | {"device_timeout_minutes", "display_brightness", "light_mode"},
    "c1000_gen2": COMMON | {"device_timeout_minutes", "display_brightness", "port_memory_enabled",
        "max_charge_percentage", "min_charge_percentage", "backup_reserve_percentage",
        "ac_off_grid_alert_enabled", "clock_screen_first_brightness_flag_raw",
        "clock_screen_second_brightness_flag_raw"},
    "c2000_gen2": {"ac_charging_power_limit_w", "max_charge_percentage",
        "min_charge_percentage", "backup_reserve_percentage"},
}


def _valid(key: str, value: object, model: str) -> bool:
    if type(value) is not int:
        return False
    if key.endswith("_enabled") or key.endswith("_flag_raw") or key == "temperature_unit_fahrenheit":
        return value in (0, 1)
    if key == "ac_charging_power_limit_w":
        if model == "c300":
            return value in (100, 200, 300, 330)
        minimum, maximum = {"c1000": (100, 1000), "c1000_gen2": (100, 1200),
                            "c2000_gen2": (300, 1800)}[model]
        return minimum <= value <= maximum and value % 100 == 0
    if key == "device_timeout_minutes":
        return value in (0, 30, 60, 120, 240, 360, 720, 1440)
    if key == "display_timeout_seconds":
        return value in (0, 10, 20, 30, 60, 300, 1800)
    if key == "display_brightness":
        return value in (1, 2, 3)
    if key == "light_mode":
        return value in (0, 1, 2, 3, 4) if model == "c1000" else value in (0, 1, 2, 3)
    if key == "max_charge_percentage":
        return value in (80, 85, 90, 95, 100)
    if key == "min_charge_percentage":
        return value == 1 or 5 <= value <= 75 and value % 5 == 0
    if key == "backup_reserve_percentage":
        return 5 <= value <= 100 and value % 5 == 0
    return False


def valid_preference(key: str, value: object, model: str) -> bool:
    """Validate a known scalar shape without asserting transport support."""
    return type(model) is str and model in FIELDS and _valid(key, value, model)


def export_settings(snapshot: object, *, now: float | None = None) -> dict:
    """Copy only known preference fields; omit names, IDs, outputs and raw bytes.

    General telemetry age is not proof of each cached field's freshness. Even
    fresh exports remain incomplete and must not be imported as full backups.
    """
    now = time.time() if now is None else now
    station = snapshot if type(snapshot) is dict else {}
    model = station.get("model")
    model = model if type(model) is str and model in FIELDS else None
    protocol = station.get("protocol")
    protocol = protocol if protocol in ("legacy", "prime", "native_mqtt") else None
    metrics = station.get("metrics")
    metrics = metrics if type(metrics) is dict else {}
    expected = FIELDS.get(model, set())
    settings = {key: metrics[key] for key in sorted(expected)
                if key in metrics and _valid(key, metrics[key], model)}
    seen = station.get("last_seen_timestamp")
    clock_valid = type(now) in (int, float) and 0 <= now <= 253402300799 and math.isfinite(now)
    seen_valid = type(seen) in (int, float) and 0 <= seen <= 253402300799 and math.isfinite(seen)
    age = now - seen if clock_valid and seen_valid else None
    fresh = (station.get("connected") is True and station.get("available") is True
             and age is not None and -5 <= age < (30 if protocol == "native_mqtt" else 90))
    plan = validate_plan_readback(station.get("tou_plan_readback")) if model in (
        "c1000_gen2", "c2000_gen2") and protocol == "native_mqtt" else None
    firmware = metrics.get("software_version")
    firmware = firmware if type(firmware) is str and len(firmware) <= 24 and re.fullmatch(
        r"\d{1,3}(?:\.\d{1,3}){1,4}", firmware, flags=re.ASCII) else None
    return {
        "schema_version": 1, "complete": False, "restore_supported": False,
        "source": "cached_telemetry", "model": model, "protocol": protocol, "firmware_version": firmware,
        "snapshot_fresh": bool(fresh), "telemetry_age_seconds": round(age, 3) if age is not None else None,
        "field_freshness_verified": False, "settings": settings,
        "missing_or_invalid_fields": sorted(expected - settings.keys()),
        "tou_plan_readback": plan, "tou_plan_fresh": bool(plan and plan_is_fresh(station, now=now)),
        "omitted_sections": ["identity_and_credentials", "runtime_output_states", "unreported_settings",
            "hidden_clock_and_automatic_backup_state"],
    }
