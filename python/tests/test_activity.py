"""Synthetic cached observations; no BLE, MQTT or real station executor."""

from copy import deepcopy
import json
import os
import sqlite3

import pytest

from solix_link.activity import ActivityStore, UpsTracker
from solix_link.settings_compare import compare_settings
from solix_link.settings_export import export_settings
from solix_link.cli import main


def station(at=1000, **metrics):
    return {"name": "test", "model": "c1000_gen2", "protocol": "native_mqtt",
        "available": True, "connected": True, "last_seen_timestamp": at,
        "metrics": {"battery_percentage": 50, "ac_input_connected": 1,
                    "backup_reserve_percentage": 20, "ac_charging_power_limit_w": 300, **metrics}}


def kinds(events):
    return [event["kind"] for event in events]


def test_mains_debounce_requires_distinct_source_reports_not_cache_polls():
    tracker = UpsTracker()
    assert tracker.observe([station()], now=1000) == []
    assert tracker.observe([station(1001, ac_input_connected=0)], now=1001) == []
    assert tracker.observe([station(1001, ac_input_connected=0)], now=1010) == []
    assert kinds(tracker.observe([station(1011, ac_input_connected=0)], now=1011)) == ["mains_lost"]
    assert tracker.observe([station(1012, ac_input_connected=1)], now=1012) == []
    assert kinds(tracker.observe([station(1017, ac_input_connected=1)], now=1017)) == ["mains_restored"]


@pytest.mark.parametrize("unknown", [None, True, "0", 2, -1])
def test_unknown_mains_never_becomes_outage(unknown):
    tracker = UpsTracker()
    tracker.observe([station()], now=1000)
    for at in (1005, 1010):
        assert tracker.observe([station(at, ac_input_connected=unknown)], now=at) == []
        assert tracker.describe(station(at, ac_input_connected=unknown), now=at)["mains_connected"] is None


def test_stale_disconnect_and_regression_are_communication_events_only():
    tracker = UpsTracker()
    tracker.observe([station()], now=1000)
    assert kinds(tracker.observe([station()], now=1030)) == ["telemetry_lost"]
    assert tracker.describe(station(), now=1030)["mains_connected"] is None
    assert kinds(tracker.observe([station(1031, ac_input_connected=0)], now=1031)) == ["telemetry_restored"]
    events = tracker.observe([station(1036, ac_input_connected=0)], now=1036)
    assert kinds(events) == ["mains_lost"] and events[0]["details"]["interval_unknown"]
    assert kinds(tracker.observe([station(1035)], now=1037)) == ["telemetry_lost"]
    assert not tracker.describe(station(1035), now=1037)["telemetry_available"]


def test_model_change_resets_baseline_without_inheriting_preferences():
    tracker = UpsTracker()
    tracker.observe([station()], now=1000)
    value = station(1001, ac_input_connected=0)
    value["model"] = "c2000_gen2"
    assert tracker.observe([value], now=1001) == []


def test_clock_regression_starts_new_baseline_instead_of_inventing_transitions():
    tracker = UpsTracker()
    tracker.observe([station()], now=1000)
    assert tracker.observe([station(995, ac_input_connected=0)], now=995) == []
    assert tracker.observe([station(1001, ac_input_connected=0)], now=1001) == []


def test_low_reserve_has_hysteresis_and_does_not_oscillate_at_100():
    tracker = UpsTracker()
    assert kinds(tracker.observe([station(battery_percentage=19)], now=1000)) == ["battery_low"]
    assert tracker.observe([station(1001, battery_percentage=21)], now=1001) == []
    assert tracker.describe(station(1001, battery_percentage=21), now=1001)["battery_reserve_low"] is True
    assert kinds(tracker.observe([station(1002, battery_percentage=25)], now=1002)) == ["battery_recovered"]
    tracker = UpsTracker()
    tracker.observe([station(battery_percentage=99, backup_reserve_percentage=100)], now=1000)
    assert "battery_recovered" in kinds(tracker.observe([station(1001, battery_percentage=100,
        backup_reserve_percentage=100)], now=1001))
    assert tracker.observe([station(1002, battery_percentage=100, backup_reserve_percentage=100)], now=1002) == []


def test_invalid_or_missing_battery_and_reserve_remain_unknown_or_use_floor():
    tracker = UpsTracker()
    value = station(battery_percentage=True, backup_reserve_percentage="private")
    tracker.observe([value], now=1000)
    public = tracker.describe(value, now=1000)
    assert public["battery_reserve_low"] is None and public["effective_reserve_percentage"] == 20


def test_settings_observation_ignores_first_missing_new_and_repeated_fields():
    tracker = UpsTracker()
    tracker.observe([station()], now=1000)
    events = tracker.observe([station(1001, ac_charging_power_limit_w=400)], now=1001)
    assert kinds(events) == ["settings_changed"]
    assert events[0]["details"]["changes"] == [{"field": "ac_charging_power_limit_w", "before": 300, "after": 400}]
    assert not events[0]["details"]["field_freshness_verified"]
    value = station(1002)
    del value["metrics"]["ac_charging_power_limit_w"]
    assert tracker.observe([value], now=1002) == []
    assert tracker.observe([station(1003, ac_charging_power_limit_w=500)], now=1003) == []


def test_partial_comparison_sanitizes_identity_and_ignores_plan_timestamp_changes():
    value = station()
    value["tou_plan_readback"] = dict(schema_version=1, enabled=False, reported_at=1000,
        source="status_d9", periods=[])
    left = export_settings(value, now=1001)
    right = deepcopy(left)
    right.update(owner_id="PRIVATE", serial_number="PRIVATE")
    right["settings"].update(owner_id="PRIVATE", ac_output_enabled=0, ac_charging_power_limit_w=500)
    right["tou_plan_readback"]["reported_at"] = 1001
    result = compare_settings(left, right)
    assert result["changes"] == [{"field": "ac_charging_power_limit_w", "before": 300, "after": 500}]
    assert "PRIVATE" not in json.dumps(result) and not result["restore_supported"]
    assert not compare_settings(left, {**right, "model": "c2000_gen2"})["compatible"]


def test_offline_diff_does_not_read_station_configuration(tmp_path, monkeypatch, capsys):
    from solix_link import cli
    def forbidden(*args, **kwargs):
        pytest.fail("Offline comparison must not access devices or config")
    monkeypatch.setattr(cli, "load_config", forbidden)
    monkeypatch.setattr(cli, "SolixMonitor", forbidden)
    before, after = tmp_path/"before.json", tmp_path/"after.json"
    before.write_text(json.dumps(export_settings(station(), now=1001)))
    after.write_text(json.dumps(export_settings(station(1001, ac_charging_power_limit_w=500), now=1002)))
    assert main(["settings-diff", "--before", str(before), "--after", str(after)]) == 0
    assert json.loads(capsys.readouterr().out)["changes"][0]["after"] == 500


@pytest.mark.parametrize("persist", [False, True])
def test_activity_bounds_privacy_copy_and_cursor(tmp_path, persist):
    tmp_path.chmod(0o700)
    store = ActivityStore(tmp_path / "activity.sqlite" if persist else None, max_records=3)
    for index in range(5):
        store.append(dict(name="one" if index % 2 else "two", timestamp=1000+index,
                          kind="telemetry_lost", details={}))
    result = store.query({"one"}, now=1005)
    assert [row["id"] for row in result["records"]] == [4]
    result["records"][0]["details"]["private"] = "never retained"
    assert "private" not in str(store.query({"one"}, now=1005))
    assert [row["id"] for row in store.query({"one", "two"}, after=2, limit=2, now=1005)["records"]] == [3, 4]
    assert store.query({"one", "two"}, now=1000+8*86400)["records"] == []
    for args in ({"limit": 201}, {"after": -1}, {"after": 2**64}, {"limit": True}):
        with pytest.raises(ValueError):
            store.query({"one"}, **args)
    store.close()
    if persist:
        path = tmp_path / "activity.sqlite"
        assert path.stat().st_mode & 0o077 == 0
        restarted = ActivityStore(path)
        assert restarted.query({"one"}, now=1005)["records"][0]["id"] == 4
        restarted.close()


def test_activity_refuses_unsafe_or_unrelated_files_and_concurrent_writer(tmp_path):
    tmp_path.chmod(0o700)
    unsafe = tmp_path / "unsafe"
    unsafe.write_text("PRIVATE")
    unsafe.chmod(0o644)
    with pytest.raises(ValueError, match="private activity"):
        ActivityStore(unsafe)
    link = tmp_path / "link"
    link.symlink_to(unsafe)
    with pytest.raises(ValueError):
        ActivityStore(link)
    path = tmp_path / "activity.sqlite"
    store = ActivityStore(path)
    with pytest.raises(ValueError):
        ActivityStore(path)
    store.close()
    unrelated = tmp_path / "unrelated.sqlite"
    db = sqlite3.connect(unrelated)
    db.execute("CREATE TABLE unrelated (value TEXT)")
    db.close()
    os.chmod(unrelated, 0o600)
    with pytest.raises(ValueError):
        ActivityStore(unrelated)
