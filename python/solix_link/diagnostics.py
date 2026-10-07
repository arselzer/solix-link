"""Radio reports and passive, redacted gateway connection diagnostics."""

from __future__ import annotations

import math
import re
import time
from collections.abc import Iterable
from .energy_values import validate_native_energy


MODELS = frozenset({"c1000", "c1000_gen2", "c2000_gen2", "c300"})
PROTOCOLS = frozenset({"legacy", "prime", "native_mqtt"})
VERSION = re.compile(r"[0-9]{1,3}(?:\.[0-9]{1,3}){1,5}\Z")
NEXT_STEPS = {
    "ready": "Telemetry is fresh; no recovery action is needed.",
    "disconnected": "Check the monitoring worker and the station's connection before retrying.",
    "awaiting_telemetry": "Wait for a complete station report; connection alone does not establish freshness.",
    "stale_telemetry": "Check the monitoring worker and transport; do not use cached readings for controls.",
    "clock_skew": "Check the gateway and worker clocks before trusting report timestamps.",
    "unavailable": "The monitor reports unavailable despite a recent timestamp; check its status and logs.",
}


def gateway_diagnostics(snapshots: Iterable[dict], *, now: float | None = None) -> dict:
    """Explain cached availability without device requests or arbitrary strings.

    Station labels are ordinal; names, network addresses, configuration, raw
    metrics and error text never enter the downloadable report. Availability
    remains authoritative from the monitor and cannot be upgraded here.
    """
    now = time.time() if now is None else now
    stations = []
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        model, protocol = snapshot.get("model"), snapshot.get("protocol")
        model = model if isinstance(model, str) and model in MODELS else "unknown"
        protocol = protocol if isinstance(protocol, str) and protocol in PROTOCOLS else "unknown"
        limit = 30 if protocol == "native_mqtt" else 90
        seen = snapshot.get("last_seen_timestamp")
        try:
            age = now - seen if type(seen) in (int, float) and math.isfinite(seen) else None
            if age is not None and not math.isfinite(age):
                age = None
        except OverflowError:
            age = None
        connected = snapshot.get("connected") is True
        if not connected:
            reason = "disconnected"
        elif age is None:
            reason = "awaiting_telemetry"
        elif age < -5:
            reason = "clock_skew"
        elif age >= limit:
            reason = "stale_telemetry"
        elif snapshot.get("available") is not True:
            reason = "unavailable"
        else:
            reason = "ready"
        metrics = snapshot.get("metrics")
        version = metrics.get("software_version") if isinstance(metrics, dict) else None
        stations.append({
            "station": len(stations) + 1,
            "model": model,
            "protocol": protocol,
            "software_version": version if isinstance(version, str) and VERSION.fullmatch(version) else None,
            "connected": connected,
            "available": reason == "ready",
            "telemetry_age_seconds": round(age, 2) if age is not None else None,
            "freshness_limit_seconds": limit,
            "availability_reason": reason,
            "next_step": NEXT_STEPS[reason],
            "native_energy": validate_native_energy(snapshot.get("native_energy"), model=model, now=now) if protocol == "native_mqtt" else None,
        })
    return {
        "schema_version": 1,
        "station_count": len(stations),
        "available_station_count": sum(station["available"] for station in stations),
        "stations": stations,
    }

FIELDS = {
    0xA1: ("system_reboot_code", 1),
    0xA2: ("sdk_reset_code", 1),
    0xA3: ("http_error_code", 4),
    0xA4: ("wifi_error_code", 4),
    0xA5: ("ble_disconnect_code", 4),
    0xA6: ("mqtt_error_code", 4),
}


def decode_wifi_rssi(payload: bytes) -> int | None:
    """Read raw signed RSSI from function 10/4822; status 01 is unavailable.

    The radio supplies a signed byte in a four-byte raw TLV. Reject other
    statuses, shapes and impossible source widths rather than returning a
    cached quality byte or interpreting failure as a signal measurement.
    """
    if payload == b"\x01":
        return None
    if len(payload) != 7 or payload[:3] != b"\x00\xa1\x04":
        raise ValueError("Invalid radio RSSI response")
    value = int.from_bytes(payload[3:], "little", signed=True)
    if not -128 <= value <= 127 or value == 0:
        raise ValueError("Invalid radio RSSI value")
    return value


def decode_network_diagnostics(payload: bytes) -> dict[str, int]:
    """Decode a successful 0f/4820 reply, rejecting incomplete reports.

    Error words are signed little-endian integers. Zero is a reported code,
    not proof of network connectivity. Reset bytes may be 255 after reading.
    """
    if not payload or payload[0] != 0:
        raise ValueError("Station did not return successful network diagnostics")
    result: dict[str, int] = {}
    position = 1
    while position < len(payload):
        if position + 2 > len(payload):
            raise ValueError("Truncated network diagnostic TLV")
        tag, length = payload[position:position + 2]
        position += 2
        if position + length > len(payload):
            raise ValueError("Truncated network diagnostic value")
        value = payload[position:position + length]
        position += length
        if tag in FIELDS:
            name, expected_length = FIELDS[tag]
            if length != expected_length or name in result:
                raise ValueError(f"Invalid network diagnostic field: {name}")
            result[name] = int.from_bytes(value, "little", signed=length == 4)
    if len(result) != len(FIELDS):
        raise ValueError("Incomplete network diagnostic report")
    return result
