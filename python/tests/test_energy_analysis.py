from copy import deepcopy
import json

import pytest

from solix_link.energy_analysis import EnergyAnalysisError, analyze_energy_epochs, compare_power_interval


def reports(a=10, b=20, t0=0, t1=600):
    return [{"timestamp": t0, "groups": {"standard": {"ac_input_energy_raw": a, "ac_output_energy_raw": a}}},
            {"timestamp": t1, "groups": {"standard": {"ac_input_energy_raw": b, "ac_output_energy_raw": b}}}]


def test_counter_analysis_is_raw_and_does_not_mutate_or_publish_absolute_times():
    r = reports()
    old = deepcopy(r)
    result = analyze_energy_epochs(r)
    assert result["intervals"][0]["raw_deltas"] == {"standard.ac_input_energy_raw": 10, "standard.ac_output_energy_raw": 10}
    assert not result["units_verified"] and not result["lifetime_total_available"]
    assert not result["mcu_snapshot_timing_verified"] and "timestamp" not in json.dumps(result)
    assert r == old


@pytest.mark.parametrize("a,b", [(20, 0), (2**32-1, 0), (2**32-1, 1)])
def test_decrease_never_assumes_wrap_or_integrates_across_reset(a, b):
    result = analyze_energy_epochs(reports(a, b))
    row = result["intervals"][0]
    assert row["analysis_segment"] == 1 and not row["raw_deltas"]
    assert row["boundary_reasons"] == ["counter_decrease_reset_wrap_or_reorder"]


@pytest.mark.parametrize("t0,t1,reason", [(600, 600, "clock_nonincreasing"), (600, 0, "clock_nonincreasing"),
                                       (0, 3601, "report_gap")])
def test_clock_and_gap_boundaries(t0, t1, reason):
    row = analyze_energy_epochs(reports(t0=t0, t1=t1))["intervals"][0]
    assert row["boundary_reasons"] == [reason] and not row["raw_deltas"]


def test_missing_counter_does_not_become_zero_or_bridge_later_reports():
    r = reports()
    del r[1]["groups"]["standard"]["ac_output_energy_raw"]
    result = analyze_energy_epochs(r)
    assert result["intervals"][0]["raw_deltas"] == {}
    assert result["intervals"][0]["missing_counters"] == ["standard.ac_output_energy_raw"]


def test_constant_load_comparison_has_no_calibration_claim_even_with_unity_ratio():
    samples = [{"timestamp": i, "power_w": 360} for i in range(0, 601, 30)]
    result = compare_power_interval(60, 0, 600, samples)
    assert result["coverage_complete"] and result["estimated_ac_energy_wh"] == 60
    assert result["raw_units_per_estimated_wh"] == 1
    assert not result["physical_units_verified"] and not result["battery_energy_estimate"]


def test_interpolation_clips_endpoints_and_integrates_linear_ramp():
    result = compare_power_interval(1, 5, 25, [{"timestamp": 0, "power_w": 0}, {"timestamp": 30, "power_w": 360}])
    assert result["coverage_complete"]
    assert result["estimated_ac_energy_wh"] == pytest.approx(1)


@pytest.mark.parametrize("samples,reason", [
    ([{"timestamp": 0, "power_w": 360}, {"timestamp": 600, "power_w": 360}], "power_sample_gap"),
    ([{"timestamp": 10, "power_w": 360}, {"timestamp": 30, "power_w": 360}], "start_not_bracketed"),
    ([{"timestamp": 0, "power_w": 360}, {"timestamp": 20, "power_w": 360}], "end_not_bracketed"),
])
def test_sparse_power_or_missing_endpoint_never_yields_ratio(samples, reason):
    result = compare_power_interval(3, 0, 30, samples)
    assert not result["coverage_complete"] and result["raw_units_per_estimated_wh"] is None
    assert reason in result["gap_reasons"]


def test_zero_power_reference_does_not_divide_by_zero():
    result = compare_power_interval(0, 0, 30, [{"timestamp": 0, "power_w": 0}, {"timestamp": 30, "power_w": 0}])
    assert result["raw_units_per_estimated_wh"] is None


@pytest.mark.parametrize("value", [True, -1, 2**32, float("nan"), "PRIVATE"])
def test_invalid_counter_fails_with_fixed_text(value):
    with pytest.raises(EnergyAnalysisError, match="^InvalidEnergyAnalysis$"):
        analyze_energy_epochs(reports(b=value))


@pytest.mark.parametrize("time,power", [(True, 360), (float("inf"), 360), (0, True), (0, -1), (0, 10001), (0, 10**400)])
def test_bad_power_trace_is_rejected(time, power):
    with pytest.raises(EnergyAnalysisError, match="^InvalidEnergyAnalysis$"):
        compare_power_interval(1, 0, 30, [{"timestamp": time, "power_w": power}, {"timestamp": 30, "power_w": 0}])
