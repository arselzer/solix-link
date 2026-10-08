"""Partial, passive C-series protobuf decoder; physical units are unverified.

Only recovered counter fields are returned. Identity strings and unknown binary
fields are deliberately omitted. This module performs no device requests.
"""

from __future__ import annotations

import base64
import binascii
import math
from collections.abc import Iterator

REPORT_NAME = "charging_pps_series_c_0009"
GROUPS = {18: "time_of_use", 19: "standard", 20: "backup_variant_1", 21: "backup_variant_2"}
COUNTERS = {
    1: "ac_output_duration_raw", 2: "ac_charge_duration_raw",
    3: "ac_input_energy_raw", 4: "ac_output_energy_raw",
    5: "other_output_duration_raw", 6: "dc_charge_duration_raw",
    7: "dc_input_energy_raw", 8: "other_output_energy_raw",
}


def _varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for index in range(10):
        if offset >= len(data):
            raise ValueError("Truncated protobuf varint")
        byte = data[offset]
        offset += 1
        if index == 9 and byte > 1:
            raise ValueError("Protobuf varint exceeds 64 bits")
        value |= (byte & 0x7F) << (7 * index)
        if byte < 0x80:
            return value, offset
    raise ValueError("Invalid protobuf varint")


def _fields(data: bytes) -> Iterator[tuple[int, int, int | bytes]]:
    offset = 0
    while offset < len(data):
        key, offset = _varint(data, offset)
        tag, wire = key >> 3, key & 7
        if not 1 <= tag < 2**29:
            raise ValueError("Invalid protobuf field number")
        if wire == 0:
            value, offset = _varint(data, offset)
        elif wire in (1, 2, 5):
            if wire == 2:
                length, offset = _varint(data, offset)
            else:
                length = 8 if wire == 1 else 4
            if length > len(data) - offset:
                raise ValueError("Truncated protobuf field")
            value = data[offset:offset + length]
            offset += length
        else:
            raise ValueError("Unsupported protobuf wire type")
        yield tag, wire, value


def decode_energy_report(payload: bytes) -> dict:
    """Decode recovered groups without asserting Wh, lifetime totals or resets."""
    if not isinstance(payload, bytes) or not 0 < len(payload) <= 8192:
        raise ValueError("Energy report must contain 1–8192 bytes")
    groups = {}
    for tag, wire, value in _fields(payload):
        if tag not in GROUPS:
            continue
        if wire != 2:
            raise ValueError("Energy group has an invalid wire type")
        counters = groups.setdefault(GROUPS[tag], {})
        for field, kind, number in _fields(value):
            if field in COUNTERS:
                if kind != 0:
                    raise ValueError("Energy counter has an invalid wire type")
                counters[COUNTERS[field]] = number
    return {"protobuf_name": REPORT_NAME, "units_verified": False, "groups": groups}


def _event_payloads(request: dict, report_name: str) -> list[tuple[bytes, int | float | None]]:
    """Bounded payloads and radio construction time; discard envelope identities."""
    if not isinstance(request, dict) or request.get("protobuf_name") != report_name:
        raise ValueError("Unsupported energy report schema")
    events = request.get("events")
    if not isinstance(events, list) or not 1 <= len(events) <= 32:
        raise ValueError("Invalid energy report events")
    reports = []
    timestamp = request.get("utc_ts")
    if type(timestamp) is str and timestamp.isascii() and timestamp.isdigit() and len(timestamp) <= 11:
        timestamp = int(timestamp)
    if type(timestamp) not in (int, float) or not 0 < timestamp < 253402300800 or not math.isfinite(timestamp):
        timestamp = None
    for event in events:
        params = event.get("params") if isinstance(event, dict) else None
        encoded = params.get("payload") if isinstance(params, dict) else None
        if not isinstance(encoded, str) or len(encoded) > 10924:
            raise ValueError("Invalid encoded energy report")
        try:
            payload = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Invalid encoded energy report") from exc
        reports.append((payload, timestamp))
    return reports


def decode_energy_events(request: dict) -> list[dict]:
    """Decode Gen 2 counters. Batch time does not order individual events."""
    return [{**decode_energy_report(payload), "event_timestamp": timestamp}
            for payload, timestamp in _event_payloads(request, REPORT_NAME)]
