"""Synthetic order/reset/storage boundaries for observed native AC accounting."""

from copy import deepcopy
import json

import pytest

from solix_link.energy_report import REPORT_NAME, decode_energy_events
from solix_link.energy_store import NativeEnergyStore
from solix_link.energy_values import MAX_REPORT_AGE, validate_native_energy
from test_energy_report import field, request

NOW = 1700000000


def report(at=NOW, value=100):
    return {"protobuf_name": REPORT_NAME, "units_verified": False, "event_timestamp": at,
            "groups": {"standard": {"ac_input_energy_raw": value, "ac_output_energy_raw": value + 1}}}


def ingest(store, at=NOW, value=100, *, receipt=None, firmware="1.1.4.9", reports=None):
    return store.ingest(reports if reports is not None else [report(at, value)],
        reported_at=at if receipt is None else receipt, firmware_version=firmware)


def test_observed_deltas_start_at_zero_and_survive_gateway_restart(tmp_path):
    path = tmp_path / "energy.json"
    store = NativeEnergyStore("c1000_gen2", path)
    first = ingest(store)
    assert first["meter"]["energy_kwh"] == {"ac_input": 0, "ac_output": 0}
    second = ingest(store, NOW + 600, 125)
    assert second["meter"]["energy_kwh"] == {"ac_input": 0.025, "ac_output": 0.025}
    assert second["meter"]["generation"] == first["meter"]["generation"]
    restarted = NativeEnergyStore("c1000_gen2", path)
    third = ingest(restarted, NOW + 1200, 150, firmware=None)
    assert third["meter"]["energy_kwh"]["ac_output"] == 0.05
    assert third["meter"]["started_at"] == NOW
    assert third["firmware_version"] == "1.1.4.9"
    assert third["meter"]["accepted_reports"] == 3
    assert path.stat().st_mode & 0o777 == 0o600


def test_duplicate_and_older_requests_do_not_refresh_or_double_count(tmp_path):
    store = NativeEnergyStore("c1000_gen2", tmp_path / "energy.json")
    ingest(store)
    latest = ingest(store, NOW+600, 125)
    for at, value, receipt in ((NOW+600, 125, NOW+601), (NOW, 100, NOW+602)):
        result = ingest(store, at, value, receipt=receipt)
        assert result["meter"]["energy_kwh"] == latest["meter"]["energy_kwh"]
        assert result["meter"]["last_receipt_at"] == NOW+600
        assert result["meter"]["last_event_timestamp"] == NOW+600
    final = ingest(store, NOW+1200, 130)
    assert final["meter"]["energy_kwh"]["ac_input"] == 0.03
    assert final["meter"]["rejected_reports"] == 2


@pytest.mark.parametrize("boundary,reason", [
    ("decrease", "counter_decreased"), ("wrap", "counter_decreased"),
    ("batch", "batch_order_unknown"), ("same_clock", "clock_conflict"),
    ("missing_clock", "timestamp_missing"), ("future_clock", "timestamp_outside_window"),
    ("stale_clock", "timestamp_outside_window"), ("missing_channel", "coverage_changed"),
    ("new_firmware", "firmware_changed"), ("long_gap", "report_gap"),
    ("jump", "implausible_delta")])
def test_ambiguous_boundaries_latch_quarantine_across_restart(tmp_path, boundary, reason):
    path = tmp_path / "energy.json"
    store = NativeEnergyStore("c1000_gen2", path)
    initial = 2**32-2 if boundary == "wrap" else 100
    first = ingest(store, value=initial)
    payload, firmware, receipt = [report(NOW+600, initial)], "1.1.4.9", NOW+600
    if boundary in ("decrease", "wrap"):
        payload[0] = report(NOW+600, 5)
    elif boundary == "batch":
        payload.append(report(NOW+600, initial+1))
    elif boundary == "same_clock":
        payload[0] = report(NOW, initial+1)
    elif boundary == "missing_clock":
        payload[0].pop("event_timestamp")
    elif boundary == "future_clock":
        payload[0]["event_timestamp"] = receipt+6
    elif boundary == "stale_clock":
        payload[0]["event_timestamp"] = receipt-MAX_REPORT_AGE
    elif boundary == "missing_channel":
        payload[0]["groups"]["standard"].pop("ac_output_energy_raw")
    elif boundary == "new_firmware":
        firmware = "1.1.4.10"
    elif boundary == "long_gap":
        payload[0]["event_timestamp"] = receipt = NOW+MAX_REPORT_AGE
    elif boundary == "jump":
        payload[0] = report(NOW+600, 10000)
    result = ingest(store, receipt=receipt, firmware=firmware, reports=payload)
    assert result["meter"]["status"] == "quarantined"
    assert result["meter"]["reason"] == reason
    assert result["meter"]["energy_kwh"] == first["meter"]["energy_kwh"]
    assert not result["meter"]["available"]
    restored = NativeEnergyStore("c1000_gen2", path)
    later = ingest(restored, receipt=receipt+600, reports=[report(receipt+600, 200)])
    assert later["meter"]["status"] == "quarantined" and later["meter"]["reason"] == reason
    assert later["meter"]["energy_kwh"] == first["meter"]["energy_kwh"]


@pytest.mark.parametrize("model,firmware", [("c2000_gen2", "2.1.6.4"), ("c1000_gen2", "1.1.4.10"),
                                         ("c1000_gen2", None)])
def test_no_meter_for_unqualified_model_or_version(model, firmware):
    assert "meter" not in ingest(NativeEnergyStore(model), firmware=firmware)


def test_legacy_persistence_is_migrated_without_backfilling_or_new_generation_on_restart(tmp_path):
    path = tmp_path / "energy.json"
    store = NativeEnergyStore("c1000_gen2", path)
    legacy = report()
    legacy.pop("event_timestamp")
    result = ingest(store, reports=[legacy])
    assert "meter" not in result
    restored = NativeEnergyStore("c1000_gen2", path)
    result = ingest(restored, NOW+600, 250)
    assert result["meter"]["energy_kwh"]["ac_input"] == 0
    generation = result["meter"]["generation"]
    assert NativeEnergyStore("c1000_gen2", path).snapshot(now=NOW+600)["meter"]["generation"] == generation


def test_validation_recomputes_meter_units_and_rejects_bad_persistent_accounting(tmp_path):
    source = ingest(NativeEnergyStore("c1000_gen2"))
    forged = deepcopy(source)
    forged["meter"].update(secret="PRIVATE", available=False, energy_kwh={"ac_input": 999}, units_verified=True)
    parsed = validate_native_energy(forged, now=NOW)
    assert parsed["meter"]["energy_kwh"]["ac_input"] == 0 and parsed["meter"]["available"]
    assert "PRIVATE" not in json.dumps(parsed) and not parsed["meter"]["units_verified"]
    source["meter"]["channels"]["ac_input"]["total_raw"] = -1
    path = tmp_path / "bad.json"
    body = json.dumps(source)
    path.write_text(body)
    path.chmod(0o600)
    store = NativeEnergyStore("c1000_gen2", path)
    assert store.persistence_error and path.read_text() == body


def test_failed_atomic_write_does_not_advance_meter(monkeypatch, tmp_path):
    import solix_link.energy_store as module
    store = NativeEnergyStore("c1000_gen2", tmp_path / "energy.json")
    before = ingest(store)
    def fail(*_args):
        raise OSError("synthetic storage failure")
    monkeypatch.setattr(module, "private_write", fail)
    with pytest.raises(OSError):
        ingest(store, NOW+600, 125)
    assert store.snapshot(now=NOW) == before


@pytest.mark.parametrize("timestamp,expected", [(NOW, NOW), (str(NOW), NOW), (NOW+0.5, NOW+0.5),
    (None, None), (True, None), ("private-value", None), (float("nan"), None), (10**400, None),
    (NOW*1000, None)])
def test_radio_request_timestamp_whitelist(timestamp, expected):
    value = request(field(19, field(3, 10)))
    value["utc_ts"] = timestamp
    value["events"][0]["utc_ts"] = NOW+100
    assert decode_energy_events(value)[0]["event_timestamp"] == expected


def test_fresh_receipts_of_duplicate_source_data_do_not_keep_meter_available():
    store = NativeEnergyStore("c1000_gen2")
    ingest(store)
    assert store.snapshot(now=NOW+MAX_REPORT_AGE)["meter"]["available"] is False
    result = ingest(store, NOW, receipt=NOW+MAX_REPORT_AGE-1)
    assert result["meter"]["last_receipt_at"] == NOW
    assert not store.snapshot(now=NOW+MAX_REPORT_AGE)["meter"]["available"]


def test_observed_meters_reach_public_api_metrics_and_terminal_rows_without_device_requests():
    import asyncio
    import time
    from solix_link.energy_values import native_energy_rows
    from solix_link.gateway_client import GatewayClient
    from solix_link.server import create_app
    from test_gateway import Gateway
    from http_helpers import api_client

    now = time.time()
    store = NativeEnergyStore("c1000_gen2")
    ingest(store, now-600)
    energy = ingest(store, now, 125)
    class MeterGateway(Gateway):
        def snapshot(self, name):
            result = super().snapshot(name)
            result.update(model="c1000_gen2", protocol="native_mqtt", native_energy=energy)
            return result
    gateway = MeterGateway()
    async def run():
        async with api_client(create_app(gateway)) as client:
            data = (await client.get("/devices/ups/energy")).json()["native_energy"]
            assert data["meter"]["energy_kwh"]["ac_input"] == 0.025
            public = GatewayClient._station((await client.get("/devices/ups")).json())
            assert public["native_energy"]["meter"] == data["meter"]
            metrics = (await client.get("/metrics")).text
            assert 'solix_native_meter_energy_kwh_estimate{device="ups",group="standard",channel="ac_output",basis="nominal_wh"} 0.025' in metrics
            assert "# TYPE solix_native_meter_energy_kwh_estimate gauge" in metrics
        assert gateway.calls == []
    asyncio.run(run())
    rows = native_energy_rows(energy, now=now)
    assert len(rows) == 4
    assert rows[-1] == ("Observed Standard", "ac output estimate (tracking)", "25", "0.025000")
