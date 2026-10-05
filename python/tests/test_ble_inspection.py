"""BLE inspection uses synthetic advertisements/GATT and no live radio."""

import asyncio
import json
from types import SimpleNamespace

import bleak
import pytest

from solix_link import ble_inspection, cli
from solix_link.protocol import COMMAND_UUID, IDENTIFIER_UUID, Model, SERVICE_UUID, TELEMETRY_UUID


def advertisement(name, address="AA:BB:CC:DD:EE:01", *, uuids=()):
    device = SimpleNamespace(name=name, address=address)
    adv = SimpleNamespace(local_name=name, service_uuids=list(uuids))
    return device, adv


@pytest.fixture
def backend(monkeypatch):
    state = SimpleNamespace(found={}, clients=[], scans=[])

    async def discover(**kwargs):
        state.scans.append(kwargs)
        return state.found

    class Client:
        def __init__(self, device, **kwargs):
            self.device = device
            self.options = kwargs
            self.calls = []
            self.services = [SimpleNamespace(uuid=SERVICE_UUID.upper(), characteristics=[
                SimpleNamespace(uuid=COMMAND_UUID, properties=["write-without-response"]),
                SimpleNamespace(uuid=TELEMETRY_UUID, properties=["notify"]),
                SimpleNamespace(uuid=IDENTIFIER_UUID, properties=["read"]),
                SimpleNamespace(uuid="00002a26-0000-1000-8000-00805f9b34fb", properties=["read"]),
                SimpleNamespace(uuid="private-invalid-uuid", properties=["read"]),
            ])]
            state.clients.append(self)

        async def connect(self):
            self.calls.append("connect")

        async def disconnect(self):
            self.calls.append("disconnect")

        async def pair(self, *_args, **_kwargs):
            pytest.fail("Inspection must never pair")

        async def read_gatt_char(self, *_args, **_kwargs):
            pytest.fail("Inspection must never read characteristic values")

        async def write_gatt_char(self, *_args, **_kwargs):
            pytest.fail("Inspection must never send station commands")

        async def start_notify(self, *_args, **_kwargs):
            pytest.fail("Inspection must never subscribe to telemetry")

    monkeypatch.setattr(bleak.BleakScanner, "discover", discover)
    monkeypatch.setattr(bleak, "BleakClient", Client)
    return state


@pytest.mark.parametrize("model,name", [(Model.C1000, "Anker A1761-demo"),
                                        (Model.C1000_GEN2, "Anker A1763-demo")])
def test_inventory_connects_only_unique_model_and_excludes_identifiers(backend, model, name):
    backend.found = {1: advertisement(name), 2: advertisement("Anker A1783-private", "AA:BB:CC:DD:EE:03")}
    result = asyncio.run(ble_inspection.inspect_ble_features(model, connect=True))
    assert result["result"] == "inspected"
    assert result["matching_advertisements"] == 1
    assert result["characteristic_reads"] == result["characteristic_writes"] == 0
    assert result["solix_handshake_started"] is False
    client, = backend.clients
    assert client.device.name == name
    assert client.options == {"timeout": 15.0, "pair": False}
    assert client.calls == ["connect", "disconnect"]
    characteristics = result["services"][0]["characteristics"]
    assert {item["role"] for item in characteristics} == {
        "solix_command", "solix_telemetry", "station_identifier", "firmware_revision"}
    output = json.dumps(result)
    for private in (name, "AA:BB", "A1783-private", "private-invalid-uuid"):
        assert private not in output


def test_discovery_is_default_and_does_not_connect(backend):
    backend.found = {1: advertisement("SOLIX C1000 Gen 2")}
    result = asyncio.run(ble_inspection.inspect_ble_features(Model.C1000_GEN2))
    assert result["result"] == "discovered"
    assert result["mode"] == "advertisement_discovery"
    assert result["connection_attempted"] is False
    assert backend.clients == []
    assert backend.scans == [{"timeout": 10.0, "return_adv": True}]


def test_missing_and_service_only_advertisements_are_never_guessed(backend):
    backend.found = {1: advertisement(None, uuids=[SERVICE_UUID.upper()]),
                     2: advertisement("Anker A1783-private", "AA:BB:CC:DD:EE:03")}
    result = asyncio.run(ble_inspection.inspect_ble_features(Model.C1000, connect=True))
    assert result["result"] == "not_found"
    assert result["unclassified_solix_advertisements"] == 1
    assert result["matching_advertisements"] == 0
    assert backend.clients == []


def test_ambiguous_target_never_connects(backend):
    backend.found = {1: advertisement("Anker A1761-demo1"),
                     2: advertisement("Anker A1761-demo2", "AA:BB:CC:DD:EE:02")}
    result = asyncio.run(ble_inspection.inspect_ble_features(Model.C1000, connect=True))
    assert result["result"] == "ambiguous"
    assert result["matching_advertisements"] == 2
    assert result["connection_attempted"] is False
    assert backend.clients == []


def test_advertisement_local_name_takes_precedence(backend):
    device, adv = advertisement("Anker A1761-demo")
    device.name = "stale-unknown-name"
    backend.found = {1: (device, adv)}
    assert asyncio.run(ble_inspection.inspect_ble_features(Model.C1000))["result"] == "discovered"


@pytest.mark.parametrize("model", [Model.C300, Model.C2000_GEN2, "unsupported"])
def test_other_models_rejected_before_discovery(backend, model):
    with pytest.raises(ValueError):
        asyncio.run(ble_inspection.inspect_ble_features(model, connect=True))
    assert backend.scans == backend.clients == []


@pytest.mark.parametrize("value", [True, 0, -1, 31, 10 ** 400, float("nan"), float("inf"), "10", None])
@pytest.mark.parametrize("option", ["scan_timeout", "connect_timeout"])
def test_invalid_bounds_rejected_before_discovery(backend, value, option):
    with pytest.raises(ValueError, match="1–30 seconds"):
        asyncio.run(ble_inspection.inspect_ble_features(Model.C1000, **{option: value}))
    assert backend.scans == backend.clients == []


def test_non_boolean_connect_rejected_before_discovery(backend):
    with pytest.raises(ValueError, match="boolean"):
        asyncio.run(ble_inspection.inspect_ble_features(Model.C1000, connect=1))
    assert backend.scans == backend.clients == []


def test_scan_error_is_redacted(backend, monkeypatch):
    async def fail(**_kwargs):
        raise PermissionError("private station AA:BB:CC:DD:EE:01")
    monkeypatch.setattr(bleak.BleakScanner, "discover", fail)
    result = asyncio.run(ble_inspection.inspect_ble_features(Model.C1000))
    assert result["result"] == "scan_failed" and result["error_type"] == "PermissionError"
    assert "private station" not in json.dumps(result)
    assert backend.clients == []


def test_connect_error_disconnects_and_redacts(backend, monkeypatch):
    backend.found = {1: advertisement("Anker A1761-demo")}
    async def fail(self):
        raise RuntimeError("private station AA:BB:CC:DD:EE:01")
    monkeypatch.setattr(bleak.BleakClient, "connect", fail)
    result = asyncio.run(ble_inspection.inspect_ble_features(Model.C1000, connect=True))
    assert result["result"] == "connection_failed" and result["error_type"] == "RuntimeError"
    assert backend.clients[0].calls == ["disconnect"]
    assert "private station" not in json.dumps(result)


def test_disconnect_failure_is_distinct_and_redacted(backend, monkeypatch):
    backend.found = {1: advertisement("Anker A1761-demo")}
    async def fail(self):
        raise RuntimeError("private station AA:BB:CC:DD:EE:01")
    monkeypatch.setattr(bleak.BleakClient, "disconnect", fail)
    result = asyncio.run(ble_inspection.inspect_ble_features(Model.C1000, connect=True))
    assert result["result"] == "disconnect_failed"
    assert result["disconnect_error_type"] == "RuntimeError"
    assert "private station" not in json.dumps(result)


def test_connection_deadline_still_cleans_up(backend, monkeypatch):
    backend.found = {1: advertisement("Anker A1761-demo")}
    async def hang(self):
        self.calls.append("connect")
        await asyncio.Event().wait()
    monkeypatch.setattr(bleak.BleakClient, "connect", hang)
    result = asyncio.run(ble_inspection.inspect_ble_features(Model.C1000, connect=True, connect_timeout=1))
    assert result["result"] == "connection_failed" and result["error_type"] == "TimeoutError"
    assert backend.clients[0].calls == ["connect", "disconnect"]


def test_discovery_deadline_is_bounded(backend, monkeypatch):
    async def hang(**_kwargs):
        await asyncio.Event().wait()
    monkeypatch.setattr(bleak.BleakScanner, "discover", hang)
    result = asyncio.run(ble_inspection.inspect_ble_features(Model.C1000, scan_timeout=1))
    assert result["result"] == "scan_failed" and result["error_type"] == "TimeoutError"
    assert backend.clients == []


def test_disconnect_deadline_is_bounded(backend, monkeypatch):
    backend.found = {1: advertisement("Anker A1761-demo")}
    async def hang(self):
        self.calls.append("disconnect")
        await asyncio.Event().wait()
    monkeypatch.setattr(bleak.BleakClient, "disconnect", hang)
    result = asyncio.run(ble_inspection.inspect_ble_features(Model.C1000, connect=True))
    assert result["result"] == "disconnect_failed"
    assert result["disconnect_error_type"] == "TimeoutError"
    assert backend.clients[0].calls == ["connect", "disconnect"]


def test_service_enumeration_error_still_disconnects(backend, monkeypatch):
    backend.found = {1: advertisement("Anker A1761-demo")}
    async def connect(self):
        self.services = None
    monkeypatch.setattr(bleak.BleakClient, "connect", connect)
    result = asyncio.run(ble_inspection.inspect_ble_features(Model.C1000, connect=True))
    assert result["result"] == "connection_failed" and result["error_type"] == "TypeError"
    assert backend.clients[0].calls == ["disconnect"]


def test_cancelled_connection_propagates_after_cleanup(backend, monkeypatch):
    backend.found = {1: advertisement("Anker A1761-demo")}
    async def cancel(self):
        raise asyncio.CancelledError()
    monkeypatch.setattr(bleak.BleakClient, "connect", cancel)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ble_inspection.inspect_ble_features(Model.C1000, connect=True))
    assert backend.clients[0].calls == ["disconnect"]


@pytest.mark.parametrize("connect", [False, True])
def test_cli_avoids_config_and_monitor_handshake(backend, monkeypatch, capsys, connect):
    backend.found = {1: advertisement("Anker A1761-demo")}
    def forbidden(*_args, **_kwargs):
        pytest.fail("Inspection must not read station profiles or construct a monitor")
    monkeypatch.setattr(cli, "load_config", forbidden)
    monkeypatch.setattr(cli, "SolixMonitor", forbidden)
    argv = ["ble-inspect", "--model", "c1000"] + (["--connect"] if connect else [])
    assert cli.main(argv) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["result"] == ("inspected" if connect else "discovered")


def test_cli_missing_station_returns_json_and_nonzero(backend, capsys):
    assert cli.main(["ble-inspect", "--model", "c1000_gen2"]) == 1
    assert json.loads(capsys.readouterr().out)["result"] == "not_found"


def test_cli_c2000_is_excluded(backend, capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(["ble-inspect", "--model", "c2000_gen2"])
    assert error.value.code == 2
    assert backend.scans == backend.clients == []
