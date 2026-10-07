"""Synthetic passive energy ingestion, public boundaries and private persistence."""

import asyncio
from copy import deepcopy
import json
import time

import pytest

from http_helpers import api_client
from solix_link.ap_service import APService
from solix_link.ap_service_config import APServiceConfig
from solix_link.energy_report import REPORT_NAME
from solix_link.energy_store import NativeEnergyStore
from solix_link.energy_values import MAX_REPORT_AGE, native_energy_rows, validate_native_energy
from solix_link.gateway_client import GatewayClient
from solix_link.mqtt_intercept import LocalMqttServer
from solix_link.protocol import Model
from solix_link.server import create_app
from solix_link.tui import public_snapshot
from test_ap_service_fleet import fleet
from test_energy_report import field, request
from test_gateway import Gateway

NOW = 1700000000


def report(value=1250, **groups):
    return {"protobuf_name": REPORT_NAME, "units_verified": False,
            "groups": groups or {"standard": {"ac_input_energy_raw": value, "ac_output_energy_raw": value - 50,
                                            "ac_output_duration_raw": 12}}}


def energy(now=NOW, model="c1000_gen2", value=1250):
    return NativeEnergyStore(model).ingest([report(value)], reported_at=now, firmware_version="1.1.4.9")


def test_persistence_is_private_atomic_and_survives_restart(tmp_path):
    path = tmp_path / "private" / "energy-state.json"
    store = NativeEnergyStore("c1000_gen2", path)
    first = store.ingest([report()], reported_at=NOW, firmware_version="1.1.4.9")
    assert first["groups"]["standard"]["energy_kwh"] == {"ac_input": 1.25, "ac_output": 1.2}
    assert first["conversion_basis"] == "nominal_wh" and first["units_verified"] is False
    assert not path.stat().st_mode & 0o077 and not path.parent.stat().st_mode & 0o077
    restarted = NativeEnergyStore("c1000_gen2", path)
    assert restarted.snapshot(now=NOW) == first
    assert restarted.snapshot(now=NOW + MAX_REPORT_AGE)["available"] is False
    assert restarted.snapshot(now=NOW-6)["available"] is False
    assert first["groups"]["standard"]["raw"]["ac_output_duration_raw"] == 12
    assert "dc_input" not in first["groups"]["standard"]["energy_kwh"]


def test_counter_decrease_and_batch_do_not_infer_wraps_resets_or_integrate():
    store = NativeEnergyStore("c2000_gen2")
    store.ingest([report()], reported_at=NOW)
    second = store.ingest([report(70)], reported_at=NOW + 600)
    assert second["counter_epoch"] == 2 and second["continuity"] == "counter_decreased"
    assert second["conversion_basis"] == "assumed_wh"
    batch = store.ingest([report(150), report(100)], reported_at=NOW+1200)
    assert batch["counter_epoch"] == 3 and batch["continuity"] == "batch_order_unknown"
    assert batch["groups"]["standard"]["energy_kwh"]["ac_input"] == 0.1
    assert batch["received_reports"] == 4 and batch["batch_reports"] == 2
    assert "lifetime" not in json.dumps(batch)


def test_validation_strips_nested_private_values_and_recomputes_conversions():
    raw = energy()
    raw.update(serial_number="PRIVATE", available=True, units="kWh", conversion_basis="verified")
    raw["groups"]["standard"].update(password="PRIVATE", energy_kwh={"ac_input": 999})
    raw["groups"]["standard"]["raw"]["account_id"] = "PRIVATE"
    before = deepcopy(raw)
    valid = validate_native_energy(raw, model="c1000_gen2", now=NOW+MAX_REPORT_AGE)
    assert "PRIVATE" not in json.dumps(valid)
    assert valid["groups"]["standard"]["energy_kwh"]["ac_input"] == 1.25
    assert valid["available"] is False and raw == before
    assert len(native_energy_rows(valid, now=NOW)) == 3


def test_empty_protobuf_group_and_explicit_zero_are_not_conflated():
    store = NativeEnergyStore("c1000_gen2")
    value = report(standard={"ac_input_energy_raw": 0}, time_of_use={})
    result = store.ingest([value], reported_at=NOW)
    assert result["groups"]["standard"]["energy_kwh"]["ac_input"] == 0
    assert result["groups"]["time_of_use"] == {"raw": {}, "energy_kwh": {}}
    with pytest.raises(ValueError):
        store.ingest([report(time_of_use={})], reported_at=NOW+1)


@pytest.mark.parametrize("field,value", [("units_verified", True), ("reported_at", True), ("reported_at", float("nan")),
    ("reported_at", 10**400), ("model", "c1000"), ("model", []), ("counter_epoch", True), ("batch_reports", 33),
    ("schema_version", True), ("counter_epoch_started_at", NOW+1), ("continuity", "verified"), ("firmware_version", "PRIVATE")])
def test_invalid_envelope_rejected(field, value):
    raw = energy()
    raw[field] = value
    assert validate_native_energy(raw, now=NOW) is None


@pytest.mark.parametrize("value", [True, -1, 1.5, "1", 2**53, float("inf"), None])
def test_invalid_counter_rejected_without_mutation(value):
    store = NativeEnergyStore("c1000_gen2")
    first = store.ingest([report()], reported_at=NOW)
    bad = report()
    bad["groups"]["standard"]["ac_input_energy_raw"] = value
    with pytest.raises(ValueError):
        store.ingest([bad], reported_at=NOW+1)
    assert store.snapshot(now=NOW) == first


def test_missing_group_firmware_scope_and_timestamp_regression():
    store = NativeEnergyStore("c1000_gen2")
    first = store.ingest([report()], reported_at=NOW, firmware_version="1.1.4.10")
    assert first["conversion_basis"] == "assumed_wh"
    for bad in ([report(groups={})], []):
        with pytest.raises(ValueError):
            store.ingest(bad, reported_at=NOW+1)
    with pytest.raises(ValueError, match="regressed"):
        store.ingest([report()], reported_at=NOW-1)
    assert validate_native_energy(first, model="c2000_gen2") is None
    with pytest.raises(ValueError):
        NativeEnergyStore("c1000").ingest([report()], reported_at=NOW)


@pytest.mark.parametrize("receipt", [True, "now", -1, float("nan"), float("inf"), 10**400])
def test_invalid_receipt_does_not_mutate_saved_state(receipt):
    store = NativeEnergyStore("c1000_gen2")
    before = store.ingest([report()], reported_at=NOW)
    with pytest.raises(ValueError):
        store.ingest([report()], reported_at=receipt)
    assert store.snapshot(now=NOW) == before


@pytest.mark.parametrize("kind", ["corrupt", "symlink", "public", "wrong_model"])
def test_invalid_persistent_state_is_preserved(tmp_path, kind):
    path = tmp_path / "state.json"
    body = "bad" if kind == "corrupt" else json.dumps(energy(model="c2000_gen2" if kind == "wrong_model" else "c1000_gen2"))
    path.write_text(body)
    path.chmod(0o644 if kind == "public" else 0o600)
    if kind == "symlink":
        destination = tmp_path / "destination"
        path.rename(destination)
        path.symlink_to(destination)
    store = NativeEnergyStore("c1000_gen2", path)
    assert store.persistence_error and store.snapshot(now=NOW) is None
    with pytest.raises(ValueError, match="persistence"):
        store.ingest([report()], reported_at=NOW)
    assert path.read_text() == body


def test_energy_ingestion_does_not_refresh_live_telemetry(tmp_path):
    config = APServiceConfig("demo", "wlan_unused", "phy9", "AT", "A1763SYNTHETIC001", "a" * 40, model=Model.C1000_GEN2)
    callbacks = []
    station = LocalMqttServer(config, tmp_path / "private", callback=callbacks.append)
    station.last_seen = NOW
    station.ingest_energy([report()])
    assert station.last_seen == NOW and not station.snapshot()["available"]
    assert callbacks[-1]["native_energy"]["available"]
    assert station.snapshot()["native_energy"]["conversion_basis"] == "assumed_wh"


def test_http_upload_routes_energy_to_the_selected_station(fleet):
    primary, second, directory = fleet
    service = APService(primary, directory, energy_reports=True)
    async def run():
        listener = await asyncio.start_server(service._api, "127.0.0.1", 0)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", listener.sockets[0].getsockname()[1])
            body = request(field(19, field(3, 1250)))
            body["device_sn"] = second.device_serial
            encoded = json.dumps(body).encode()
            writer.write(f"POST /equipment/logging/upload_pb_events HTTP/1.1\r\nDevice-Sn: {second.device_serial}\r\nContent-Length: {len(encoded)}\r\n\r\n".encode() + encoded)
            await writer.drain()
            response = await reader.read()
            writer.close()
            await writer.wait_closed()
            assert b"200 OK" in response
            assert service.stations[primary.name].energy.snapshot() is None
            assert service.stations[second.name].energy.snapshot()["groups"]["standard"]["energy_kwh"]["ac_input"] == 1.25
        finally:
            listener.close()
            await listener.wait_closed()
    asyncio.run(run())


def test_gateway_api_metrics_and_public_clients_preserve_energy_without_requests():
    class EnergyGateway(Gateway):
        def snapshot(self, name):
            result = super().snapshot(name)
            result["native_energy"] = energy(time.time(), model="c2000_gen2")
            result["native_energy"]["groups"]["standard"]["secret"] = "PRIVATE"
            return result
    gateway = EnergyGateway()
    async def run():
        async with api_client(create_app(gateway, token="token")) as client:
            assert (await client.get("/devices/ups/energy")).status_code == 401
            headers = {"Authorization": "Bearer token"}
            result = (await client.get("/devices/ups", headers=headers)).json()
            assert "PRIVATE" not in json.dumps(result)
            assert GatewayClient._station(result)["native_energy"]["groups"]["standard"]["energy_kwh"]["ac_input"] == 1.25
            assert public_snapshot(result)["native_energy"]["model"] == "c2000_gen2"
            response = await client.get("/devices/ups/energy", headers=headers)
            assert response.json()["native_energy"]["units_verified"] is False
            assert (await client.get("/devices/missing/energy", headers=headers)).status_code == 404
            metrics = (await client.get("/metrics", headers=headers)).text
            assert 'solix_native_energy_kwh_unverified{device="ups",group="standard",channel="ac_input",basis="assumed_wh"} 1.25' in metrics
            assert "PRIVATE" not in metrics
            assert "# TYPE solix_native_energy_kwh_unverified gauge" in metrics
            diagnostics = (await client.get("/diagnostics", headers=headers)).json()
            assert "PRIVATE" not in json.dumps(diagnostics)
            assert diagnostics["stations"][0]["native_energy"]["groups"]["standard"]["energy_kwh"]["ac_input"] == 1.25
        assert gateway.calls == []
    asyncio.run(run())


def test_gateway_energy_cli_is_cached_json_only(monkeypatch, capsys):
    from solix_link import cli
    calls = []
    def cached(self, name):
        calls.append(name)
        return energy()
    monkeypatch.setattr(GatewayClient, "energy", cached)
    assert cli.main(["gateway-energy", "--gateway-url", "http://127.0.0.1:8765", "--name", "demo"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["name"] == "demo"
    assert document["native_energy"]["groups"]["standard"]["energy_kwh"]["ac_input"] == 1.25
    assert calls == ["demo"]


def test_energy_read_route_obeys_station_scope(tmp_path):
    from test_access_activity_server import Stations, policy
    access, _, tokens = policy(tmp_path)
    gateway = Stations()
    async def run():
        async with api_client(create_app(gateway, permissions=access)) as client:
            headers = {"Authorization": "Bearer " + tokens[2]}
            assert (await client.get("/devices/protected/energy", headers=headers)).status_code == 404
            allowed = await client.get("/devices/test/energy", headers=headers)
            assert allowed.status_code == 200 and allowed.json()["native_energy"] is None
            assert allowed.headers["cache-control"] == "no-store"
        assert gateway.calls == []
    asyncio.run(run())
