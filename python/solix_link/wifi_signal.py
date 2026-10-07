"""Validated, separately timed radio observations; never infer signal quality."""

from __future__ import annotations

import math
import time


MAX_AGE_SECONDS = 600
POLL_INTERVAL_SECONDS = 300


def validate_wifi_signal(value: object, *, model: str, protocol: str,
                         now: float | None = None) -> dict | None:
    """Sanitize a C1000 Gen 2 observation without refreshing its timestamp."""
    if model != "c1000_gen2" or protocol != "native_mqtt" or type(value) is not dict:
        return None
    if (type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or value.get("source") != "radio_ap_info"
            or value.get("main_version") != "1.1.4.9"
            or value.get("radio_version") != "0.3.3.0"
            or value.get("settings_unchanged") is not True):
        return None
    observed = value.get("observed_at")
    rssi = value.get("wifi_rssi_dbm")
    if type(observed) not in (int, float) or not 0 < observed < 253402300800:
        return None
    if rssi is not None and (type(rssi) is not int or not -128 <= rssi < 0):
        return None
    age = (time.time() if now is None else now) - observed
    available = math.isfinite(age) and -5 <= age < MAX_AGE_SECONDS and rssi is not None
    return {"schema_version": 1, "source": "radio_ap_info", "main_version": "1.1.4.9",
            "radio_version": "0.3.3.0", "settings_unchanged": True, "observed_at": observed,
            "max_age_seconds": MAX_AGE_SECONDS, "available": available,
            "wifi_rssi_dbm": rssi if available else None}
