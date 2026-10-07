from concurrent.futures import ThreadPoolExecutor
import os
import sqlite3
import stat

import pytest

from solix_link.history import HistoryStore


class Clock:
    def __init__(self, value=1000):
        self.value = value

    def __call__(self):
        return self.value


def snapshot(timestamp=1000, *, name="original", model="c1000", protocol="prime",
             input_w=1000, output_w=500, battery=80, **fields):
    return {"name": name, "model": model, "protocol": protocol, "connected": True,
            "available": True, "last_seen_timestamp": timestamp,
            "metrics": {"battery_percentage": battery, "ac_input_power_w": input_w,
                        "ac_output_power_w": output_w}, **fields}


@pytest.fixture
def history(tmp_path):
    clock = Clock()
    store = HistoryStore(tmp_path / "private" / "history.sqlite3", clock=clock)
    yield store, clock
    store.close()


def record(store, clock, timestamp, **values):
    clock.value = timestamp
    return store.record([snapshot(timestamp, **values)])


def test_constant_energy_is_trapezoidal_and_coverage_not_elapsed_time(history):
    store, clock = history
    for timestamp in (1000, 1005, 1010, 1015):
        record(store, clock, timestamp)
    result = store.query("original")
    assert result["estimated"] is True
    assert result["totals"] == {
        "ac_input_energy_kwh_estimate": pytest.approx(1000 * 15 / 3600000),
        "ac_output_energy_kwh_estimate": pytest.approx(500 * 15 / 3600000),
        "ac_input_coverage_seconds": 15, "ac_output_coverage_seconds": 15, "gap_count": 0,
    }
    assert [point["timestamp"] for point in result["points"]] == [1000, 1005, 1010, 1015]
    assert result["points"][0]["gap"] is True
    assert result["points"][1]["max_source_interval_seconds"] == 5


def test_variable_power_uses_both_endpoints(history):
    store, clock = history
    record(store, clock, 1000, input_w=0, output_w=100)
    record(store, clock, 1010, input_w=1000, output_w=300)
    totals = store.query("original")["totals"]
    assert totals["ac_input_energy_kwh_estimate"] == pytest.approx(500 * 10 / 3600000)
    assert totals["ac_output_energy_kwh_estimate"] == pytest.approx(200 * 10 / 3600000)


def test_unchanged_cached_reading_is_not_a_new_point_or_gap(history):
    store, clock = history
    record(store, clock, 1000)
    for now in (1001, 1002, 1003, 1004):
        store.record([snapshot(1000)], now=now)
    record(store, clock, 1005)
    result = store.query("original")
    assert store.stats()["samples"] == 2
    assert result["totals"]["ac_input_coverage_seconds"] == 5
    assert result["totals"]["gap_count"] == 0


@pytest.mark.parametrize("change", [
    {"available": False}, {"connected": False}, {"connected": 1},
    {"metrics": None}, {"model": "unknown"}, {"protocol": "unknown"},
    {"model": []}, {"protocol": {}}, {"last_seen_timestamp": 1100},
    {"last_seen_timestamp": float("nan")}, {"last_seen_timestamp": True},
])
def test_unavailable_or_malformed_snapshot_breaks_continuity(history, change):
    store, clock = history
    record(store, clock, 1000)
    store.record([snapshot(1001, **change)], now=1001)
    record(store, clock, 1005)
    record(store, clock, 1010)
    result = store.query("original")
    assert result["totals"]["ac_input_coverage_seconds"] == 5
    assert result["totals"]["gap_count"] == 1
    assert any(point["gap"] and point["ac_input_power_w"] is None for point in result["points"])


@pytest.mark.parametrize("value", [-1, 10001, 65535, True, "1000", float("inf"), float("nan"), 10 ** 500])
def test_invalid_power_is_unknown_and_never_integrated(history, value):
    store, clock = history
    record(store, clock, 1000)
    record(store, clock, 1005, input_w=value)
    record(store, clock, 1010)
    record(store, clock, 1015)
    result = store.query("original")
    assert result["points"][1]["ac_input_power_w"] is None
    assert result["totals"]["ac_input_coverage_seconds"] == 5
    assert result["totals"]["ac_output_coverage_seconds"] == 5
    assert result["totals"]["gap_count"] == 1


@pytest.mark.parametrize("value", [-1, 101, True, "80", float("inf"), float("nan")])
def test_invalid_soc_breaks_pair_and_is_not_persisted(history, value):
    store, clock = history
    record(store, clock, 1000)
    record(store, clock, 1005, battery=value)
    record(store, clock, 1010)
    assert store.query("original")["totals"]["ac_input_coverage_seconds"] == 0
    assert store.query("original")["points"][1]["battery_percentage"] is None


def test_missing_fields_are_unknown_not_zero_and_no_total_power_fallback(history):
    store, clock = history
    item = snapshot()
    item["metrics"] = {"total_input_power_w": 300, "total_output_power_w": 200}
    store.record([item])
    record(store, clock, 1005, output_w=None, battery=None)
    record(store, clock, 1010)
    result = store.query("original")
    assert result["totals"]["ac_input_coverage_seconds"] == 5
    assert result["totals"]["ac_output_coverage_seconds"] == 0
    assert result["points"][0]["ac_input_power_w"] is None
    assert result["points"][1]["battery_percentage"] is None


def test_conflicting_duplicate_breaks_even_when_timestamp_did_not_change(history):
    store, clock = history
    record(store, clock, 1000)
    store.record([snapshot(1000, input_w=100)], now=1001)
    record(store, clock, 1005)
    record(store, clock, 1010)
    assert store.query("original")["totals"]["ac_input_coverage_seconds"] == 5
    assert store.query("original")["totals"]["gap_count"] == 1


def test_duplicate_names_in_one_batch_do_not_integrate_either_endpoint(history):
    store, clock = history
    record(store, clock, 1000)
    store.record([snapshot(1005), snapshot(1005, input_w=10)], now=1005)
    record(store, clock, 1010)
    assert store.query("original")["totals"]["ac_input_coverage_seconds"] == 0


@pytest.mark.parametrize("protocol,age", [("native_mqtt", 30), ("prime", 90), ("legacy", 90)])
def test_stale_duplicate_does_not_hold_last_watts(history, protocol, age):
    store, clock = history
    record(store, clock, 1000, protocol=protocol)
    for now in range(1001, 1000 + age + 1):
        store.record([snapshot(1000, protocol=protocol)], now=now)
    record(store, clock, 1000 + age + 1, protocol=protocol)
    result = store.query("original")
    assert result["totals"]["ac_input_coverage_seconds"] == 0
    assert result["totals"]["gap_count"] == 1


def test_long_gap_does_not_integrate_unknown_interval(history):
    store, clock = history
    record(store, clock, 1000)
    record(store, clock, 1015)
    record(store, clock, 1031)
    record(store, clock, 1036)
    result = store.query("original")
    assert result["totals"]["ac_input_coverage_seconds"] == 20
    assert result["totals"]["gap_count"] == 1
    assert result["points"][-2]["gap"] is True


@pytest.mark.parametrize("field,value", [("protocol", "legacy"), ("model", "c1000_gen2")])
def test_transport_or_model_changes_break_pairs(history, field, value):
    store, clock = history
    record(store, clock, 1000)
    record(store, clock, 1005, **{field: value})
    record(store, clock, 1010, **{field: value})
    result = store.query("original")
    assert result["totals"]["ac_input_coverage_seconds"] == 5
    assert result["totals"]["gap_count"] == 1


def test_report_rollback_breaks_and_replay_cannot_recreate_points(history):
    store, clock = history
    record(store, clock, 1000)
    record(store, clock, 1005)
    store.record([snapshot(1002)], now=1006)
    store.record([snapshot(1005)], now=1007)
    record(store, clock, 1010)
    record(store, clock, 1015)
    result = store.query("original")
    assert result["totals"]["ac_input_coverage_seconds"] == 10
    assert 1002 not in [point["timestamp"] for point in result["points"]]


def test_wall_clock_rollback_breaks_until_clock_catches_up(history):
    store, clock = history
    record(store, clock, 1000)
    record(store, clock, 1005)
    store.record([snapshot(1004)], now=1004)
    store.record([snapshot(1005)], now=1004.5)
    record(store, clock, 1010)
    record(store, clock, 1015)
    assert store.query("original")["totals"]["ac_input_coverage_seconds"] == 10
    assert store.query("original")["totals"]["gap_count"] == 1


def test_omitted_station_breaks_continuity(history):
    store, clock = history
    record(store, clock, 1000)
    store.record([], now=1001)
    record(store, clock, 1005)
    record(store, clock, 1010)
    assert store.query("original")["totals"]["ac_input_coverage_seconds"] == 5


def test_restart_preserves_totals_and_highwater_without_carrying_endpoint(tmp_path):
    clock = Clock()
    path = tmp_path / "private" / "history.sqlite3"
    store = HistoryStore(path, clock=clock)
    record(store, clock, 1000)
    record(store, clock, 1005)
    before = store.query("original")["lifetime_totals"]
    store.close()
    clock.value = 1006
    reopened = HistoryStore(path, clock=clock)
    reopened.record([snapshot(1005)])
    record(reopened, clock, 1010)
    record(reopened, clock, 1015)
    result = reopened.query("original")
    assert result["lifetime_totals"]["ac_input_coverage_seconds"] == before["ac_input_coverage_seconds"] + 5
    assert result["lifetime_totals"]["gap_count"] == 1
    assert len([point for point in result["points"] if point["timestamp"] == 1005
                and point["ac_input_power_w"] is not None]) == 1
    reopened.close()


def test_gap_marker_precedes_newly_observed_delayed_report(history):
    store, clock = history
    record(store, clock, 1000)
    store.record([snapshot(1000, available=False)], now=1006)
    store.record([snapshot(1005)], now=1007)
    store.record([snapshot(1010)], now=1011)
    clock.value = 1011
    result = store.query("original")
    marker = next(point for point in result["points"] if point["ac_input_power_w"] is None)
    assert marker["timestamp"] == 1000
    assert result["points"][-1]["gap"] is False
    assert result["totals"]["ac_input_coverage_seconds"] == 5


def test_retention_and_row_limit_do_not_destroy_lifetime_totals(tmp_path):
    clock = Clock()
    store = HistoryStore(tmp_path / "private" / "history.sqlite3", clock=clock, retention_days=1, max_samples=3)
    for timestamp in range(1000, 1030, 5):
        record(store, clock, timestamp)
    result = store.query("original")
    assert store.stats()["samples"] == 3
    assert result["lifetime_totals"]["ac_input_coverage_seconds"] == 25
    assert result["totals"]["ac_input_coverage_seconds"] == 10
    assert result["collection_start"] == 1000
    assert result["window"]["since"] == 1015
    clock.value = 1025 + 86401
    store.record([], now=clock.value)
    result = store.query("original", since=0)
    assert all(point["timestamp"] >= clock.value - 86400 for point in result["points"])
    assert result["lifetime_totals"]["ac_input_coverage_seconds"] == 25
    assert result["points"] == []
    assert result["totals"]["ac_input_coverage_seconds"] == 0
    assert store.stats()["samples"] == 0
    store.close()


def test_window_totals_include_only_whole_pairs(history):
    store, clock = history
    for timestamp in (1000, 1005, 1010):
        record(store, clock, timestamp)
    result = store.query("original", since=1002, until=1010)
    assert result["totals"]["ac_input_coverage_seconds"] == 5
    assert result["window"] == {"since": 1002, "until": 1010}


def test_downsampling_propagates_hidden_outage_and_source_intervals(history):
    store, clock = history
    for timestamp in (1000, 1005, 1010):
        record(store, clock, timestamp)
    store.record([snapshot(1011, available=False)], now=1011)
    for timestamp in (1015, 1020, 1025):
        record(store, clock, timestamp)
    result = store.query("original", limit=2)
    assert result["truncated"] is True
    assert len(result["points"]) == 2
    assert result["points"][-1]["gap"] is True
    assert result["points"][-1]["max_source_interval_seconds"] == 5
    assert result["totals"]["ac_input_coverage_seconds"] == 20


def test_downsampling_does_not_bridge_missing_field(history):
    store, clock = history
    record(store, clock, 1000)
    record(store, clock, 1005, battery=None)
    record(store, clock, 1010)
    assert store.query("original", limit=2)["points"][-1]["gap"] is True


@pytest.mark.parametrize("bounds", [
    {"limit": 0}, {"limit": 2001}, {"limit": True}, {"limit": 1.5},
    {"since": 1001, "until": 1000}, {"until": 1006}, {"since": -1},
    {"since": float("nan")}, {"until": float("inf")}, {"since": True},
])
def test_query_rejects_malformed_bounds_without_echoing_fields(history, bounds):
    store, clock = history
    record(store, clock, 1000)
    with pytest.raises(ValueError):
        store.query("original", **bounds)


def test_query_window_hard_cap(history):
    store, clock = history
    record(store, clock, 1000)
    with pytest.raises(ValueError):
        store.query("original", since=0, until=32 * 86400, now=32 * 86400)
    assert store.query("original", limit=1)["points"][0]["timestamp"] == 1000


def test_unknown_and_invalid_names_and_closed_store(history):
    store, _ = history
    with pytest.raises(KeyError):
        store.query("missing")
    with pytest.raises(ValueError):
        store.query("\x00secret")
    store.close()
    store.close()
    for action in (store.stats, lambda: store.record([]), lambda: store.query("original")):
        with pytest.raises(ValueError, match="closed"):
            action()


@pytest.mark.parametrize("batch", [None, [1], [snapshot()] * 65])
def test_invalid_batches_reset_continuity(history, batch):
    store, clock = history
    record(store, clock, 1000)
    with pytest.raises(ValueError):
        store.record(batch)
    record(store, clock, 1005)
    assert store.query("original")["totals"]["ac_input_coverage_seconds"] == 0


def test_private_information_is_never_persisted_or_returned(tmp_path):
    path = tmp_path / "private" / "history.sqlite3"
    store = HistoryStore(path, clock=Clock())
    item = snapshot(address="PRIVATE-MAC", account_id="PRIVATE-ACCOUNT", error="PRIVATE-ERROR")
    item["metrics"].update(client_id="PRIVATE-ID", raw_tlvs="PRIVATE-CAPTURE")
    store.record([item])
    assert "PRIVATE" not in repr(store.query("original"))
    assert set(store.stats()) == {"samples", "stations", "retention_days", "max_gap_seconds",
                                  "max_samples", "max_points", "estimated", "collection_start"}
    store.close()
    assert b"PRIVATE" not in path.read_bytes()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


@pytest.mark.parametrize("name", [
    "Office · C1000 Gen 2", "Büro ⚡", "O'Reilly \"UPS\"",
    "x'); DROP TABLE stations; --", " leading and trailing ", "A" * 64,
])
def test_public_names_preserve_spaces_unicode_and_quotes_exactly(history, name):
    store, clock = history
    record(store, clock, 1000, name=name)
    record(store, clock, 1005, name=name)
    result = store.query(name)
    assert result["name"] == name
    assert result["totals"]["ac_input_coverage_seconds"] == 5
    assert store.stats()["stations"] == 1
    if name != name.strip():
        with pytest.raises(KeyError):
            store.query(name.strip())
    path = store._path
    store.close()
    reopened = HistoryStore(path, clock=clock)
    assert reopened.query(name)["name"] == name
    reopened.close()


@pytest.mark.parametrize("name", [
    "", " ", "\x00name", "line\nbreak", "tab\tname", "escape\x1bname",
    "\ud800", "A" * 65, None, [], False,
])
def test_public_names_reject_empty_controls_unprintable_and_oversized(history, name):
    store, clock = history
    store.record([snapshot(name=name)])
    assert store.stats()["stations"] == 0
    with pytest.raises(ValueError, match="name"):
        store.query(name)


@pytest.mark.parametrize("kind", ["file_link", "parent_link", "hard_link", "public_file", "public_parent", "sidecar_link", "fifo"])
def test_history_refuses_nonprivate_or_nonregular_paths(tmp_path, kind):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    path = private / "history.sqlite3"
    real = private / "real.sqlite3"
    if kind == "file_link":
        real.touch(mode=0o600)
        path.symlink_to(real)
    elif kind == "parent_link":
        link = tmp_path / "link"
        link.symlink_to(private, target_is_directory=True)
        path = link / path.name
    elif kind == "hard_link":
        real.touch(mode=0o600)
        os.link(real, path)
    elif kind == "public_file":
        path.touch(mode=0o644)
    elif kind == "public_parent":
        private.chmod(0o755)
    elif kind == "sidecar_link":
        (private / "history.sqlite3-journal").symlink_to(real)
    else:
        os.mkfifo(path, 0o600)
    with pytest.raises(ValueError, match="History"):
        HistoryStore(path, clock=Clock())


def test_database_has_one_process_owner_until_close(tmp_path):
    path = tmp_path / "private" / "history.sqlite3"
    store = HistoryStore(path, clock=Clock())
    with pytest.raises(OSError):
        HistoryStore(path, clock=Clock())
    store.close()
    reopened = HistoryStore(path, clock=Clock())
    reopened.close()


def test_version_one_migration_preserves_energy_and_adds_stable_generation(tmp_path):
    path = tmp_path / "private" / "history.sqlite3"
    clock = Clock()
    store = HistoryStore(path, clock=clock)
    record(store, clock, 1000)
    record(store, clock, 1005)
    before = store.query("original")
    store.close()
    # Construct the actual previous schema, including retained samples/totals.
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE stations DROP COLUMN generation")
        db.execute("PRAGMA user_version=1")
    reopened = HistoryStore(path, clock=clock)
    after = reopened.query("original")
    assert after["points"] == before["points"]
    assert after["lifetime_totals"] == before["lifetime_totals"]
    assert after["collection_start"] == before["collection_start"]
    assert len(after["generation"]) == 32
    assert reopened.summary()["stations"][0]["generation"] == after["generation"]
    reopened.close()
    again = HistoryStore(path, clock=clock)
    assert again.query("original")["generation"] == after["generation"]
    again.close()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2


def test_failed_generation_migration_rolls_back_schema_and_version(tmp_path, monkeypatch):
    import solix_link.history as module
    path = tmp_path / "private" / "history.sqlite3"
    store = HistoryStore(path, clock=Clock())
    store.record([snapshot()])
    store.close()
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE stations DROP COLUMN generation")
        db.execute("PRAGMA user_version=1")
    def fail():
        raise OSError("synthetic random source failure")
    monkeypatch.setattr(module.uuid, "uuid4", fail)
    with pytest.raises(OSError):
        HistoryStore(path, clock=Clock())
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert "generation" not in {row[1] for row in db.execute("PRAGMA table_info(stations)")}
        assert db.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 1


@pytest.mark.parametrize("model,protocol", [("c300", "legacy"), ("c1000", "prime"),
    ("c1000_gen2", "native_mqtt"), ("c2000_gen2", "native_mqtt")])
def test_all_supported_models_supply_kwh_with_no_station_commands(history, model, protocol):
    store, clock = history
    record(store, clock, 1000, model=model, protocol=protocol)
    record(store, clock, 1005, model=model, protocol=protocol)
    row = store.summary()["stations"][0]
    assert row["lifetime_totals"]["ac_output_energy_kwh_estimate"] == pytest.approx(500*5/3600000)
    assert row["generation"] == store.query("original")["generation"]


def test_database_version_rejected_and_descriptor_released(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    path = private / "history.sqlite3"
    path.touch(mode=0o600)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError, match="version"):
        HistoryStore(path, clock=Clock())
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=0")
    store = HistoryStore(path, clock=Clock())
    store.close()


def test_unrelated_sqlite_database_is_not_modified(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    path = private / "unrelated.sqlite3"
    path.touch(mode=0o600)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE unrelated (value TEXT)")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Unrecognized"):
        HistoryStore(path, clock=Clock())
    assert path.read_bytes() == before


@pytest.mark.parametrize("options", [
    {"retention_days": 0}, {"retention_days": 1.5}, {"retention_days": True},
    {"max_samples": 0}, {"max_points": 2001}, {"max_gap_seconds": 0},
    {"max_gap_seconds": float("nan")}, {"max_power_w": 65535},
])
def test_invalid_limits_fail_before_creating_database(tmp_path, options):
    path = tmp_path / "private" / "history.sqlite3"
    with pytest.raises(ValueError):
        HistoryStore(path, clock=Clock(), **options)
    assert not path.exists()


def test_serialized_thread_calls_work_with_different_pool_threads(history):
    store, _ = history
    store.record([snapshot()], now=1000)

    def worker(index):
        if index % 3 == 0:
            return store.record([snapshot()], now=1001)
        if index % 3 == 1:
            return store.query("original", now=1001)
        return store.stats()

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert len(list(pool.map(worker, range(60)))) == 60
    assert store.stats()["samples"] == 1
    assert store.query("original", now=1001)["lifetime_totals"]["gap_count"] == 0
