"""Constant-cost counter summaries retain honest epochs, gaps and auth."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import time

import pytest

from http_helpers import api_client
from test_history import snapshot
from test_server_readonly_features import ReadOnlyGateway
from solix_link.history import HistoryStore
from solix_link.server import create_app


def test_summary_is_atomic_detached_and_never_scans_sqlite(tmp_path):
    store = HistoryStore(tmp_path / "private" / "h.sqlite", clock=lambda: 1010)
    try:
        assert store.summary()["updated_at"] is None
        for timestamp in (1000, 1005, 1010):
            store.record([snapshot(timestamp, owner_id="PRIVATE-OWNER", error="PRIVATE-ERROR")], now=timestamp)
        queries = []
        store._db.set_trace_callback(queries.append)
        with ThreadPoolExecutor(max_workers=4) as pool:
            reports = list(pool.map(lambda _: store.summary(), range(8)))
        assert queries == []
        report = reports[0]
        assert report["updated_at"] == 1010 and report["estimated"]
        entry = report["stations"][0]
        assert entry["collection_start"] == 1000 and entry["last_seen_timestamp"] == 1010
        assert entry["lifetime_totals"]["ac_input_coverage_seconds"] == 10
        assert entry["lifetime_totals"]["ac_input_energy_kwh_estimate"] == pytest.approx(1000 * 10 / 3600000)
        assert entry["gap_open"] is False
        assert "PRIVATE" not in str(report)
        entry["lifetime_totals"]["gap_count"] = 1234
        report["limits"]["max_samples"] = 1
        assert store.summary() == reports[1]
    finally:
        store.close()
    with pytest.raises(ValueError, match="closed"):
        store.summary()


def test_summary_distinguishes_sampler_time_from_stale_station_report_and_missing_channel(tmp_path):
    with_store = HistoryStore(tmp_path / "private" / "h.sqlite", clock=lambda: 1050)
    try:
        with_store.record([snapshot(1000)], now=1000)
        with_store.record([snapshot(1005, input_w=None)], now=1005)
        entry = with_store.summary()["stations"][0]
        assert entry["gap_open"] is False
        assert entry["lifetime_totals"]["ac_input_coverage_seconds"] == 0
        assert entry["lifetime_totals"]["ac_output_coverage_seconds"] == 5
        with_store.record([snapshot(1005, available=False)], now=1010)
        report = with_store.summary()
        assert report["updated_at"] == 1010
        entry = report["stations"][0]
        assert entry["last_seen_timestamp"] == 1005 and entry["gap_open"] is True
        assert entry["lifetime_totals"]["gap_count"] == 1
    finally:
        with_store.close()


def test_summary_restart_preserves_epoch_totals_without_bridging(tmp_path):
    path = tmp_path / "private" / "h.sqlite"
    first = HistoryStore(path, clock=lambda: 1005)
    first.record([snapshot(1000)], now=1000)
    first.record([snapshot(1005)], now=1005)
    previous = first.summary()["stations"][0]
    first.close()
    second = HistoryStore(path, clock=lambda: 1010)
    try:
        assert second.summary()["updated_at"] is None
        second.record([snapshot(1010)], now=1010)
        current = second.summary()["stations"][0]
        assert current["collection_start"] == previous["collection_start"]
        for key in ("ac_input_energy_kwh_estimate", "ac_output_energy_kwh_estimate",
                    "ac_input_coverage_seconds", "ac_output_coverage_seconds"):
            assert current["lifetime_totals"][key] == previous["lifetime_totals"][key]
        assert current["lifetime_totals"]["gap_count"] == previous["lifetime_totals"]["gap_count"] + 1
    finally:
        second.close()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True, "1"])
def test_summary_rejects_corrupt_counter_values(tmp_path, value):
    store = HistoryStore(tmp_path / "private" / "h.sqlite", clock=lambda: 1000)
    try:
        store.record([snapshot(1000)], now=1000)
        store._states["original"].totals = (value, 0, 0, 0, 0)
        with pytest.raises(ValueError, match="counters"):
            store.summary()
    finally:
        store.close()


def test_summary_http_auth_opt_in_and_configured_station_filter(tmp_path):
    path = tmp_path / "private" / "h.sqlite"
    old = HistoryStore(path)
    old.record([snapshot(time.time(), name="removed-public-name")])
    old.close()
    gateway = ReadOnlyGateway()
    async def run():
        headers = {"Authorization": "Bearer token"}
        async with api_client(create_app(gateway, token="token")) as client:
            assert (await client.get("/history/summary")).status_code == 401
            assert (await client.get("/history/summary", headers=headers)).json() == {
                "schema_version": 1, "enabled": False, "estimated": True, "read_only": True}
        async with api_client(create_app(gateway, token="token", history_file=path)) as client:
            assert (await client.get("/history/summary")).status_code == 401
            response = await client.get("/history/summary", headers=headers)
            report = response.json()
            assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
            assert report["enabled"] and report["recording"] and report["read_only"]
            assert report["updated_at"] is not None
            assert [item["name"] for item in report["stations"]] == ["ups"]
            assert "removed-public-name" not in response.text
            assert (await client.head("/history/summary", headers=headers)).status_code == 200
        assert gateway.calls == []
    asyncio.run(run())


def test_failed_history_summary_does_not_claim_healthy_recording(tmp_path):
    gateway = ReadOnlyGateway()
    good = gateway.snapshots
    gateway.snapshots = lambda: (_ for _ in ()).throw(RuntimeError("PRIVATE-ERROR"))
    async def run():
        async with api_client(create_app(gateway, history_file=tmp_path / "private" / "h.sqlite")) as client:
            await asyncio.sleep(0.02)
            response = await client.get("/history/summary")
            assert response.status_code == 503 and response.json() == {"error": "HistoryUnavailable"}
            gateway.snapshots = good
            assert (await client.get("/devices")).status_code == 200
            assert gateway.calls == []
    asyncio.run(run())
