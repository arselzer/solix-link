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


@pytest.mark.parametrize("pulse_phase,expected_integral_wh", [(1, 360), (4.5, 860)])
def test_temporally_covered_samples_can_miss_or_overweight_short_pulses(pulse_phase, expected_integral_wh):
    # Independent analytic waveform: 360 W base plus a 1000 W, one-second
    # pulse every ten seconds, for one hour. True energy is 460 Wh.
    # This is a hypothetical ideal counter, not a claim about either station.
    samples = [{"timestamp": second, "power_w": 360 + (
        1000 if pulse_phase <= second % 10 < pulse_phase + 1 else 0)}
        for second in range(0, 3601, 5)]
    result = compare_power_interval(460, 0, 3600, samples, max_power_gap=15,
                                    candidate_wh_per_raw_unit=1.0)
    assert result["coverage_complete"] and result["gap_reasons"] == []
    assert result["estimated_ac_energy_wh"] == pytest.approx(expected_integral_wh)
    # A correct 1 Wh/unit meter can fail the rounding-only check solely because
    # the sampled integral differs from the known waveform's energy.
    assert result["candidate_scale"]["rounding_compatible"] is False
    assert result["physical_units_verified"] is False


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


@pytest.mark.parametrize("candidate,expected", [(1.0, False), (0.9, True)])
def test_explicit_scale_hypothesis_distinguishes_nine_second_sampling_from_wh(candidate, expected):
    # Synthetic 360 W for 540 s = 54 Wh, while an assumed ten-second sample
    # conversion could report 60 units. No station observation is embedded.
    samples = [{"timestamp": i, "power_w": 360} for i in range(0, 541, 30)]
    result = compare_power_interval(60, 0, 540, samples, candidate_wh_per_raw_unit=candidate)
    assert result["candidate_scale"]["rounding_compatible"] is expected
    assert result["candidate_scale"]["interval_energy_wh"] == 60 * candidate
    assert not result["physical_units_verified"] and not result["mcu_snapshot_timing_verified"]
    assert "candidate_scale" not in compare_power_interval(60, 0, 540, samples)


@pytest.mark.parametrize("power,expected", [(359.999, True), (360, False), (360.001, False)])
def test_rounding_bound_is_exclusive_at_one_candidate_unit(power, expected):
    result = compare_power_interval(2, 0, 30,
        [{"timestamp": 0, "power_w": power}, {"timestamp": 30, "power_w": power}],
        candidate_wh_per_raw_unit=1.0)
    assert result["candidate_scale"]["rounding_compatible"] is expected


def test_fractional_scale_cannot_qualify_a_rounded_floating_point_boundary():
    result = compare_power_interval(2, 0, 30,
        [{"timestamp": 0, "power_w": 36}, {"timestamp": 30, "power_w": 36}],
        candidate_wh_per_raw_unit=0.1)
    # Binary float can put |0.2 - 0.3| just below 0.1; the exact error is one
    # full unit, which does not satisfy the exclusive rounding bound.
    assert result["candidate_scale"]["rounding_compatible"] is False


@pytest.mark.parametrize("end,step,power", [(600, 600, 360), (30, 30, 0)])
def test_gaps_and_zero_reference_cannot_qualify_a_candidate_scale(end, step, power):
    result = compare_power_interval(1, 0, end,
        [{"timestamp": i, "power_w": power} for i in range(0, end+1, step)],
        candidate_wh_per_raw_unit=0.9)
    assert result["candidate_scale"]["rounding_compatible"] is None
    assert result["candidate_scale"]["difference_to_power_wh"] is None
    assert result["physical_units_verified"] is False


@pytest.mark.parametrize("candidate", [True, False, 0, -1, 1001, float("nan"), float("inf"), "PRIVATE", 10**400])
def test_bad_candidate_scale_fails_with_fixed_text(candidate):
    with pytest.raises(EnergyAnalysisError, match="^InvalidEnergyAnalysis$"):
        compare_power_interval(1, 0, 30, [{"timestamp": 0, "power_w": 120},
            {"timestamp": 30, "power_w": 120}], candidate_wh_per_raw_unit=candidate)


@pytest.mark.parametrize("value", [True, -1, 2**32, float("nan"), "PRIVATE"])
def test_invalid_counter_fails_with_fixed_text(value):
    with pytest.raises(EnergyAnalysisError, match="^InvalidEnergyAnalysis$"):
        analyze_energy_epochs(reports(b=value))


@pytest.mark.parametrize("time,power", [(True, 360), (float("inf"), 360), (0, True), (0, -1), (0, 10001), (0, 10**400)])
def test_bad_power_trace_is_rejected(time, power):
    with pytest.raises(EnergyAnalysisError, match="^InvalidEnergyAnalysis$"):
        compare_power_interval(1, 0, 30, [{"timestamp": time, "power_w": power}, {"timestamp": 30, "power_w": 0}])
