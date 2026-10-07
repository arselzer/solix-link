"""Conservative observed AC counter deltas; no station commands or reset guesses."""

from __future__ import annotations

from copy import deepcopy
import uuid

from .energy_values import MAX_INTEGER, MAX_REPORT_AGE, _timestamp

CHANNELS = ("ac_input", "ac_output")
MAX_POWER_W = 10000


def update_energy_meter(previous: dict | None, reports: list[dict], *, model: str,
                        firmware_version: str | None, receipt: float, groups: dict) -> dict | None:
    """Count ordered Standard AC deltas on the observed Gen 2 firmware versions.

    A persistent quarantine freezes totals on ambiguous boundaries. Older
    uploads and exact timestamp duplicates do not refresh the accepted baseline.
    A physical reset is never inferred from a decreasing counter.
    """
    meter = deepcopy(previous)

    def quarantine(reason: str) -> dict | None:
        if meter is not None:
            if meter["status"] != "quarantined":
                meter.update(status="quarantined", reason=reason)
            meter["rejected_reports"] += 1
        return meter

    if (model, firmware_version) not in (("c1000_gen2", "1.1.4.9"), ("c2000_gen2", "2.1.6.4")):
        return quarantine("firmware_changed")
    if len(reports) != 1:
        return quarantine("batch_order_unknown")
    timestamp = reports[0].get("event_timestamp")
    if not _timestamp(timestamp):
        return quarantine("timestamp_missing")
    if not -5 <= receipt - timestamp < MAX_REPORT_AGE:
        return quarantine("timestamp_outside_window")
    raw = groups.get("standard", {})
    if any(type(raw.get(f"{channel}_energy_raw")) is not int
           or not 0 <= raw[f"{channel}_energy_raw"] <= 2**32 - 1 for channel in CHANNELS):
        return quarantine("coverage_changed")
    counters = {channel: raw[f"{channel}_energy_raw"] for channel in CHANNELS}
    if meter is None:
        return {"schema_version": 1, "generation": uuid.uuid4().hex, "started_at": receipt,
            "last_event_timestamp": timestamp, "last_receipt_at": receipt,
            "status": "tracking", "reason": "none", "accepted_reports": 1, "rejected_reports": 0,
            "channels": {channel: {"counter_raw": counter, "total_raw": 0}
                         for channel, counter in counters.items()}}
    if meter["status"] == "quarantined":
        return quarantine(meter["reason"])
    interval = timestamp - meter["last_event_timestamp"]
    if interval < 0:
        meter["rejected_reports"] += 1
        return meter
    if interval == 0:
        if any(counter != meter["channels"][channel]["counter_raw"] for channel, counter in counters.items()):
            return quarantine("clock_conflict")
        meter["rejected_reports"] += 1
        return meter
    if interval >= MAX_REPORT_AGE:
        return quarantine("report_gap")
    deltas = {channel: counter - meter["channels"][channel]["counter_raw"]
              for channel, counter in counters.items()}
    if any(delta < 0 for delta in deltas.values()):
        return quarantine("counter_decreased")
    # Two rounded endpoints and a deliberately broad electrical ceiling.
    if any(delta > MAX_POWER_W * interval / 3600 + 2 for delta in deltas.values()):
        return quarantine("implausible_delta")
    if any(meter["channels"][channel]["total_raw"] + delta > MAX_INTEGER for channel, delta in deltas.items()):
        return quarantine("total_overflow")
    for channel, delta in deltas.items():
        meter["channels"][channel]["counter_raw"] = counters[channel]
        meter["channels"][channel]["total_raw"] += delta
    meter.update(last_event_timestamp=timestamp, last_receipt_at=receipt,
                 accepted_reports=meter["accepted_reports"] + 1)
    return meter
