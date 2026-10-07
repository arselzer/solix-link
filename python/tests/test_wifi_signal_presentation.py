"""Independent radio freshness and explicit optional polling, without devices."""
import asyncio
import json
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from solix_link.ap_service_monitor import APServiceMonitor
from solix_link.cli import parser
from solix_link.gateway_client import GatewayClient
from solix_link.protocol import Model
from solix_link.tui import public_snapshot
from solix_link.wifi_signal import validate_wifi_signal
from test_ap_service_fleet import fleet
from test_native_wifi_rssi import setup

NOW = 1700000000


def report(**changes):
    return dict(schema_version=1, source="radio_ap_info", main_version="1.1.4.9", radio_version="0.3.3.0",
                settings_unchanged=True, observed_at=NOW-1, wifi_rssi_dbm=-42, private="discard") | changes


@pytest.mark.parametrize("changes,valid,available", [({}, True, True),
    ({"observed_at": NOW-600}, True, False), ({"observed_at": NOW+6}, True, False),
    ({"observed_at": float("nan")}, False, False), ({"observed_at": True}, False, False),
    ({"wifi_rssi_dbm": None}, True, False), ({"wifi_rssi_dbm": 0}, False, False),
    ({"wifi_rssi_dbm": -129}, False, False), ({"wifi_rssi_dbm": False}, False, False),
    ({"wifi_rssi_dbm": -42.0}, False, False), ({"settings_unchanged": 1}, False, False),
    ({"schema_version": True}, False, False), ({"radio_version": "unknown"}, False, False),
    ({"main_version": "1.1.4.10"}, False, False)])
def test_radio_report_validation(changes, valid, available):
    value = validate_wifi_signal(report(**changes), model="c1000_gen2", protocol="native_mqtt", now=NOW)
    assert (value is not None) is valid
    if value is not None:
        assert value["available"] is available
        assert value["wifi_rssi_dbm"] == (-42 if available else None)
        assert "private" not in value


@pytest.mark.parametrize("model,protocol", [("c1000", "native_mqtt"), ("c2000_gen2", "native_mqtt"),
                                           ("c300", "legacy"), ("c1000_gen2", "prime")])
def test_other_model_and_transport_never_accept_signal(model, protocol):
    assert validate_wifi_signal(report(), model=model, protocol=protocol, now=NOW) is None


def test_radio_cache_is_separate_from_fresh_controller_confirmation(tmp_path):
    server, station = setup(tmp_path)
    server.last_seen = 123
    result = asyncio.run(server.wifi_rssi())
    snapshot = server.snapshot()
    assert snapshot["wifi_signal"]["wifi_rssi_dbm"] == -70
    assert snapshot["wifi_signal"]["observed_at"] == result["observed_at"]
    # The surrounding two controller reads legitimately refresh telemetry.
    # Publishing/reading the separately timestamped radio cache does not.
    confirmed_at = server.last_seen
    assert [command for command, _ in station.requests] == ["0100", "0022", "0100"]
    server.snapshot()
    assert server.last_seen == confirmed_at
    station.rssi_reply = b"invalid"
    with pytest.raises(ValueError): asyncio.run(server.wifi_rssi())
    assert server.snapshot()["wifi_signal"] is None


@pytest.mark.parametrize("model,versions,available,expected", [
    (Model.C1000_GEN2, True, True, 1), (Model.C1000_GEN2, False, True, 0),
    (Model.C1000_GEN2, True, False, 0), (Model.C2000_GEN2, True, True, 0), (Model.C1000, True, True, 0)])
def test_monitor_only_queries_validated_firmware(monkeypatch, model, versions, available, expected):
    monitor = object.__new__(APServiceMonitor)
    monitor.directory = Path("synthetic")
    monitor.devices = {"test": SimpleNamespace(model=model)}
    monitor.snapshot = lambda _name: {"available": available, "metrics": {
        "software_version": "1.1.4.9" if versions else "new", "software_version_module": "0.3.3.0"}}
    calls = []
    async def query(*args, **kwargs): calls.append((args, kwargs)); return {}
    monkeypatch.setattr("solix_link.ap_service_monitor.ap_service_request", query)
    asyncio.run(monitor._query_wifi_signal("test"))
    assert len(calls) == expected
    if calls: assert calls[0][0][1] == "wifi-rssi" and calls[0][1] == {"name": "test"}


def test_monitor_polling_is_opt_in_and_cancellation_is_clean(fleet, monkeypatch):
    primary, _, directory = fleet
    async def run(enabled):
        monitor = APServiceMonitor(primary, directory, wifi_rssi=enabled)
        calls = []
        async def query(name): calls.append(name)
        monkeypatch.setattr(monitor, "_query_wifi_signal", query)
        await monitor.start()
        await asyncio.sleep(0)
        await monitor.stop()
        assert bool(calls) is enabled
        if enabled: assert len(calls) == len(monitor.devices)
        assert monitor._task.done()
        if monitor._radio_task: assert monitor._radio_task.done()
    asyncio.run(run(False)); asyncio.run(run(True))
    args = parser().parse_args(["ap-service-serve", "--directory", "synthetic"])
    assert args.wifi_rssi is False
    assert parser().parse_args(["ap-service-serve", "--directory", "synthetic", "--wifi-rssi"]).wifi_rssi


def test_ha_portable_validation_sources_match():
    root = Path(__file__).resolve().parents[2]
    for package, component in (("wifi_signal.py", "wifi_signal.py"), ("telemetry_values.py", "telemetry.py")):
        assert (root / "python/solix_link" / package).read_bytes() == (root / "custom_components/solix_link" / component).read_bytes()


def test_cached_sdk_and_terminal_present_signal_with_own_age():
    value = {"name": "synthetic", "model": "c1000_gen2", "protocol": "native_mqtt", "connected": True,
             "available": True, "last_seen_timestamp": time.time(), "metrics": {},
             "wifi_signal": report(observed_at=time.time())}
    sdk = GatewayClient._station(value)
    assert public_snapshot(sdk)["metrics"]["wifi_rssi_dbm"] == -42
    value["wifi_signal"]["observed_at"] = time.time()-601
    assert "wifi_rssi_dbm" not in public_snapshot(GatewayClient._station(value))["metrics"]


def test_http_and_metrics_are_cached_and_signal_is_sanitized():
    from http_helpers import api_client
    from solix_link.server import create_app
    from test_server_readonly_features import ReadOnlyGateway
    gateway = ReadOnlyGateway()
    gateway.current['wifi_signal'] = report(observed_at=time.time())
    gateway.current['model'] = 'c1000_gen2'
    gateway.current['protocol'] = 'native_mqtt'
    async def forbidden(*_args, **_kwargs): raise AssertionError('GET must not query a station')
    gateway.command = forbidden
    async def run():
        async with api_client(create_app(gateway, token='synthetic')) as client:
            headers = {'Authorization': 'Bearer synthetic'}
            assert (await client.get('/devices')).status_code == 401
            data = (await client.get('/devices/ups', headers=headers)).json()
            assert data['wifi_signal']['wifi_rssi_dbm'] == -42
            assert 'private' not in data['wifi_signal']
            metrics = (await client.get('/metrics', headers=headers)).text
            assert 'solix_wifi_rssi_dbm{device="ups"} -42' in metrics
            gateway.current['wifi_signal']['observed_at'] = time.time()-601
            data = (await client.get('/devices/ups', headers=headers)).json()
            assert not data['wifi_signal']['available']
            metrics = (await client.get('/metrics', headers=headers)).text
            assert 'solix_wifi_rssi_dbm{' not in metrics
            gateway.current['model'] = 'c2000_gen2'
            assert (await client.get('/devices/ups', headers=headers)).json()['wifi_signal'] is None
    asyncio.run(run())
