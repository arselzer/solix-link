"""Offline counter boundaries and power-trace comparisons, never calibration claims."""
from __future__ import annotations

import math

from .energy_report import COUNTERS, GROUPS

FIRMWARE_SHA256 = "21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9"
MAX_TIMESTAMP = 253402300799


class EnergyAnalysisError(ValueError):
    def __init__(self):
        super().__init__("InvalidEnergyAnalysis")


def _number(value: object, minimum=0, maximum=MAX_TIMESTAMP) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value) and minimum <= value <= maximum
    except OverflowError:
        return False


def analyze_energy_epochs(reports: object, *, max_report_gap: int = 3600) -> dict:
    """Compare adjacent sanitized A1763-format reports, with no wrap assumption.

    A decrease may be a reset, width wrap or reordered report; start a new
    analysis segment and never compute an energy delta across that boundary.
    Radio event timestamps are not established MCU measurement timestamps.
    """
    if (type(reports) is not list or not 1 <= len(reports) <= 1024
            or type(max_report_gap) is not int or not 1 <= max_report_gap <= 86400):
        raise EnergyAnalysisError()
    parsed = []
    for report in reports:
        if (type(report) is not dict or set(report) != {"timestamp", "groups"}
                or not _number(report["timestamp"]) or type(report["groups"]) is not dict
                or not report["groups"] or not set(report["groups"]) <= set(GROUPS.values())):
            raise EnergyAnalysisError()
        counters = {}
        for group, values in report["groups"].items():
            if type(values) is not dict or not values or not set(values) <= set(COUNTERS.values()):
                raise EnergyAnalysisError()
            for field, value in values.items():
                if type(value) is not int or not 0 <= value < 2**32:
                    raise EnergyAnalysisError()
                counters[group + "." + field] = value
        parsed.append((report["timestamp"], counters))
    intervals, segment = [], 0
    for index, ((before_time, before), (after_time, after)) in enumerate(zip(parsed, parsed[1:]), 1):
        elapsed = after_time - before_time
        common = before.keys() & after.keys()
        decreases = sorted(key for key in common if after[key] < before[key])
        missing = sorted(before.keys() ^ after.keys())
        reasons = []
        if elapsed <= 0:
            reasons.append("clock_nonincreasing")
        elif elapsed > max_report_gap:
            reasons.append("report_gap")
        if decreases:
            reasons.append("counter_decrease_reset_wrap_or_reorder")
        if missing:
            reasons.append("counter_coverage_changed")
        if reasons:
            segment += 1
        deltas = {key: after[key] - before[key] for key in sorted(common)} if not reasons else {}
        intervals.append({"ending_report_index": index, "analysis_segment": segment,
                          "elapsed_event_seconds": elapsed, "boundary_reasons": reasons,
                          "decreased_counters": decreases, "missing_counters": missing,
                          "raw_deltas": deltas})
    return {"schema_version": 1, "model_scope": "A1763 main 1.1.4.9", "firmware_sha256": FIRMWARE_SHA256,
            "units_verified": False, "lifetime_total_available": False,
            "mcu_snapshot_timing_verified": False, "reports": len(parsed), "intervals": intervals}


def compare_power_interval(raw_delta: object, start: object, end: object, samples: object,
                           *, max_power_gap: int = 30) -> dict:
    """Compare a raw delta with a covered trapezoidal AC-power estimate.

    Samples must bracket both endpoints. Sparse or missing intervals remain
    gaps; never hold the last watt value through an outage. A matching ratio
    is evidence to examine, not proof of a physical unit or battery energy.
    """
    if (type(raw_delta) is not int or not 0 <= raw_delta < 2**32
            or not _number(start) or not _number(end) or end <= start
            or type(samples) is not list or not 2 <= len(samples) <= 4096
            or type(max_power_gap) is not int or not 1 <= max_power_gap <= 300):
        raise EnergyAnalysisError()
    points = []
    for sample in samples:
        if (type(sample) is not dict or set(sample) != {"timestamp", "power_w"}
                or not _number(sample["timestamp"]) or not _number(sample["power_w"], 0, 10000)
                or points and sample["timestamp"] <= points[-1][0]):
            raise EnergyAnalysisError()
        points.append((sample["timestamp"], sample["power_w"]))
    energy, coverage = 0.0, 0.0
    gaps = []
    if points[0][0] > start:
        gaps.append("start_not_bracketed")
    if points[-1][0] < end:
        gaps.append("end_not_bracketed")
    for (t0, p0), (t1, p1) in zip(points, points[1:]):
        left, right = max(start, t0), min(end, t1)
        if right <= left:
            continue
        if t1 - t0 > max_power_gap:
            gaps.append("power_sample_gap")
            continue
        a = p0 + (p1 - p0) * ((left - t0) / (t1 - t0))
        b = p0 + (p1 - p0) * ((right - t0) / (t1 - t0))
        energy += (a + b) / 2 * ((right - left) / 3600)
        coverage += right - left
    complete = not gaps and math.isclose(coverage, end - start, abs_tol=1e-9, rel_tol=1e-12)
    return {"raw_delta": raw_delta, "estimated_ac_energy_wh": energy,
            "covered_seconds": coverage, "requested_seconds": end - start,
            "coverage_complete": complete, "gap_reasons": sorted(set(gaps)),
            "raw_units_per_estimated_wh": raw_delta / energy if complete and energy > 0 else None,
            "physical_units_verified": False, "mcu_snapshot_timing_verified": False,
            "battery_energy_estimate": False}
