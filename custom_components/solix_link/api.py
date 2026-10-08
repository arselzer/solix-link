"""Small async client for the local gateway; no Anker or Bluetooth access."""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
import time
import uuid
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import aiohttp

COMMANDS = frozenset({"set-charge-power", "set-charge-cap", "set-backup-reserve",
                      "set-tou-plan", "return-grid", "set-discharge-floor",
                      "set-temperature-unit", "set-off-grid-alert", "set-device-timeout",
                      "set-fast-charge", "set-ac-power-saving", "set-dc-power-saving",
                      "set-display-brightness", "set-display-timeout", "set-port-memory", "set-light", "set-clock-brightness"})
METRICS = frozenset({"battery_percentage", "temperature_c", "output_power_w",
                    "expansion_battery_count", "expansion_battery_percentage", "expansion_temperature_c",
                    "ac_input_power_w", "ac_output_power_w", "dc_output_power_w",
                    "dc_input_power_w", "solar_input_power_w", "input_power_w",
                    "usb_c1_power_w", "usb_c2_power_w", "usb_c3_power_w", "usb_a1_power_w", "usb_a2_power_w",
                    "time_remaining_minutes", "dc_output_timer_remaining_seconds",
                    "software_version_controller", "software_version_inverter", "software_version_bms", "software_version_module",
                    "ac_input_connected", "ac_output_enabled", "ac_output_timer_remaining_seconds", "dc_output_enabled", "battery_status",
                    "ac_output_timeout_seconds", "dc_output_timeout_seconds",
                    "ac_charging_power_limit_w", "max_charge_percentage",
                    "min_charge_percentage", "backup_reserve_percentage",
                    "active_tariff", "usage_mode", "tou_schedule_slot_count", "disaster_preparation_active",
                    "ac_fast_charge_enabled", "software_version",
                    "temperature_unit_fahrenheit", "ac_off_grid_alert_enabled", "device_timeout_minutes",
                    "ac_power_saving_mode_enabled", "dc_power_saving_mode_enabled",
                    "dc_input_active", "pv_weak_light_locked", "dc_input_power_raw", "controller_error_code",
                    "battery_health_raw", "display_brightness", "display_timeout_seconds", "port_memory_enabled", "light_mode",
                    "ac_output_frequency_setting_hz", "ac_frequency_raw", "clock_screen_enabled",
                    "clock_screen_transfer_status_raw", "clock_screen_first_brightness_flag_raw",
                    "clock_screen_second_brightness_flag_raw"})
POWER_MINIMUM = {"c1000": 100, "c1000_gen2": 100, "c2000_gen2": 300}
POWER_MAXIMUM = {"c1000": 1000, "c1000_gen2": 1200, "c2000_gen2": 1800}
CHARGE_CAP_MODELS = frozenset({"c1000_gen2", "c2000_gen2"})
NATIVE_MODELS = CHARGE_CAP_MODELS
DISCHARGE_FLOORS = (1, 5, 10, 15, 20)
DEVICE_TIMEOUT_OPTIONS = {"never": 0, "30_minutes": 30, "1_hour": 60, "2_hours": 120,
                          "4_hours": 240, "6_hours": 360, "12_hours": 720, "24_hours": 1440}
DISPLAY_BRIGHTNESS_OPTIONS = {"low": 1, "medium": 2, "high": 3}
DISPLAY_TIMEOUT_OPTIONS = {"never": 0, "10_seconds": 10, "20_seconds": 20, "30_seconds": 30,
                           "1_minute": 60, "5_minutes": 300, "30_minutes": 1800}
ORIGINAL_DISPLAY_TIMEOUT_OPTIONS = {key: seconds for key, seconds in DISPLAY_TIMEOUT_OPTIONS.items() if seconds >= 20}
LIGHT_MODE_OPTIONS = {"off": 0, "low": 1, "medium": 2, "high": 3, "sos": 4}
BOOLEAN_SETTINGS = {"set-off-grid-alert": "ac_off_grid_alert_enabled", "set-fast-charge": "ac_fast_charge_enabled",
                    "set-ac-power-saving": "ac_power_saving_mode_enabled", "set-dc-power-saving": "dc_power_saving_mode_enabled",
                    "set-port-memory": "port_memory_enabled"}


class GatewayError(Exception):
    """Gateway communication or response validation failed."""


class GatewayAuthError(GatewayError):
    """Gateway rejected authentication."""


class GatewayCommandError(GatewayError):
    """A command failed; its settings may already have changed."""


def normalize_url(value: str) -> str:
    """Normalize an endpoint without accepting credentials or URL parameters."""
    if not isinstance(value, str) or any(ord(char) < 32 for char in value):
        raise ValueError("Enter an HTTP or HTTPS gateway URL")
    parts = urlsplit(value.strip())
    if (parts.scheme not in ("http", "https") or not parts.hostname
            or parts.username is not None or parts.password is not None
            or parts.query or parts.fragment):
        raise ValueError("Enter a gateway URL without credentials, query or fragment")
    port = parts.port  # Reject malformed/out-of-range ports.
    host = parts.hostname.lower()
    host = f"[{host}]" if ":" in host else host
    if port is not None and port != (443 if parts.scheme == "https" else 80):
        host += f":{port}"
    return urlunsplit((parts.scheme, host, parts.path.rstrip("/"), "", ""))


def gateway_id(url: str) -> str:
    """Identify the configured endpoint without an account or station serial."""
    return hashlib.sha256(normalize_url(url).encode()).hexdigest()


def device_id(endpoint_id: str, name: str) -> str:
    """Keep names containing punctuation from colliding with each other."""
    return hashlib.sha256(f"{endpoint_id}\0{name}".encode()).hexdigest()


def numeric(value: Any) -> int | float | None:
    if type(value) in (int, float) and math.isfinite(value):
        return value
    return None


def binary_state(value: Any) -> bool | None:
    """Only explicit decoded 0/1 states establish mains/output presence."""
    return bool(value) if type(value) is int and value in (0, 1) else None


def snapshot_available(snapshot: dict, max_age: float = 90, now: float | None = None) -> bool:
    seen = numeric(snapshot.get("last_seen_timestamp"))
    if seen is None or not snapshot.get("connected") or not snapshot.get("available"):
        return False
    age = (time.time() if now is None else now) - seen
    return -5 <= age <= max_age


def parse_tou_plan(value: Any) -> dict | None:
    """Standalone HA validation; no SDK/Bluetooth dependencies or raw fields."""
    if (type(value) is not dict or set(value) != {
            "schema_version", "enabled", "periods", "reported_at", "source"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or type(value["enabled"]) is not bool or value["source"] != "status_d9"
            or type(value["reported_at"]) not in (int, float)
            or not 0 <= value["reported_at"] <= 253402300799
            or not math.isfinite(value["reported_at"])
            or type(value["periods"]) is not list or len(value["periods"]) > 6):
        return None
    periods = []
    for item in value["periods"]:
        if (type(item) is not dict or set(item) != {"tariff", "start_hour", "end_hour"}
                or item["tariff"] not in ("peak", "mid_peak", "off_peak")
                or type(item["start_hour"]) is not int or type(item["end_hour"]) is not int
                or not 0 <= item["start_hour"] < item["end_hour"] <= 24):
            return None
        periods.append(item.copy())
    ordered = sorted(periods, key=lambda item: item["start_hour"])
    if (value["enabled"] and not periods or any(
            left["end_hour"] > right["start_hour"] for left, right in zip(ordered, ordered[1:]))):
        return None
    return {"schema_version": 1, "enabled": value["enabled"], "periods": periods,
            "reported_at": value["reported_at"], "source": "status_d9"}


CONTROL_REASONS = frozenset({"model_unsupported", "transport_unsupported", "gateway_controls_disabled",
    "token_scope_denied", "worker_controls_disabled", "not_advertised", "telemetry_unavailable",
    "missing_or_invalid_metrics", "firmware_unqualified", "output_must_be_off",
    "countdown_must_be_inactive", "standard_mode_required", "clock_must_be_inactive", "command_in_progress"})
EXPECTED_METRICS = frozenset({"ac_charging_power_limit_w", "max_charge_percentage", "min_charge_percentage",
    "backup_reserve_percentage", "display_timeout_seconds", "display_brightness", "device_timeout_minutes",
    "clock_screen_first_brightness_flag_raw", "clock_screen_second_brightness_flag_raw", "port_memory_enabled",
    "ac_fast_charge_enabled", "temperature_unit_fahrenheit", "ac_off_grid_alert_enabled", "light_mode",
    "ac_power_saving_mode_enabled", "dc_power_saving_mode_enabled", "ac_input_connected", "ac_output_enabled",
    "dc_output_enabled", "usage_mode", "active_tariff", "tou_schedule_slot_count", "clock_screen_enabled",
    "clock_screen_transfer_status_raw", "ac_output_timeout_seconds", "dc_output_timeout_seconds",
    "ac_output_timer_remaining_seconds", "dc_output_timer_remaining_seconds",
    "disaster_preparation_active",
    "software_version", "software_version_module"})


def parse_control_availability(value: Any, model: str, protocol: str) -> dict | None:
    if (type(value) is not dict or type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or value.get("preflight_only") is not True or value.get("backend_validation_required") is not True
            or value.get("model") != model or value.get("protocol") != protocol
            or type(value.get("commands")) is not list or len(value["commands"]) > len(COMMANDS)):
        return None
    rows, seen = [], set()
    for row in value["commands"]:
        if (type(row) is not dict or type(row.get("command")) is not str or row["command"] not in COMMANDS
                or row["command"] in seen or any(type(row.get(key)) is not bool for key in ("advertised", "permitted", "ready"))
                or type(row.get("reasons")) is not list or len(row["reasons"]) > len(CONTROL_REASONS)
                or any(type(reason) is not str or reason not in CONTROL_REASONS for reason in row["reasons"])
                or type(row.get("missing_metrics")) is not list or len(row["missing_metrics"]) > len(EXPECTED_METRICS)
                or any(type(key) is not str or key not in EXPECTED_METRICS for key in row["missing_metrics"])):
            return None
        seen.add(row["command"])
        rows.append({"command": row["command"], "advertised": row["advertised"], "permitted": row["permitted"],
            "ready": row["ready"] and row["advertised"] and row["permitted"] and not row["reasons"] and not row["missing_metrics"],
            "reasons": list(row["reasons"]), "missing_metrics": list(row["missing_metrics"])})
    return {"schema_version": 1, "preflight_only": True, "backend_validation_required": True, "commands": rows}


def parse_command_context(value: Any, model: str, protocol: str) -> dict | None:
    if (type(value) is not dict or type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or value.get("preconditions_supported") is not True or type(value.get("busy")) is not bool
            or type(value.get("gateway_instance")) is not str or not re.fullmatch(r"[0-9a-f]{32}", value["gateway_instance"])
            or type(value.get("issued_at")) not in (int, float) or not 0 <= value["issued_at"] <= 253402300799
            or not math.isfinite(value["issued_at"]) or type(value.get("request_window_seconds")) is not int
            or value["request_window_seconds"] != 3600):
        return None
    expected = value.get("expected")
    if (type(expected) is not dict or not {"model", "protocol", "metrics"} <= expected.keys()
            or set(expected) - {"model", "protocol", "metrics", "tou_plan_readback"}
            or expected["model"] != model or expected["protocol"] != protocol or type(expected["metrics"]) is not dict
            or any(key not in EXPECTED_METRICS for key in expected["metrics"])):
        return None
    metrics = {}
    for key, metric in expected["metrics"].items():
        if key in ("software_version", "software_version_module"):
            valid = type(metric) is str and len(metric) <= 24 and re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){1,4}", metric, flags=re.ASCII)
        elif key in ("usage_mode", "active_tariff"):
            choices = ("standard", "time_of_use") if key == "usage_mode" else ("none", "peak", "mid_peak", "off_peak")
            valid = type(metric) is str and metric in choices
        else:
            valid = type(metric) is int and 0 <= metric <= 604800
        if not valid:
            return None
        metrics[key] = metric
    copied = {"model": model, "protocol": protocol, "metrics": metrics}
    if "tou_plan_readback" in expected:
        plan = parse_tou_plan(expected["tou_plan_readback"])
        if model not in NATIVE_MODELS or protocol != "native_mqtt" or plan is None:
            return None
        copied["tou_plan_readback"] = plan
    return {"schema_version": 1, "gateway_instance": value["gateway_instance"], "issued_at": value["issued_at"],
        "request_window_seconds": 3600, "preconditions_supported": True, "busy": value["busy"], "expected": copied}


def parse_snapshot(value: Any) -> dict:
    """Validate the gateway contract and discard identity/raw diagnostic fields."""
    if not isinstance(value, dict):
        raise GatewayError("Gateway returned an invalid device")
    for field in ("name", "model", "protocol"):
        if not isinstance(value.get(field), str) or not value[field]:
            raise GatewayError("Gateway returned an invalid device identity")
    if any(type(value.get(field)) is not bool for field in ("connected", "available")):
        raise GatewayError("Gateway returned invalid availability")
    if not isinstance(value.get("metrics"), dict):
        raise GatewayError("Gateway returned invalid telemetry")
    controls = value.get("controls", [])
    if not isinstance(controls, list) or any(not isinstance(item, str) for item in controls):
        raise GatewayError("Gateway returned invalid controls")
    result = {key: value[key] for key in ("name", "model", "protocol", "connected", "available")}
    result["last_seen_timestamp"] = numeric(value.get("last_seen_timestamp"))
    result["controls"] = sorted(set(controls) & COMMANDS)
    result["tou_plan_readback"] = parse_tou_plan(value.get("tou_plan_readback")) if (
        value["model"] in ("c1000_gen2", "c2000_gen2") and value["protocol"] == "native_mqtt") else None
    result["native_energy"] = None
    result["original_counters"] = None
    if value.get("original_counters") is not None:
        from .original_counters import validate_original_counters
        result["original_counters"] = validate_original_counters(value["original_counters"], model=value["model"], protocol=value["protocol"])
    result["wifi_signal"] = None
    if value.get("wifi_signal") is not None and value["model"] == "c1000_gen2" and value["protocol"] == "native_mqtt":
        from .wifi_signal import validate_wifi_signal
        result["wifi_signal"] = validate_wifi_signal(value["wifi_signal"], model=value["model"], protocol=value["protocol"])
    if value.get("native_energy") is not None and value["protocol"] == "native_mqtt":
        from .native_energy import validate_native_energy
        result["native_energy"] = validate_native_energy(value["native_energy"], model=value["model"])
    result["metrics"] = {key: metric for key, metric in value["metrics"].items()
                         if key in METRICS and (numeric(metric) is not None or isinstance(metric, str))}
    if value.get("power_flow") in ("unknown", "battery", "grid", "transitioning"):
        result["power_flow"] = value["power_flow"]
    for key, parser in (("control_availability", parse_control_availability), ("command_context", parse_command_context)):
        parsed = parser(value.get(key), value["model"], value["protocol"])
        if parsed is not None:
            result[key] = parsed
    state = value.get("ups_state")
    if (type(state) is dict and type(state.get("schema_version")) is int and state["schema_version"] == 1
            and type(state.get("telemetry_available")) is bool
            and all(state.get(key) is None or type(state.get(key)) is bool
                    for key in ("mains_connected", "battery_reserve_low"))
            and type(state.get("effective_reserve_percentage")) is int
            and 1 <= state["effective_reserve_percentage"] <= 100
            and type(state.get("reserve_hysteresis_percentage")) is int
            and state["reserve_hysteresis_percentage"] == 5):
        result["ups_state"] = {key: state.get(key) for key in ("schema_version", "telemetry_available",
            "mains_connected", "battery_reserve_low", "effective_reserve_percentage", "reserve_hysteresis_percentage")}
        if not state["telemetry_available"]:
            result["ups_state"].update(mains_connected=None, battery_reserve_low=None)
    return result


def integer(value: Any, label: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{label} must be an integer")
    return value


def native_gen2(snapshot: dict) -> bool:
    return snapshot.get("model") in NATIVE_MODELS and snapshot.get("protocol") == "native_mqtt"


def original_encrypted_preferences(snapshot: dict) -> bool:
    """Original preferences still require each command to be advertised below."""
    return snapshot.get("model") == "c1000" and snapshot.get("protocol") in ("prime", "native_mqtt")


def reserve_supported(snapshot: dict) -> bool:
    return snapshot.get("model") == "c2000_gen2" or native_gen2(snapshot)


def discharge_floor_options(snapshot: dict) -> list[str]:
    """Offer supported values that preserve the current reserve and charge cap."""
    if (snapshot.get("model") != "c1000_gen2" or not native_gen2(snapshot)
            or "set-discharge-floor" not in snapshot.get("controls", [])):
        return []
    metrics = snapshot.get("metrics", {})
    lower, upper, reserve = (metrics.get(key) for key in (
        "min_charge_percentage", "max_charge_percentage", "backup_reserve_percentage"))
    if (any(type(value) is not int for value in (lower, upper, reserve))
            or lower not in DISCHARGE_FLOORS or upper not in (80, 85, 90, 95, 100)
            or not 5 <= reserve <= 100 or reserve % 5 or not lower + 5 <= reserve <= upper):
        return []
    return [f"{value}%" for value in DISCHARGE_FLOORS if value + 5 <= reserve]


def device_timeout_options(snapshot: dict) -> list[str]:
    """Require supported transport, advertised control and exact timeout readback."""
    model, protocol = snapshot.get("model"), snapshot.get("protocol")
    supported = (model == "c1000" and protocol in ("legacy", "prime", "native_mqtt")
                 or model == "c1000_gen2" and protocol in ("prime", "native_mqtt"))
    value = snapshot.get("metrics", {}).get("device_timeout_minutes")
    if (not supported or "set-device-timeout" not in snapshot.get("controls", [])
            or type(value) is not int or value not in DEVICE_TIMEOUT_OPTIONS.values()):
        return []
    return list(DEVICE_TIMEOUT_OPTIONS)


def temperature_unit_supported(snapshot: dict) -> bool:
    supported = (snapshot.get("model") == "c1000" and snapshot.get("protocol") in ("legacy", "prime", "native_mqtt")
                 or snapshot.get("model") == "c1000_gen2" and native_gen2(snapshot))
    return (supported and "set-temperature-unit" in snapshot.get("controls", [])
            and binary_state(snapshot.get("metrics", {}).get("temperature_unit_fahrenheit")) is not None)


def _native_display_options(snapshot: dict, command: str, metric: str, options: dict[str, int]) -> list[str]:
    value = snapshot.get("metrics", {}).get(metric)
    if (snapshot.get("model") != "c1000_gen2" or not native_gen2(snapshot)
            or command not in snapshot.get("controls", []) or type(value) is not int or value not in options.values()):
        return []
    return list(options)


def display_brightness_options(snapshot: dict) -> list[str]:
    if original_encrypted_preferences(snapshot):
        value = snapshot.get("metrics", {}).get("display_brightness")
        if ("set-display-brightness" in snapshot.get("controls", [])
                and type(value) is int and value in DISPLAY_BRIGHTNESS_OPTIONS.values()):
            return list(DISPLAY_BRIGHTNESS_OPTIONS)
        return []
    return _native_display_options(snapshot, "set-display-brightness", "display_brightness", DISPLAY_BRIGHTNESS_OPTIONS)


def display_timeout_options(snapshot: dict) -> list[str]:
    if snapshot.get("model") == "c2000_gen2":
        value = snapshot.get("metrics", {}).get("display_timeout_seconds")
        if (snapshot.get("protocol") in ("prime", "native_mqtt")
                and "set-display-timeout" in snapshot.get("controls", [])
                and type(value) is int and value in (30, 60)):
            return ["30_seconds", "1_minute"]
        return []
    if original_encrypted_preferences(snapshot):
        value = snapshot.get("metrics", {}).get("display_timeout_seconds")
        if ("set-display-timeout" in snapshot.get("controls", [])
                and type(value) is int and value in ORIGINAL_DISPLAY_TIMEOUT_OPTIONS.values()):
            return list(ORIGINAL_DISPLAY_TIMEOUT_OPTIONS)
        return []
    return _native_display_options(snapshot, "set-display-timeout", "display_timeout_seconds", DISPLAY_TIMEOUT_OPTIONS)


def light_mode_options(snapshot: dict) -> list[str]:
    value = snapshot.get("metrics", {}).get("light_mode")
    if (original_encrypted_preferences(snapshot)
            and "set-light" in snapshot.get("controls", [])
            and type(value) is int and value in LIGHT_MODE_OPTIONS.values()):
        return list(LIGHT_MODE_OPTIONS)
    return []


def boolean_setting_supported(snapshot: dict, command: str) -> bool:
    model, protocol = snapshot.get("model"), snapshot.get("protocol")
    original = model == "c1000" and protocol == "legacy"
    if command in ("set-off-grid-alert", "set-port-memory"):
        supported = model == "c1000_gen2" and native_gen2(snapshot)
    elif command == "set-fast-charge":
        supported = (model == "c1000" and protocol in ("legacy", "prime", "native_mqtt")
                     or model == "c1000_gen2" and protocol in ("prime", "native_mqtt"))
    elif command in ("set-ac-power-saving", "set-dc-power-saving"):
        supported = original or (command == "set-dc-power-saving" and model == "c1000" and protocol in ("prime", "native_mqtt")
                                 and binary_state(snapshot.get("metrics", {}).get("dc_output_enabled")) is False)
        if command == "set-ac-power-saving" and model == "c1000" and protocol in ("prime", "native_mqtt"):
            metrics = snapshot.get("metrics", {})
            supported = (binary_state(metrics.get("ac_output_enabled")) is False
                         and type(metrics.get("ac_output_timer_remaining_seconds")) is int
                         and metrics["ac_output_timer_remaining_seconds"] == 0)
        elif command in ("set-dc-power-saving", "set-ac-power-saving") and model == "c1000_gen2" and protocol == "native_mqtt":
            metrics = snapshot.get("metrics", {})
            supported = (metrics.get("software_version") == "1.1.4.9"
                         and binary_state(metrics.get("ac_output_enabled" if command == "set-ac-power-saving" else "dc_output_enabled")) is False
                         and all(type(metrics.get(key)) is int and metrics[key] == 0 for key in
                                 ("ac_output_timeout_seconds", "dc_output_timeout_seconds")))
    else:
        return False
    return (supported and command in snapshot.get("controls", [])
            and binary_state(snapshot.get("metrics", {}).get(BOOLEAN_SETTINGS[command])) is not None)


def fast_charge_enable_allowed(snapshot: dict) -> bool:
    """Gen 2 enables require explicit Standard/no-tariff telemetry."""
    metrics = snapshot.get("metrics", {})
    if snapshot.get("model") != "c1000_gen2":
        return True
    return metrics.get("usage_mode") == "standard" and metrics.get("active_tariff") == "none"


def validate_plan(periods: Any, enabled: Any) -> list[dict]:
    if type(enabled) is not bool or not isinstance(periods, list) or len(periods) > 6:
        raise ValueError("Use a boolean enabled value and at most six periods")
    if enabled and not periods:
        raise ValueError("An enabled schedule needs at least one period")
    result = []
    for period in periods:
        if not isinstance(period, dict) or set(period) != {"tariff", "start_hour", "end_hour"}:
            raise ValueError("Each period needs tariff, start_hour and end_hour")
        tariff = period["tariff"]
        if not isinstance(tariff, str) or tariff not in ("peak", "mid_peak", "off_peak"):
            raise ValueError("Tariff must be peak, mid_peak or off_peak")
        start = integer(period["start_hour"], "Start hour")
        end = integer(period["end_hour"], "End hour")
        if not 0 <= start < end <= 24:
            raise ValueError("Use whole hours 0 <= start < end <= 24; split overnight periods")
        result.append(dict(period))
    ordered = sorted(result, key=lambda item: item["start_hour"])
    if any(left["end_hour"] > right["start_hour"] for left, right in zip(ordered, ordered[1:])):
        raise ValueError("Schedule periods must not overlap")
    return result


def clock_brightness_supported(snapshot: dict, window: int) -> bool:
    metrics = snapshot.get("metrics", {})
    return (type(window) is int and window in (1, 2) and snapshot.get("model") == "c1000_gen2"
            and snapshot.get("protocol") == "native_mqtt" and metrics.get("software_version") == "1.1.4.9"
            and "set-clock-brightness" in snapshot.get("controls", [])
            and metrics.get("usage_mode") == "standard" and metrics.get("active_tariff") == "none"
            and all(type(metrics.get(key)) is int and metrics[key] == 0 for key in
                    ("clock_screen_enabled", "clock_screen_transfer_status_raw",
                     "ac_output_timeout_seconds", "dc_output_timeout_seconds"))
            and all(type(metrics.get(key)) is int and metrics[key] in (0, 1) for key in
                    ("clock_screen_first_brightness_flag_raw", "clock_screen_second_brightness_flag_raw")))


def validate_command(snapshot: dict, payload: dict) -> None:
    """Validate known settings against fresh telemetry before any POST."""
    command = payload.get("command")
    if not isinstance(command, str) or command not in COMMANDS or command not in snapshot["controls"]:
        raise ValueError("This control is not enabled by the gateway")
    if not snapshot_available(snapshot, 30):
        raise ValueError("Fresh, connected telemetry is required for controls")
    metrics = snapshot["metrics"]
    model = snapshot["model"]
    expected = {
        "set-charge-power": {"command", "watts"},
        "set-charge-cap": {"command", "upper"},
        "set-backup-reserve": {"command", "reserve"},
        "set-tou-plan": {"command", "periods", "enabled"},
        "return-grid": {"command", "timeout"},
        "set-discharge-floor": {"command", "lower"},
        "set-temperature-unit": {"command", "fahrenheit"},
        "set-off-grid-alert": {"command", "enabled"},
        "set-device-timeout": {"command", "minutes"},
        "set-fast-charge": {"command", "enabled"},
        "set-ac-power-saving": {"command", "enabled"},
        "set-dc-power-saving": {"command", "enabled"},
        "set-port-memory": {"command", "enabled"},
        "set-display-brightness": {"command", "level"},
        "set-clock-brightness": {"command", "window", "high"},
        "set-display-timeout": {"command", "seconds"},
        "set-light": {"command", "mode"},
    }[command]
    if set(payload) != expected:
        raise ValueError("Unexpected command fields")
    if command == "set-charge-power":
        watts = integer(payload["watts"], "Charging power")
        if (model not in POWER_MAXIMUM
                or not POWER_MINIMUM[model] <= watts <= POWER_MAXIMUM[model] or watts % 100):
            raise ValueError("Charging power is outside the validated model range")
        if numeric(metrics.get("ac_charging_power_limit_w")) is None:
            raise ValueError("Charging-power telemetry is missing")
    elif command == "set-charge-cap":
        upper = integer(payload["upper"], "Charge cap")
        if model not in CHARGE_CAP_MODELS or upper not in (80, 85, 90, 95, 100):
            raise ValueError("Charge cap must be 80–100 percent in steps of five")
        if numeric(metrics.get("max_charge_percentage")) is None:
            raise ValueError("Charge-cap telemetry is missing")
        reserve = numeric(metrics.get("backup_reserve_percentage"))
        if reserve_supported(snapshot) and (reserve is None or upper < reserve):
            raise ValueError("Charge cap must preserve the reported backup reserve")
    elif command == "set-backup-reserve":
        reserve = integer(payload["reserve"], "Backup reserve")
        lower, upper = (numeric(metrics.get(key)) for key in ("min_charge_percentage", "max_charge_percentage"))
        if (not reserve_supported(snapshot) or lower is None or upper is None
                or numeric(metrics.get("backup_reserve_percentage")) is None
                or reserve % 5 or not 5 <= reserve <= 100 or not lower + 5 <= reserve <= upper):
            raise ValueError("Reserve must be within current caps, in steps of five")
    elif command == "set-device-timeout":
        minutes = integer(payload["minutes"], "Device Timeout")
        if not device_timeout_options(snapshot) or minutes not in DEVICE_TIMEOUT_OPTIONS.values():
            raise ValueError("Device Timeout requires supported C1000 telemetry and an allowed minute value; 0 means Never")
    elif command == "set-display-brightness":
        level = integer(payload["level"], "Display brightness")
        if not display_brightness_options(snapshot) or level not in DISPLAY_BRIGHTNESS_OPTIONS.values():
            raise ValueError("Display brightness requires a supported C1000 transport and level 1, 2 or 3")
    elif command == "set-clock-brightness":
        if type(payload["high"]) is not bool or not clock_brightness_supported(snapshot, payload["window"]):
            raise ValueError("Clock brightness requires known inactive C1000 Gen 2 clock and countdown telemetry")
    elif command == "set-display-timeout":
        seconds = integer(payload["seconds"], "Screen timeout")
        if seconds not in [DISPLAY_TIMEOUT_OPTIONS[option] for option in display_timeout_options(snapshot)]:
            raise ValueError("Screen timeout requires a supported C1000 transport and second value")
    elif command == "set-light":
        mode = integer(payload["mode"], "Light mode")
        if mode not in [LIGHT_MODE_OPTIONS[option] for option in light_mode_options(snapshot)]:
            raise ValueError("Light control requires supported original C1000 telemetry and mode 0 through 4")
    elif command == "set-discharge-floor":
        lower = integer(payload["lower"], "Discharge floor")
        if f"{lower}%" not in discharge_floor_options(snapshot):
            raise ValueError("Discharge floor must preserve reserve on C1000 Gen 2 native MQTT")
    elif command == "set-temperature-unit":
        if type(payload["fahrenheit"]) is not bool or not temperature_unit_supported(snapshot):
            raise ValueError("Temperature display requires a supported C1000 profile and valid boolean telemetry")
    elif command in BOOLEAN_SETTINGS:
        if type(payload["enabled"]) is not bool or not boolean_setting_supported(snapshot, command):
            raise ValueError("This setting requires a supported C1000 profile and valid boolean telemetry")
        if command == "set-fast-charge" and model == "c1000_gen2":
            if native_gen2(snapshot) and binary_state(metrics.get("ac_input_connected")) is not True:
                raise ValueError("Native fast charge requires connected mains")
            if payload["enabled"] and not fast_charge_enable_allowed(snapshot):
                raise ValueError("Fast charge enable requires Standard mode with no active tariff")
    elif command == "set-tou-plan":
        if not native_gen2(snapshot):
            raise ValueError("Time-of-Use control requires Gen 2 native MQTT")
        validate_plan(payload["periods"], payload["enabled"])
        if metrics.get("ac_fast_charge_enabled") != 0:
            raise ValueError("Fast charge must be off before changing Time-of-Use")
    elif command == "return-grid":
        if not native_gen2(snapshot):
            raise ValueError("Return to grid requires Gen 2 native MQTT")
        if integer(payload["timeout"], "Timeout") != 30:
            raise ValueError("Return-to-grid timeout must be 30 seconds")
    if command in ("set-tou-plan", "return-grid"):
        if metrics.get("ac_output_enabled") != 1 or metrics.get("ac_input_connected") != 1:
            raise ValueError("Enabled AC output and connected mains must be reported")
        if any(numeric(metrics.get(key)) is None for key in (
            "battery_percentage", "max_charge_percentage", "min_charge_percentage", "backup_reserve_percentage"
        )):
            raise ValueError("Complete battery and charge-limit telemetry is required")


def request_deadline(method: str, payload: dict | None = None) -> int:
    """Allow the worker budget, its five-second RPC margin, and HTTP overhead.

    Match gateway ap_service.control_timeout without importing gateway/BLE
    dependencies into HA. This deadline bounds waiting, not device execution:
    connection failure or expiry cannot establish whether settings changed.
    """
    if method != "POST":
        return 10
    payload = payload or {}
    command = payload.get("command")
    budget = 45
    if command == "return-grid":
        duration = integer(payload.get("timeout", 30), "Timeout")
        if not 5 <= duration <= 120:
            raise ValueError("Return-to-grid timeout is outside the gateway range")
        budget = 2 * duration + 100
    elif command == "set-tou-plan":
        budget = 120
    return budget + 5 + 10


class GatewayClient:
    """Use Home Assistant's shared session; never log bearer tokens or bodies."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str = "") -> None:
        self.session = session
        self.url = normalize_url(url)
        self.token = token.strip()
        if any(ord(char) < 32 for char in self.token):
            raise ValueError("Invalid gateway token")

    async def _request(self, method: str, path: str, payload: dict | None = None,
                       *, optional_history: bool = False) -> Any:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            async with self.session.request(method, self.url + path, json=payload,
                                            headers=headers, allow_redirects=False,
                                            timeout=aiohttp.ClientTimeout(
                                                total=3 if optional_history else request_deadline(method, payload),
                                                sock_connect=3 if optional_history else 10
                                            )) as response:
                if response.status == 401 or (response.status == 403 and method == "GET"):
                    raise GatewayAuthError("Gateway authentication failed")
                if optional_history and response.status == 404:
                    return None
                if response.status != 200:
                    if method == "POST":
                        if response.status == 504:
                            raise GatewayCommandError("Power flow was not confirmed; settings may have changed. Refresh status before retrying.")
                        raise GatewayCommandError(
                            f"Gateway command failed (HTTP {response.status}); settings may have changed. Refresh status before retrying."
                        )
                    raise GatewayError(f"Gateway request failed (HTTP {response.status})")
                if optional_history:
                    # A fixed fleet summary is bounded; never ingest raw history
                    # rows or an unbounded private response into HA.
                    import json
                    body = bytearray()
                    async for chunk in response.content.iter_chunked(8192):
                        body.extend(chunk)
                        if len(body) > 65536:
                            raise GatewayError("Gateway history summary is too large")
                    try:
                        result = json.loads(body)
                    except RecursionError as err:
                        raise GatewayError("Invalid gateway history summary") from err
                    if not isinstance(result, dict):
                        raise GatewayError("Invalid gateway history summary")
                    return result
                return await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            if method == "POST":
                raise GatewayCommandError("Command result is unknown; settings may have changed. Refresh status before retrying.") from err
            raise GatewayError("Unable to read the gateway") from err

    async def async_devices(self) -> dict[str, dict]:
        data = await self._request("GET", "/devices")
        if not isinstance(data, dict) or not isinstance(data.get("devices"), list):
            raise GatewayError("Gateway returned an invalid device list")
        result = {}
        for item in data["devices"]:
            snapshot = parse_snapshot(item)
            if snapshot["name"] in result:
                raise GatewayError("Gateway device names must be unique")
            result[snapshot["name"]] = snapshot
        return result

    async def async_history_summary(self) -> dict | None:
        """One optional read-only fleet request, never a station query."""
        return await self._request("GET", "/history/summary", optional_history=True)

    async def async_device(self, name: str) -> dict:
        snapshot = parse_snapshot(await self._request("GET", f"/devices/{quote(name, safe='')}"))
        if snapshot["name"] != name:
            raise GatewayError("Gateway returned a different device")
        return snapshot

    async def async_command(self, name: str, payload: dict, *, expected_snapshot: dict | None = None) -> dict:
        if not self.token:
            raise GatewayAuthError("A gateway token is required for controls")
        snapshot = await self.async_device(name) if expected_snapshot is None else expected_snapshot
        if snapshot.get("name") != name:
            raise ValueError("Command baseline belongs to another station")
        validate_command(snapshot, payload)
        body = dict(payload)
        context = snapshot.get("command_context")
        if context is not None:
            body.update(expected=context["expected"], coordination={"request_id": uuid.uuid4().hex,
                "gateway_instance": context["gateway_instance"], "issued_at": context["issued_at"]})
        raw = await self._request("POST", f"/devices/{quote(name, safe='')}/commands", body)
        try:
            result = parse_snapshot(raw)
        except GatewayError as err:
            raise GatewayCommandError("Invalid command response; settings may have changed. Refresh status before retrying.") from err
        if result["name"] != name:
            raise GatewayCommandError("Gateway returned a different device; command result is unknown")
        return result
