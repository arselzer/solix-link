"""Passive original-C1000 numeric diagnostics; no physical units are assigned."""

from __future__ import annotations

import math
import re
import time

REPORT_NAME = "charging_pps_series_c_0002"
COUNTERS = tuple(f"counter_{index}_raw" for index in range(1, 9))
MAX_AGE_SECONDS = 7200


def decode_original_counter_events(request: dict) -> list[dict]:
    """Decode only field 19's eight numbers; never map them to Gen 2 channels."""
    from .energy_report import _event_payloads, _fields
    reports = []
    for payload, timestamp in _event_payloads(request, REPORT_NAME):
        if not 0 < len(payload) <= 8192:
            raise ValueError("Original counter report must contain 1–8192 bytes")
        counters, found = {}, False
        for tag, wire, value in _fields(payload):
            if tag != 19:
                continue
            if found or wire != 2:
                raise ValueError("Ambiguous original counter group")
            found = True
            for field, kind, number in _fields(value):
                if not 1 <= field <= 8:
                    continue
                key = f"counter_{field}_raw"
                if kind != 0 or key in counters:
                    raise ValueError("Ambiguous original counter field")
                counters[key] = number
        if not found:
            raise ValueError("Missing original counter group")
        reports.append({"counters": counters, "event_timestamp": timestamp})
    return reports


def validate_original_counters(value: object, *, model: str, protocol: str,
                               now: float | None = None) -> dict | None:
    """Allowlisted public diagnostics with independent upload freshness."""
    now = time.time() if now is None else now
    if (model != "c1000" or protocol != "native_mqtt" or type(value) is not dict
            or type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or value.get("protobuf_name") != REPORT_NAME or value.get("units_verified") is not False
            or value.get("layout_provenance") != "main_1_5_9_encoder"
            or type(value.get("reported_at")) not in (int, float)
            or not math.isfinite(value["reported_at"]) or not 0 <= value["reported_at"] < 253402300800
            or type(now) not in (int, float) or not math.isfinite(now)
            or type(value.get("reports")) is not list or not 1 <= len(value["reports"]) <= 32):
        return None
    reports = []
    for report in value["reports"]:
        if (type(report) is not dict or type(report.get("counters")) is not dict
                or set(report["counters"]) - set(COUNTERS)
                or any(type(v) is not int or not 0 <= v < 2**64 for v in report["counters"].values())):
            return None
        stamp = report.get("event_timestamp")
        if stamp is not None and (type(stamp) not in (int, float) or not math.isfinite(stamp)
                                  or not 0 < stamp < 253402300800):
            return None
        reports.append({"counters": report["counters"].copy(), "event_timestamp": stamp})
    version = value.get("firmware_version")
    version = version if type(version) is str and len(version) <= 24 and re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,3}){1,4}", version) else None
    return {"schema_version": 1, "protobuf_name": REPORT_NAME, "units_verified": False,
            "layout_provenance": "main_1_5_9_encoder", "installed_layout_verified": False,
            "firmware_version": version,
            "reported_at": value["reported_at"], "reports": reports,
            "batch_order_unknown": len(reports) > 1,
            "available": -5 <= now - value["reported_at"] < MAX_AGE_SECONDS}


def original_counter_value(report: object, index: int, *, model: str, protocol: str,
                           now: float | None = None) -> int | None:
    report = validate_original_counters(report, model=model, protocol=protocol, now=now)
    if report is None or not report["available"] or report["batch_order_unknown"] or type(index) is not int or not 1 <= index <= 8:
        return None
    return report["reports"][0]["counters"].get(f"counter_{index}_raw")
