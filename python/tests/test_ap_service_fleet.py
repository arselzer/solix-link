"""Several synthetic stations share one AP without sharing command state."""
import asyncio
import base64
from dataclasses import replace
import json
import ssl
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes
import pytest

from solix_link import add_ap_service_device, initialize_ap_service, load_ap_service_profiles
from solix_link.ap_service import APService, ap_service_request
from solix_link.ap_service_config import APServiceConfig, private_write
from solix_link.ap_service_monitor import APServiceMonitor
from solix_link.ap_service_fleet import MqttDeviceRouter
from solix_link.mqtt_intercept import _Connection, mqtt_packet, mqtt_string, read_mqtt
from solix_link.protocol import DATA_RESPONSE, Model, build_packet, parse_packet, parse_tlvs, tlv
from solix_link.tui import TuiBackend


@pytest.fixture
def fleet(tmp_path):
    primary = APServiceConfig("servers", "wlan_unused", "phy9", "AT", "A1783SYNTHETIC001", "a" * 40)
    directory = tmp_path / "private"
    initialize_ap_service(directory, primary)
    second = replace(primary, name="office", model=Model.C1000_GEN2, device_serial="A1763SYNTHETIC002", account_id="b" * 40)
    add_ap_service_device(directory, second)
    return primary, second, directory


def test_shared_network_unique_certificates_and_no_secret_repr(fleet):
    primary, second, directory = fleet
    profiles = load_ap_service_profiles(directory)
    assert set(profiles) == {"servers", "office"}
    child = profiles[second.name][1]
    assert (directory / "ca.pem").read_bytes() == (child / "ca.pem").read_bytes()
    assert (directory / "server.pem").read_bytes() == (child / "server.pem").read_bytes()
    ca = x509.load_pem_x509_certificate((directory / "ca.pem").read_bytes())
    second_cert = x509.load_pem_x509_certificate((child / "client.pem").read_bytes())
    assert second_cert.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier).value.key_identifier == ca.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value.digest
    certificates = [x509.load_pem_x509_certificate((path / "client.pem").read_bytes()).fingerprint(hashes.SHA256()) for _, path in profiles.values()]
    assert certificates[0] != certificates[1]
    assert not (child / "ca-key.pem").exists()
    assert all(path.stat().st_mode & 0o077 == 0 for path in child.iterdir())
    for config in (primary, second):
        assert config.account_id not in repr(config) and config.device_serial not in repr(config)


def test_add_refuses_duplicates_active_worker_and_different_network(fleet):
    primary, second, directory = fleet
    for config in (second, replace(second, name="duplicate"), replace(second, name="new", device_serial="A1763SYNTHETIC003", ssid="wrong-ap")):
        with pytest.raises((ValueError, FileExistsError)):
            add_ap_service_device(directory, config)
    new = replace(primary, name="third", device_serial="A1783SYNTHETIC003")
    private_write(directory / "ready", "ready")
    with pytest.raises(RuntimeError, match="Stop"):
        add_ap_service_device(directory, new)
    assert not (directory / "devices" / "third").exists()


def test_profile_loader_rejects_parent_directory_symlink_and_identity_conflicts(fleet, tmp_path):
    _, second, directory = fleet
    child = directory / "devices" / second.name
    child.rename(tmp_path / "external")
    child.symlink_to(tmp_path / "external", target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        load_ap_service_profiles(directory)


def test_tui_routes_selection_and_controls_to_named_station(fleet):
    _, _, directory = fleet
    async def run():
        calls = []
        async def request(path, command, **fields):
            calls.append((path, command, fields))
            return {"connected": True, "available": True, "control_enabled": True, "metrics": {}}
        backend = TuiBackend([], directory, requester=request)
        assert {target.key for target in backend.targets} == {"native:office", "native:servers"}
        await backend.connect("native:office")
        await backend.control("temperature-unit", "fahrenheit")
        await backend.connect("native:servers")
        await backend.control("charge-power", "1700")
        assert [fields["name"] for _, _, fields in calls] == ["office", "office", "servers", "servers"]
        assert all("account_id" not in fields for _, _, fields in calls)
        await backend.disconnect()
        assert len(calls) == 4
    asyncio.run(run())


def test_gateway_reports_each_stations_freshness_and_capabilities(fleet):
    primary, second, directory = fleet
    monitor = APServiceMonitor(primary, directory)
    for name, (config, path) in load_ap_service_profiles(directory).items():
        private_write(path / "status.json", json.dumps({"name": name, "connected": True, "last_seen_timestamp": time.time() - (40 if name == "servers" else 0), "metrics": {}, "control_enabled": True}))
    snapshots = {status["name"]: status for status in monitor.snapshots()}
    assert snapshots["office"]["available"] and not snapshots["servers"]["available"]
    assert "set-temperature-unit" in monitor.supported_commands("office")
    assert "set-temperature-unit" not in monitor.supported_commands("servers")
    with pytest.raises(KeyError): monitor.snapshot("unknown")


def test_unix_controls_require_name_and_touch_only_selected_station(fleet):
    primary, second, directory = fleet
    async def run():
        service = APService(primary, directory, allow_control=True)
        calls = []
        for name, station in service.stations.items():
            async def power(watts, name=name):
                calls.append((name, watts))
                return {"name": name, "metrics": {"ac_output_enabled": 1}}
            station.set_ac_charging_power = power
        listener = await asyncio.start_unix_server(service._control, path=directory / "control.sock")
        try:
            assert len((await ap_service_request(directory, "status"))["devices"]) == 2
            with pytest.raises(ValueError): await ap_service_request(directory, "set-charge-power", watts=1000)
            with pytest.raises(ValueError): await ap_service_request(directory, "set-charge-power", name="unknown", watts=1000)
            await ap_service_request(directory, "set-charge-power", name="office", watts=1000)
            assert calls == [("office", 1000)]
        finally:
            listener.close(); await listener.wait_closed()
    asyncio.run(run())


def test_private_countdown_requires_gen2_c1000_target_and_exact_shape(fleet):
    primary, second, directory = fleet
    async def run():
        service = APService(primary, directory, allow_control=True)
        calls = []
        for name, station in service.stations.items():
            async def countdown(seconds, name=name):
                calls.append((name, seconds))
                return {"settings_confirmed": True}
            station.set_ac_countdown = countdown
        listener = await asyncio.start_unix_server(service._control, path=directory / "control.sock")
        try:
            for values in ({"seconds": 600}, {"name": "servers", "seconds": 600},
                           {"name": "office", "seconds": 600, "enabled": False},
                           {"name": "office"}):
                with pytest.raises(ValueError):
                    await ap_service_request(directory, "set-ac-countdown", **values)
            assert calls == []
            await ap_service_request(directory, "set-ac-countdown", name="office", seconds=600)
            await ap_service_request(directory, "set-ac-countdown", name="office", seconds=0)
            assert calls == [("office", 600), ("office", 0)]
            assert "set-ac-countdown" not in APServiceMonitor(primary, directory).supported_commands("office")
        finally:
            listener.close()
            await listener.wait_closed()
    asyncio.run(run())


@pytest.mark.parametrize("command,method,result_key", [
    ("wireless-state", "wireless_state", "wireless_state"),
    ("wifi-rssi", "wifi_rssi", "wifi_rssi_dbm"),
])
def test_radio_query_requires_selected_gen2_model_and_accepts_no_write_fields(fleet, command, method, result_key):
    primary, second, directory = fleet
    async def run():
        service = APService(primary, directory, allow_control=False)
        calls = []
        async def query():
            calls.append(second.name)
            return {result_key: 1}
        setattr(service.stations[second.name], method, query)
        listener = await asyncio.start_unix_server(service._control, path=directory / "control.sock")
        try:
            for values in ({}, {"name": "servers"}, {"name": "unknown"},
                           {"name": "office", "enabled": True}, {"name": "office", "seconds": 0}):
                with pytest.raises(ValueError):
                    await ap_service_request(directory, command, **values)
            assert calls == []
            result = await ap_service_request(directory, command, name="office")
            assert result[result_key] == 1 and calls == ["office"]
            assert command not in APServiceMonitor(primary, directory).supported_commands("office")
        finally:
            listener.close(); await listener.wait_closed()
    asyncio.run(run())


def test_device_api_routes_header_only_requests_and_credentials(fleet):
    primary, second, directory = fleet
    async def run():
        service = APService(primary, directory)
        listener = await asyncio.start_server(service._api, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        async def request(path, serial=None, body=None):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            content = json.dumps(body or {}).encode()
            header = f"POST {path} HTTP/1.1\r\nHost: synthetic\r\nContent-Length: {len(content)}\r\n"
            if serial: header += f"device-sn: {serial}\r\n"
            writer.write((header + "\r\n").encode() + content)
            await writer.drain()
            result = await reader.read()
            writer.close(); await writer.wait_closed()
            return result
        try:
            for config in (primary, second):
                reply = await request("//equipment/devicemanage/get_mqtt_info", config.device_serial)
                assert b"200 OK" in reply
                assert (b"Transfer-Encoding: chunked" in reply) == (config.model == Model.C2000_GEN2)
                assert config.device_serial.encode() in reply if config.model == Model.C1000_GEN2 else True
            assert b"200 OK" in await request("//equipment/agreement/get_device_point_switch", second.device_serial, {"account": second.account_id})
            assert b"400 Bad Request" in await request("/equipment/help/dst")
            assert b"400 Bad Request" in await request("/equipment/help/dst", primary.device_serial, {"device_sn": second.device_serial})
        finally:
            listener.close(); await listener.wait_closed()
    asyncio.run(run())


def test_shared_tls_mqtt_routes_two_models_and_isolates_topics_and_acknowledgements(fleet, monkeypatch):
    primary, second, directory = fleet
    async def run():
        service = APService(primary, directory, allow_control=True)
        router = service._router
        gate = asyncio.Event()
        original_poll = _Connection.poll
        async def poll(connection):
            await gate.wait()
            connection._command_ready_at = 0  # Synthetic test avoids the real C1000 startup grace.
            await original_poll(connection)
        monkeypatch.setattr(_Connection, "poll", poll)
        await router.start("127.0.0.1", 0)
        port = router._server.sockets[0].getsockname()[1]
        responders = []
        writers = []
        captured = {primary.name: [], second.name: []}
        async def connect(config, path):
            context = ssl.create_default_context(cafile=str(path / "ca.pem"))
            context.load_cert_chain(path / "client.pem", path / "client-key.pem")
            reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=context, server_hostname=config.broker_host)
            writers.append(writer)
            writer.write(mqtt_packet(0x10, b"\x00\x04MQTT\x04\x02\x00\x3c\x00\x04same"))
            await writer.drain()
            assert await read_mqtt(reader) == (0x20, b"\x00\x00")
            other = second if config.name == primary.name else primary
            topic = f"cmd/anker_power/{other.product}/{other.device_serial}/req".encode()
            writer.write(mqtt_packet(0x82, b"\x00\x01" + len(topic).to_bytes(2, "big") + topic + b"\x01"))
            await writer.drain()
            assert await read_mqtt(reader) == (0x90, b"\x00\x01\x80")
            topic = service.stations[config.name].topic.encode()
            writer.write(mqtt_packet(0x82, b"\x00\x02" + len(topic).to_bytes(2, "big") + topic + b"\x01"))
            await writer.drain()
            assert await read_mqtt(reader) == (0x90, b"\x00\x02\x01")
            async def respond():
                watts = 1200 if config.model == Model.C1000_GEN2 else 1800
                while True:
                    first, body = await read_mqtt(reader)
                    assert first == 0x30
                    request_topic, pos = mqtt_string(body, 0)
                    assert request_topic == service.stations[config.name].topic
                    envelope = json.loads(json.loads(body[pos:])["payload"])
                    assert envelope["device_sn"] == config.device_serial
                    frame = parse_packet(base64.b64decode(envelope["data"]))
                    captured[config.name].append(frame.command.hex())
                    if frame.command.hex() == "0101": watts = int.from_bytes(parse_tlvs(frame.payload)[0xA4][1:], "little")
                    a4 = bytearray(34); a4[0] = 4; a4[5:7] = watts.to_bytes(2, "little")
                    data = (tlv(0xA4, a4) + tlv(0xA7, b"\x04\x01\x2d\x00\x01\x2d\x00")
                            + tlv(0xB2, b"\x04\x00\x00\x00") + tlv(0xD9, bytes([4, 0, 0, 10, 100, 1, 0]) + bytes(19))) if frame.command.hex() == "0100" else b""
                    packet = build_packet(DATA_RESPONSE, (int.from_bytes(frame.command, "big") | 0x800).to_bytes(2, "big"), b"\x00" + data)
                    message = json.dumps({"payload": json.dumps({"pn": config.product, "sn": config.device_serial, "data": base64.b64encode(packet).decode()})}).encode()
                    response_topic = f"dt/anker_power/{config.product}/{config.device_serial}/param_info".encode()
                    writer.write(mqtt_packet(0x30, len(response_topic).to_bytes(2, "big") + response_topic + message))
                    await writer.drain()
            responders.append(asyncio.create_task(respond()))
        try:
            for config, path in load_ap_service_profiles(directory).values(): await connect(config, path)
            gate.set()
            async with asyncio.timeout(3):
                while not all(station.snapshot()["available"] for station in service.stations.values()): await asyncio.sleep(.01)
            await asyncio.gather(service.stations["office"].set_ac_charging_power(1100), service.stations["servers"].set_ac_charging_power(1700))
            assert service.stations["office"].metrics["ac_charging_power_limit_w"] == 1100
            assert service.stations["servers"].metrics["ac_charging_power_limit_w"] == 1700
            assert sum(action == "0101" for action in captured["office"]) == 1
            assert sum(action == "0101" for action in captured["servers"]) == 1
            assert all(station.metrics["ac_output_enabled"] == 1 for station in service.stations.values())
        finally:
            await router.stop()
            for station in service.stations.values(): await station.stop()
            for task in responders: task.cancel()
            await asyncio.gather(*responders, return_exceptions=True)
            for writer in writers: writer.close()
    asyncio.run(run())


def test_tui_adds_paired_station_to_stopped_ap_without_device_writes(tmp_path):
    from solix_link.config import DeviceConfig
    from types import SimpleNamespace
    primary = APServiceConfig("servers", "wlan_unused", "phy9", "AT", "A1783SYNTHETIC001", "a" * 40)
    directory = tmp_path / "private"
    initialize_ap_service(directory, primary)
    device = DeviceConfig("office", "AA:BB:CC:DD:EE:02", Model.C1000_GEN2, "b" * 40)
    backend = TuiBackend([device], directory)
    backend.target = backend.targets[0]
    backend.monitor = SimpleNamespace(connected=True, metrics={"serial_number": "A1763SYNTHETIC002", "ac_output_enabled": 1})
    backend.last_seen = time.time()
    asyncio.run(backend.register_native())
    assert set(load_ap_service_profiles(directory)) == {"servers", "office"}
    assert {target.key for target in backend.targets if target.native} == {"native:servers", "native:office"}
    assert backend.monitor.metrics["ac_output_enabled"] == 1


def test_multiple_stations_are_visible_and_controlled_through_http(fleet, monkeypatch):
    from http_helpers import api_client
    from solix_link.server import create_app
    from solix_link import ap_service_monitor
    primary, second, directory = fleet
    monitor = APServiceMonitor(primary, directory)
    for name, (config, path) in load_ap_service_profiles(directory).items():
        private_write(path / "status.json", json.dumps({"name":name,"model":config.model.value,"protocol":"native_mqtt",
                      "connected":True,"available":True,"last_seen_timestamp":time.time(),"metrics":{"ac_output_enabled":1},"control_enabled":True}))
    calls=[]
    async def request(path, command, **fields):
        calls.append((path,command,fields))
        return monitor.snapshot(fields["name"])
    monkeypatch.setattr(ap_service_monitor,"ap_service_request",request)
    async def run():
        async with api_client(create_app(monitor,token="test",allow_control=True)) as client:
            headers={"Authorization":"Bearer test"}
            values=(await client.get("/devices",headers=headers)).json()["devices"]
            assert {value["name"] for value in values}=={"servers","office"}
            response=await client.post("/devices/office/commands",json={"command":"set-temperature-unit","fahrenheit":True},headers=headers)
            assert response.status_code==200
            assert calls==[(directory,"set-temperature-unit",{"name":"office","fahrenheit":True})]
            response=await client.post("/devices/servers/commands",json={"command":"set-temperature-unit","fahrenheit":True},headers=headers)
            assert response.status_code==403 and len(calls)==1
    asyncio.run(run())
