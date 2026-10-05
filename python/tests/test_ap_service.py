import asyncio
import base64
from dataclasses import replace
import json
from pathlib import Path
import ssl
import time

from cryptography import x509
import pytest

from solix_gen2 import APServiceConfig, LocalMqttServer, initialize_ap_service, load_ap_service
from solix_gen2.isolated_ap import IsolatedAP
from solix_gen2.ap_service_config import private_write
from solix_gen2.ap_service_monitor import APServiceMonitor
from solix_gen2.ap_service import api_response, http_reply, ntp_reply
from solix_gen2.mqtt_credentials import decrypt_device_credential
from solix_gen2.mqtt_intercept import mqtt_packet, native_response, read_mqtt
from solix_gen2.protocol import DATA_RESPONSE, build_packet, parse_packet, parse_tlvs, tlv


@pytest.fixture
def ap_service(tmp_path):
    config = APServiceConfig("ups", "wlan_ap", "phy9", "AT", "A1783SYNTHETIC001", "a" * 40,
                       timezone_name="Europe/Vienna")
    directory = tmp_path / "private"
    initialize_ap_service(directory, config)
    return config, directory


def test_private_bootstrap_certificates_and_no_overwrite(ap_service):
    config, directory = ap_service
    assert load_ap_service(directory / "ap_service.json") == config
    assert directory.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in directory.iterdir())
    response = json.loads((directory / "mqtt-response.json").read_text())["data"]
    assert response["aws_root_ca1_pem"].encode() == (directory / "ca.pem").read_bytes()
    assert decrypt_device_credential(config.device_serial, response["private_key"]) == (directory / "client-key.pem").read_bytes()
    cert = x509.load_pem_x509_certificate((directory / "server.pem").read_bytes())
    assert config.broker_host in cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    ca = x509.load_pem_x509_certificate((directory / "ca.pem").read_bytes())
    issuer_id = ca.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value.digest
    for filename in ("server.pem", "client.pem"):
        leaf = x509.load_pem_x509_certificate((directory / filename).read_bytes())
        assert leaf.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier).value.key_identifier == issuer_id
        usage = leaf.extensions.get_extension_for_class(x509.KeyUsage)
        assert usage.critical and usage.value.digital_signature and not usage.value.key_cert_sign
    assert config.account_id not in repr(config) and config.passphrase not in repr(config)
    before = (directory / "client-key.pem").read_bytes()
    with pytest.raises(FileExistsError):
        initialize_ap_service(directory, config)
    assert (directory / "client-key.pem").read_bytes() == before


@pytest.mark.parametrize("changes", [{"namespace": "bad; command"}, {"ssid": "x\nssid=other"},
                                     {"passphrase": "password\ncommand"}, {"gateway": "8.8.8.1"},
                                     {"gateway": "127.0.0.1"}, {"broker_host": "evil/host"},
                                     {"device_serial": "short"}, {"account_id": "bad"}])
def test_reject_unsafe_ap_service_configuration(ap_service, changes):
    with pytest.raises(ValueError):
        replace(ap_service[0], **changes)


def test_refuse_public_config_and_symlink(ap_service, tmp_path):
    config, directory = ap_service
    path = directory / "ap_service.json"
    path.chmod(0o644)
    with pytest.raises(ValueError, match="owner-only"):
        load_ap_service(path)
    target = tmp_path / "target"
    target.write_text("unchanged")
    link = directory / "link"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        private_write(link, "secret")
    assert target.read_text() == "unchanged"


def test_api_framing_and_ntp_without_forwarding(ap_service):
    config, _ = ap_service
    credentials = b'{"certificate_pem":"synthetic"}'
    body, chunked = api_response("/equipment/devicemanage/get_mqtt_info", {"device_sn": config.device_serial}, config, credentials)
    wire = http_reply(body, credentials=chunked)
    assert b"Transfer-Encoding: chunked" in wire
    assert wire.endswith(b"0\r\n\r\n")
    # Parse the exact one-byte chunks, rather than accepting only a header match.
    framed = wire.split(b"\r\n\r\n", 1)[1]
    assert framed == b"".join(b"1\r\n" + bytes([v]) + b"\r\n" for v in credentials) + b"0\r\n\r\n"
    assert api_response("//equipment/devicemanage/get_mqtt_info", {}, config, credentials) == (credentials, True)
    body, chunked = api_response("/equipment/devicerelation/bind_device", {}, config, credentials)
    assert not chunked and json.loads(body)["data"]["is_bind"]
    assert f"Content-Length: {len(body)}".encode() in http_reply(body)
    with pytest.raises(ValueError):
        api_response("/equipment/devicemanage/get_mqtt_info", {"device_sn": "other"}, config, credentials)
    with pytest.raises(ValueError):
        api_response("https://example.invalid/unknown", {}, config, credentials)
    request = bytes([0x23]) + bytes(39) + b"12345678"
    reply = ntp_reply(request, 1800000000.5)
    assert len(reply) == 48 and reply[24:32] == b"12345678"
    assert reply[0] & 7 == 4
    assert int.from_bytes(reply[40:44], "big") == 1800000000 + 2208988800
    assert ntp_reply(b"short", time.time()) is None


@pytest.mark.parametrize("enabled", [False, True])
def test_point_switch_schema_requires_explicit_energy_reporting(ap_service, enabled):
    config, _ = ap_service
    body, chunked = api_response("/equipment/agreement/get_device_point_switch", {}, config, b"",
                                 energy_reports=enabled)
    response = json.loads(body)
    assert response["code"] == 0 and response["msg"] == "success"
    assert response["data"] == {"param": [{"param_name": "20001", "param_value": str(int(enabled))}]}
    assert not chunked
    default, _ = api_response("/equipment/agreement/get_device_point_switch", {}, config, b"")
    assert json.loads(default)["data"]["param"][0]["param_value"] == "0"


def response(config, command, fields=b"", *, serial=None):
    frame = build_packet(DATA_RESPONSE, bytes.fromhex(command), b"\x00" + fields)
    return json.dumps({"payload": json.dumps({"sn": serial or config.device_serial, "pn": "A1783",
                                               "data": base64.b64encode(frame).decode()})}).encode()


def test_native_response_filters_identity_and_malformed_envelopes(ap_service):
    config, _ = ap_service
    assert native_response(response(config, "0901"), config).command.hex() == "0901"
    assert native_response(response(config, "0901", serial="other"), config) is None
    for message in (b"[]", b'{"payload":"[]"}', b"bad json"):
        with pytest.raises(ValueError):
            native_response(message, config)


async def fake_station(config, directory, port, *, retained_first=False, reserve=10, cap_behavior="apply"):
    context = ssl.create_default_context(cafile=str(directory / "ca.pem"))
    context.load_cert_chain(directory / "client.pem", directory / "client-key.pem")
    reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=context, server_hostname=config.broker_host)
    client_id = b"synthetic-station"
    writer.write(mqtt_packet(0x10, b"\x00\x04MQTT\x04\x02\x00\x3c" + len(client_id).to_bytes(2, "big") + client_id))
    await writer.drain()
    assert await read_mqtt(reader) == (0x20, b"\x00\x00")
    topic = f"cmd/anker_power/A1783/{config.device_serial}/req".encode()
    writer.write(mqtt_packet(0x82, b"\x00\x01" + len(topic).to_bytes(2, "big") + topic + b"\x01"))
    await writer.drain()
    assert await read_mqtt(reader) == (0x90, b"\x00\x01\x01")
    captured = []
    async def respond():
        watts = 1800
        upper = 100 if reserve > 90 else 90
        reported_reserve = reserve
        first_status = True
        try:
            while True:
                first, body = await read_mqtt(reader)
                if first == 0x40:
                    continue
                assert first == 0x30  # No retained or QoS2 commands.
                size = int.from_bytes(body[:2], "big")
                envelope = json.loads(body[2 + size:])
                frame = parse_packet(base64.b64decode(json.loads(envelope["payload"])["data"]))
                command = frame.command.hex()
                captured.append(frame)
                fields = b""
                if command == "0101":
                    tags = parse_tlvs(frame.payload)
                    assert set(tags) == {0xA1, 0xA4, 0xFD}
                    watts = int.from_bytes(tags[0xA4][1:], "little")
                elif command == "0103":
                    tags = parse_tlvs(frame.payload)
                    assert set(tags) == {0xA1, 0xAA, 0xFD}
                    if cap_behavior != "ignored":
                        upper = tags[0xAA][1]
                    if cap_behavior == "reserve_changed":
                        reported_reserve += 1
                elif command == "0100":
                    schedule_tail = bytes(18 if cap_behavior == "truncated_schedule" else 19)
                    fields = (tlv(0xA5, bytes([4, 25, 0, 90, 100]))
                              + tlv(0xA7, bytes.fromhex("04015600015600"))
                              + tlv(0xA4, bytes(5) + watts.to_bytes(2, "little"))
                              + tlv(0xD9, bytes([4, 0, 0, reported_reserve, upper, 1, 0]) + schedule_tail))
                elif command == "0089":
                    fields = tlv(0xA1, b"\x34")
                else:
                    raise AssertionError(command)
                message = response(config, f"{int(command, 16) | 0x800:04x}", fields)
                topic = f"dt/anker_power/A1783/{config.device_serial}/param_info".encode()
                prefix = len(topic).to_bytes(2, "big") + topic
                if retained_first and first_status and command == "0100":
                    writer.write(mqtt_packet(0x31, prefix + message))
                    await writer.drain()
                    await asyncio.sleep(0.1)
                first_status = False
                writer.write(mqtt_packet(0x32, prefix + b"\x00\x02" + message))
                await writer.drain()
        except (asyncio.IncompleteReadError, OSError):
            pass
        finally:
            writer.close()
    return asyncio.create_task(respond()), captured, writer


def test_tls_mqtt_native_controls_freshness_and_cleanup(ap_service):
    async def run():
        config, directory = ap_service
        server = LocalMqttServer(config, directory)
        await server.start(host="127.0.0.1", port=0)
        port = server._server.sockets[0].getsockname()[1]
        task, captured, writer = await fake_station(config, directory, port, retained_first=True)
        try:
            await asyncio.sleep(0.05)
            assert server.last_seen is None  # Retained reading cannot establish freshness.
            async with asyncio.timeout(2):
                while not server.snapshot()["available"]:
                    await asyncio.sleep(0.01)
            assert config.device_serial not in json.dumps(server.snapshot())
            with pytest.raises(PermissionError):
                await server.set_ac_charging_power(1700)
            with pytest.raises(PermissionError):
                await server.set_charge_cap(95)
            assert not any(frame.command.hex() == "0101" for frame in captured)
            server.allow_control = True
            result = await server.set_ac_charging_power(1700)
            assert result["metrics"]["ac_charging_power_limit_w"] == 1700
            assert result["metrics"]["ac_output_enabled"] == 1
            result = await server.set_ac_charging_power(1800)
            assert result["metrics"]["ac_charging_power_limit_w"] == 1800
            result = await server.set_charge_cap(95)
            assert result["metrics"]["max_charge_percentage"] == 95
            assert result["metrics"]["min_charge_percentage"] == 1
            assert result["metrics"]["backup_reserve_percentage"] == 10
            result = await server.set_charge_cap(90)
            assert result["metrics"]["max_charge_percentage"] == 90
            await server.connection.request(server.commands.readiness())
            server.last_seen = time.time() - 31
            assert not server.snapshot()["available"]
            writer.write(mqtt_packet(0xE0, b""))
            await writer.drain()
            await asyncio.wait_for(task, 3)
            await writer.wait_closed()
            await asyncio.sleep(0.05)
            assert not server.snapshot()["available"]
        finally:
            await asyncio.wait_for(server.stop(), 5)
            await asyncio.wait_for(task, 5)
        assert set(frame.command.hex() for frame in captured) <= {"0100", "0101", "0103", "0089"}
        assert (directory / "mqtt-events.jsonl").stat().st_mode & 0o777 == 0o600
    asyncio.run(run())


@pytest.mark.parametrize("reserve,behavior,error,match", [
    (95, "apply", ValueError, "backup reserve"),
    (10, "ignored", RuntimeError, "not confirmed"),
    (10, "reserve_changed", RuntimeError, "Another setting changed"),
    (10, "truncated_schedule", RuntimeError, "Missing fresh charge-limit baseline"),
])
def test_native_charge_cap_rejects_reserve_clamping_and_false_confirmation(ap_service, reserve, behavior, error, match):
    async def run():
        config, directory = ap_service
        server = LocalMqttServer(config, directory, allow_control=True)
        await server.start(host="127.0.0.1", port=0)
        task, captured, writer = await fake_station(
            config, directory, server._server.sockets[0].getsockname()[1], reserve=reserve, cap_behavior=behavior)
        try:
            async with asyncio.timeout(2):
                while not server.snapshot()["available"]:
                    await asyncio.sleep(0.01)
            with pytest.raises(error, match=match):
                await server.set_charge_cap(90 if reserve == 95 else 95)
            writes = [frame for frame in captured if frame.command.hex() == "0103"]
            assert len(writes) == (0 if reserve == 95 or behavior == "truncated_schedule" else 1)
        finally:
            writer.write(mqtt_packet(0xE0, b""))
            await writer.drain()
            await asyncio.wait_for(task, 5)
            await writer.wait_closed()
            await asyncio.wait_for(server.stop(), 5)
    asyncio.run(run())


def test_mqtt_oversize_length_rejected_before_reading_payload():
    async def run():
        reader = asyncio.StreamReader()
        reader.feed_data(b"\x30\x81\x80\x08")  # 131073 bytes.
        with pytest.raises(ValueError, match="too large"):
            await read_mqtt(reader)
    asyncio.run(run())


def test_shutdown_closes_an_active_tls_station_before_waiting_for_listener(ap_service):
    async def run():
        config, directory = ap_service
        server = LocalMqttServer(config, directory)
        await server.start(host="127.0.0.1", port=0)
        task, _, writer = await fake_station(config, directory, server._server.sockets[0].getsockname()[1])
        try:
            async with asyncio.timeout(2):
                while not server.snapshot()["available"]:
                    await asyncio.sleep(0.01)
            # The client remains connected; it has not sent DISCONNECT.
            await asyncio.wait_for(server.stop(), 5)
            assert not server.snapshot()["connected"]
            await asyncio.wait_for(task, 5)
            await writer.wait_closed()
        finally:
            writer.close()
            await asyncio.wait_for(server.stop(), 5)
    asyncio.run(run())


def test_service_shutdown_cancels_an_incomplete_http_request(ap_service):
    from solix_gen2.ap_service import APService
    async def run():
        config, directory = ap_service
        service = APService(config, directory)
        listener = await asyncio.start_server(service._api, "127.0.0.1", 0)
        service._servers.append(listener)
        reader, writer = await asyncio.open_connection("127.0.0.1", listener.sockets[0].getsockname()[1])
        try:
            writer.write(b"POST /equipment/devicemanage/get_mqtt_info HTTP/1.1\r\n")
            await writer.drain()
            async with asyncio.timeout(2):
                while not service._clients:
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(service.stop(), 5)
            try:
                assert await asyncio.wait_for(reader.read(), 2) == b""
            except ConnectionResetError:
                pass  # Closing with unread request bytes may reset the socket.
            assert not service._clients and not service._servers
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionResetError:
                pass
            await service.stop()
    asyncio.run(run())


def test_namespace_cleanup_returns_adapter_before_deleting_namespace(ap_service, monkeypatch):
    config, directory = ap_service
    ap = IsolatedAP(config, directory)
    ap.created = ap.moved = True
    calls = []
    monkeypatch.setattr(ap, "_run", lambda *args: calls.append(args) or "")
    ap.stop()
    assert any(args[-5:] == ("ip", "link", "set", config.interface, "down") for args in calls)
    move = next(i for i, args in enumerate(calls) if "phy" in args)
    delete = next(i for i, args in enumerate(calls) if args[:3] == ("ip", "netns", "del"))
    host_down = next(i for i, args in enumerate(calls) if args == ("ip", "link", "set", config.interface, "down"))
    host_flush = next(i for i, args in enumerate(calls) if args == ("ip", "addr", "flush", "dev", config.interface))
    assert move < host_down < host_flush < delete
    assert not ap.created and not ap.moved


def test_namespace_retained_if_adapter_cannot_return(ap_service, monkeypatch):
    ap = IsolatedAP(*ap_service)
    ap.created = ap.moved = True
    calls = []
    def fail(*args):
        calls.append(args)
        if "phy" in args:
            raise OSError("simulated")
        return ""
    monkeypatch.setattr(ap, "_run", fail)
    with pytest.raises(RuntimeError, match="retained"):
        ap.stop()
    assert not any(args[:3] == ("ip", "netns", "del") for args in calls)


@pytest.mark.parametrize("existing_namespace", [False, True])
def test_ap_refuses_existing_namespace_or_active_adapter_before_mutation(ap_service, monkeypatch, existing_namespace):
    ap = IsolatedAP(*ap_service)
    monkeypatch.setattr("solix_gen2.isolated_ap.os.geteuid", lambda: 0)
    monkeypatch.setattr("solix_gen2.isolated_ap.shutil.which", lambda value: value)
    calls = []
    def run(*args):
        calls.append(args)
        if args == ("ip", "netns", "list"):
            return ap.config.namespace + "\n" if existing_namespace else ""
        if args[:3] == ("ip", "-j", "link"):
            return '[{"flags":["UP"]}]'
        raise AssertionError("Must not reach any mutation")
    monkeypatch.setattr(ap, "_run", run)
    with pytest.raises(RuntimeError, match="existing" if existing_namespace else "DOWN"):
        ap.start()
    assert not ap.created and not ap.moved
    assert not any(args[:3] == ("ip", "netns", "add") for args in calls)


def test_http_adapter_marks_stale_worker_and_readings_unavailable(ap_service, monkeypatch):
    config, directory = ap_service
    service = APServiceMonitor(config, directory)
    private_write(directory / "status.json", json.dumps({"name": config.name, "connected": True,
                  "last_seen_timestamp": time.time() - 40, "metrics": {}}))
    assert not service.snapshot(config.name)["available"]
    assert not service.snapshot(config.name)["metrics"]


def test_native_http_auth_and_freshness(ap_service):
    from http_helpers import api_client
    from solix_gen2.server import create_app
    async def run():
        config, directory = ap_service
        service = APServiceMonitor(config, directory)
        private_write(directory / "status.json", json.dumps({"name": config.name, "connected": True,
                      "last_seen_timestamp": time.time(), "metrics": {"battery_percentage": 90}}))
        async with api_client(create_app(service, token="test-token")) as client:
            assert (await client.get("/devices")).status_code == 401
            headers = {"Authorization": "Bearer test-token"}
            value = (await client.get("/devices/ups", headers=headers)).json()
            assert value["available"] and value["metrics"]["battery_percentage"] == 90
            assert config.device_serial not in json.dumps(value)
            private_write(directory / "status.json", json.dumps({"name": config.name, "connected": False,
                          "last_seen_timestamp": time.time(), "metrics": {"battery_percentage": 90}}))
            assert (await client.get("/health", headers=headers)).status_code == 503
    asyncio.run(run())


def test_request_timeout_closes_connection_and_prevents_late_ack_reuse(ap_service):
    from solix_gen2.mqtt_intercept import _Connection
    class Writer:
        closed = False
        def is_closing(self):
            return self.closed
        def write(self, data):
            pass
        async def drain(self):
            pass
        def close(self):
            self.closed = True
    async def run():
        config, directory = ap_service
        server = LocalMqttServer(config, directory)
        writer = Writer()
        connection = _Connection(server, asyncio.StreamReader(), writer)
        connection.subscribed = True
        with pytest.raises(TimeoutError):
            await connection.request(server.commands.ac_charging_power(1700), timeout=0.01)
        assert writer.closed and connection.pending is None
        with pytest.raises(ConnectionError):
            await connection.request(server.commands.ac_charging_power(1800), timeout=0.01)
    asyncio.run(run())
