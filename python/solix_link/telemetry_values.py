"""Model-scoped reported telemetry, distinct from saved configuration."""

import re

COMPONENT_VERSIONS = ("software_version_controller", "software_version_inverter",
                      "software_version_bms", "software_version_module")
PORT_MODELS = {
    "usb_c1_power_w": {"c1000", "c300"}, "usb_c2_power_w": {"c1000", "c300"},
    "usb_c3_power_w": {"c300"}, "usb_a1_power_w": {"c1000", "c300"},
    "usb_a2_power_w": {"c1000"}, "dc_input_power_w": {"c1000"},
    "solar_input_power_w": {"c300"}, "input_power_w": {"c300"},
}


def telemetry_supported(key: str, model: str) -> bool:
    if key in PORT_MODELS:
        return model in PORT_MODELS[key]
    if key in COMPONENT_VERSIONS:
        return model == "c2000_gen2" or (model == "c1000_gen2" and key == "software_version_module")
    if key.endswith("_timer_remaining_seconds"):
        return model in ("c1000", "c2000_gen2", "c300")
    if key == "wifi_rssi_dbm":
        return model == "c1000_gen2"
    return True


def version_value(value: object) -> str | None:
    return value if (type(value) is str and len(value) <= 24
                     and re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,3}){1,4}", value)) else None


def duration_value(key: str, value: object, *, model: str, activity: str | None) -> int | None:
    if type(value) is not int or not telemetry_supported(key, model):
        return None
    if key == "time_remaining_minutes":
        # Original/C300 LCD estimates use tenths of an hour, capped at 99.9 h.
        # Gen 2 encodes a uint16 in those same units; 0xffff is not a runtime.
        limit = 5994 if model in ("c1000", "c300") else 65534 * 6
        if not 0 < value <= limit or value % 6:
            return None
        if model != "c1000" and activity not in ("charging", "discharging"):
            return None
        return value
    # Unknown/max uint32 sentinels are never presented as days of countdown.
    return value if 0 <= value < 0xffffffff else None
