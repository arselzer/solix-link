"""The deferred restart gate must reject unsafe or incomplete private evidence."""
import copy
import importlib.util
from pathlib import Path

import pytest

from solix_link.history import HistoryStore

spec = importlib.util.spec_from_file_location(
    "verify_history_restart", Path(__file__).resolve().parents[2] / "tools/verify_history_restart.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


def observation():
    return {"completed": True, "read_only": True, "duration_hours": 48,
            "interval_seconds": 30, "stop_reason": "duration_reached", "request_errors": 0,
            "samples": 5760, "started_at": 1000000, "finished_at": 1172800,
            "baseline": {model: {"ac_output_enabled": 1} for model in checks.MODELS},
            "stations": {model: {"samples": 5760, "unavailable_samples": 0,
                "empty_settings_samples": 0, "nonempty_changed_samples": 0,
                "ac_not_enabled_samples": 0, "max_report_age_seconds": 2}
                for model in checks.MODELS}}


def test_completed_observation_gate():
    assert checks.verify_observation(observation())["station_count"] == 3


@pytest.mark.parametrize("key,value", [
    ("completed", False), ("completed", 1), ("read_only", False),
    ("request_errors", 1), ("request_errors", False), ("duration_hours", 24), ("interval_seconds", 60),
    ("stop_reason", "size_limit"), ("samples", 4999), ("samples", 5762),
    ("samples", True), ("finished_at", 1000000), ("finished_at", float("nan")),
    ("finished_at", 1173000), ("finished_at", 10**400), ("stations", {}), ("baseline", {}),
])
def test_observation_fails_closed(key, value):
    data = observation()
    data[key] = value
    with pytest.raises(ValueError):
        checks.verify_observation(data)


@pytest.mark.parametrize("key,value", [
    ("samples", 5000), ("unavailable_samples", 1), ("empty_settings_samples", 1),
    ("nonempty_changed_samples", 1), ("ac_not_enabled_samples", 1),
    ("max_report_age_seconds", 31), ("max_report_age_seconds", float("inf")),
])
def test_one_station_failure_blocks_restart(key, value):
    data = observation()
    data["stations"]["c2000_gen2"][key] = value
    with pytest.raises(ValueError):
        checks.verify_observation(data)


@pytest.fixture
def evidence(tmp_path):
    path = tmp_path / "private/readings.sqlite3"
    def snapshots(at):
        return [{"name": model, "model": model, "protocol": "native_mqtt",
                 "connected": True, "available": True, "last_seen_timestamp": at,
                 "metrics": {"battery_percentage": 50, "ac_input_power_w": 300,
                             "ac_output_power_w": 100}} for model in sorted(checks.MODELS)]
    def capture(store, max_id=0):
        return {"stations": [dict(row) for row in store._db.execute("SELECT * FROM stations")],
                "max_sample_id": store._db.execute("SELECT MAX(id) FROM samples").fetchone()[0],
                "new_samples": [dict(row) for row in store._db.execute(
                    "SELECT * FROM samples WHERE id>? ORDER BY id", (max_id,))]}
    first = HistoryStore(path, clock=lambda: 100)
    first.record(snapshots(100), now=100)
    first.record(snapshots(101), now=101)
    before = capture(first)
    first.close()
    second = HistoryStore(path, clock=lambda: 102)
    second.record(snapshots(101), now=102)  # Duplicate retained report; gap marker only.
    second.record(snapshots(102), now=102)
    second.record(snapshots(103), now=103)
    after = capture(second, before["max_sample_id"])
    second.close()
    return before, after


def test_actual_store_restart_preserves_counters_and_breaks_integration(evidence):
    assert checks.verify_restart(*evidence)["restart_energy_not_bridged"] is True


@pytest.mark.parametrize("mutation", [
    "missing_station", "duplicate_station", "new_epoch", "model_changed", "protocol_changed",
    "counter_reset", "counter_increase_missing", "missing_sample", "reordered_samples",
    "bridged_energy", "bridged_coverage", "missing_gap", "interval_bridged", "no_gap_count",
    "no_new_measurement", "nonfinite", "wrong_gap_delta", "boolean_gap",
    "max_id_mismatch", "missing_zero_row", "missing_measurement_highwater", "id_hole",
])
def test_restart_evidence_cannot_pass_after_damage(evidence, mutation):
    before, after = copy.deepcopy(evidence)
    station = after["stations"][0]
    first = next(s for s in after["new_samples"]
                 if s["name"] == station["name"] and s["battery"] is not None)
    prior = next(s for s in before["stations"] if s["name"] == station["name"])
    if mutation == "missing_station": after["stations"].pop()
    elif mutation == "duplicate_station": after["stations"][1] = station.copy()
    elif mutation == "new_epoch": station["collection_start"] += 1
    elif mutation == "model_changed": station["model"] = "c300"
    elif mutation == "protocol_changed": station["protocol"] = "legacy"
    elif mutation == "counter_reset": station["input_kwh"] = 0
    elif mutation == "counter_increase_missing": station["output_kwh"] += 0.001
    elif mutation == "missing_sample": after["new_samples"].pop()
    elif mutation == "reordered_samples": after["new_samples"].reverse()
    elif mutation == "bridged_energy": first["input_kwh"] = 0.01
    elif mutation == "bridged_coverage": first["input_seconds"] = 1
    elif mutation == "missing_gap": first["gap"] = 0
    elif mutation == "interval_bridged": first["interval_start"] = prior["highwater"]
    elif mutation == "no_gap_count": station["gaps"] = prior["gaps"]
    elif mutation == "no_new_measurement": station["highwater"] = prior["highwater"]
    elif mutation == "nonfinite": station["input_kwh"] = float("nan")
    elif mutation == "wrong_gap_delta": station["gaps"] += 1
    elif mutation == "boolean_gap": first["gap"] = True
    elif mutation == "max_id_mismatch": after["max_sample_id"] += 1
    elif mutation == "missing_zero_row":
        # Drop an all-zero gap marker without changing the declared final ID.
        after["new_samples"] = [s for s in after["new_samples"]
                                if s is not after["new_samples"][0]]
    elif mutation == "missing_measurement_highwater": station["highwater"] += 1
    elif mutation == "id_hole":
        after["new_samples"][0]["id"] += 1
    with pytest.raises(ValueError):
        checks.verify_restart(before, after)


def test_cli_rejects_duplicate_private_evidence_keys(tmp_path):
    path = tmp_path / "capture.json"
    path.write_text('{"completed":true,"completed":false}')
    with pytest.raises(ValueError): checks.read(path)
