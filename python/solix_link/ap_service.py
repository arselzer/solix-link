"""Local HTTP credential bootstrap, NTP and MQTT services inside the AP namespace."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import signal
import struct
import time

from .ap_service_config import APServiceConfig, load_ap_service, load_ap_service_profiles, private_write
from .ap_service_fleet import MqttDeviceRouter
from .mqtt_intercept import LocalMqttServer
from .protocol import Model, parse_tlvs, timezone_confer
from .tou import PowerFlowTimeout, TouPeriod
from .energy_report import decode_energy_events
from .commands import native_commands_for_model, validate_command


def api_response(path: str, request: dict, config: APServiceConfig, credentials: bytes,
                 *, energy_reports: bool = False) -> tuple[bytes, bool]:
    """Return the minimal observed device API schema, never forwarding requests."""
    if type(energy_reports) is not bool:
        raise ValueError("Energy reporting must be a boolean")
    # The radio concatenates a trailing-slash base URL with a leading-slash path.
    path = "/" + path.lstrip("/")
    if not isinstance(request, dict) or request.get("device_sn", config.device_serial) != config.device_serial:
        raise ValueError("Unexpected device identity")
    if path == "/equipment/devicemanage/get_mqtt_info":
        # C1000 0.3.3.0 joined MQTT with Content-Length; one-byte chunks stalled
        # its live bootstrap. Retain the verified C2000 short-read workaround.
        return credentials, config.model == Model.C2000_GEN2
    if path in ("/equipment/devicerelation/bind_device", "/equipment/devicerelation/check_relate_bind_device"):
        data = {"device_sn": config.device_serial, "account": request.get("account", ""),
                "is_bind": True, "is_relate": True, "bind": True, "relate": True,
                "bind_status": 1, "relate_status": 1, "status": 1, "result": True,
                "bind_state": 1, "relate_state": 1}
    elif path == "/equipment/help/dst":
        data = {"timezone": timezone_confer(config.timezone_name)[1].decode()}
    elif path == "/equipment/logging/upload_pb_events":
        # Local acknowledgement only; the cloud's response schema is unverified.
        decode_energy_events(request)
        data = {}
    elif path == "/equipment/agreement/get_device_point_switch":
        # Recovered C1000 radio 0.3.3.0 accepts the first param's exact name and
        # string value. This analytics flag is independent of power controls.
        data = {"param": [{"param_name": "20001", "param_value": "1" if energy_reports else "0"}]}
    elif path == "/equipment/devicemanage/update_info":
        data = {}
    else:
        raise ValueError("Unsupported local API path")
    return json.dumps({"code": 0, "msg": "success", "data": data, "trace_id": ""}).encode(), False


def http_reply(body: bytes, *, credentials: bool = False, status: int = 200) -> bytes:
    """Only the large certificate response uses the verified short-read workaround."""
    header = f"HTTP/1.1 {status} {'OK' if status == 200 else 'Bad Request'}\r\nContent-Type: application/json\r\nConnection: close\r\n"
    if credentials:
        return (header + "Transfer-Encoding: chunked\r\n\r\n").encode() + b"".join(
            b"1\r\n" + bytes([value]) + b"\r\n" for value in body) + b"0\r\n\r\n"
    return (header + f"Content-Length: {len(body)}\r\n\r\n").encode() + body


def ntp_reply(request: bytes, now: float) -> bytes | None:
    if len(request) != 48 or request[0] & 7 != 3 or (request[0] >> 3) & 7 not in (3, 4):
        return None
    def timestamp(value: float) -> bytes:
        return struct.pack("!II", int(value) + 2208988800, int(value % 1 * 2**32))
    reply = bytearray(48)
    reply[:4] = bytes([(request[0] & 0x38) | 4, 2, request[2], 0xEC])
    reply[4:12] = struct.pack("!II", 1 << 16, 1 << 16)
    reply[12:16] = b"LOCL"
    reply[16:24] = timestamp(now - 1)
    reply[24:32] = request[40:48]
    reply[32:40] = timestamp(now)
    reply[40:48] = timestamp(now)
    return bytes(reply)


class _Ntp(asyncio.DatagramProtocol):
    def __init__(self, mqtt: LocalMqttServer) -> None:
        self.mqtt = mqtt

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, address) -> None:
        reply = ntp_reply(data, time.time())
        if reply is not None:
            self.transport.sendto(reply, address)
            self.mqtt.record("ntp_served")


class APService:
    """Own device-facing services. Use only on the isolated AP interface.

    The Unix control socket is owner-only. Network-facing HTTP emulates the
    station bootstrap API; it does not expose charging controls to the station.
    """

    def __init__(self, config: APServiceConfig, directory: Path, *, allow_control: bool = False,
                 energy_reports: bool = False, callback=None) -> None:
        if type(energy_reports) is not bool:
            raise ValueError("Energy reporting must be a boolean")
        self.config, self.directory = config, directory
        self.energy_reports = energy_reports
        profiles = load_ap_service_profiles(directory, config)
        self.stations = {name: LocalMqttServer(item, path, allow_control=allow_control, callback=callback)
                         for name, (item, path) in profiles.items()}
        self.mqtt = self.stations[config.name]
        self._router = MqttDeviceRouter(self.stations, directory) if len(self.stations) > 1 else None
        self._by_serial = {station.config.device_serial: station for station in self.stations.values()}
        self.credentials = (directory / "mqtt-response.json").read_bytes()
        self._servers: list[asyncio.Server] = []
        self._transport = None
        self._clients: set[asyncio.Task] = set()
        self._heartbeat: asyncio.Task | None = None
        self.socket_path = directory / "control.sock"
        self._socket_owned = False
        self._ready_owned = False

    async def start(self, *, api_port: int = 80, mqtt_port: int = 8883, ntp_port: int = 123) -> None:
        # Never unlink a potentially active worker's socket.
        if self.socket_path.exists():
            raise RuntimeError("Existing AP service control socket; stop or recover the previous worker first")
        try:
            if self._router:
                await self._router.start(self.config.gateway, mqtt_port)
            else:
                await self.mqtt.start(port=mqtt_port)
            self._servers.append(await asyncio.start_server(self._api, self.config.gateway, api_port, limit=16384))
            self._transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                lambda: _Ntp(self.mqtt), local_addr=(self.config.gateway, ntp_port))
            self._servers.append(await asyncio.start_unix_server(self._control, path=self.socket_path, limit=4096))
            self._socket_owned = True
            os.chmod(self.socket_path, 0o600)
            self._heartbeat = asyncio.create_task(self._refresh())
            private_write(self.directory / "ready", "ready\n")
            self._ready_owned = True
        except BaseException:
            await self.stop()
            raise

    async def _refresh(self) -> None:
        while True:
            for station in self.stations.values():
                station.changed()
            await asyncio.sleep(5)

    async def _api(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        self._clients.add(task)
        try:
            if len(self._clients) > 8:
                return
            async with asyncio.timeout(10):
                headers = await reader.readuntil(b"\r\n\r\n")
                lines = headers.decode("ascii").split("\r\n")
                method, path, version = lines[0].split()
                if method != "POST" or version not in ("HTTP/1.0", "HTTP/1.1"):
                    raise ValueError("Unsupported HTTP request")
                fields = {}
                for line in lines[1:]:
                    if line:
                        key, value = line.split(":", 1)
                        key = key.lower()
                        if key in fields:
                            raise ValueError("Duplicate HTTP header")
                        fields[key] = value.strip()
                if "transfer-encoding" in fields:
                    raise ValueError("Chunked requests are unsupported")
                length = int(fields.get("content-length", "0"))
                if not 0 <= length <= 16384:
                    raise ValueError("Request too large")
                body = await reader.readexactly(length)
                self.mqtt.record("api_request", headers_hex=headers.hex(), body_hex=body.hex())
                request = json.loads(body or b"{}")
                if not isinstance(request, dict):
                    raise ValueError("Invalid request")
                identity = fields.get("device-sn") or request.get("device_sn")
                if identity is None and len(self.stations) == 1:
                    identity = self.config.device_serial
                station = self._by_serial.get(identity) if isinstance(identity, str) else None
                if station is None or request.get("device_sn", identity) != identity:
                    raise ValueError("Unexpected device identity")
                credentials = (station.directory / "mqtt-response.json").read_bytes()
                response, chunked = api_response(path, request, station.config, credentials,
                                                 energy_reports=self.energy_reports)
                if "/" + path.lstrip("/") == "/equipment/logging/upload_pb_events":
                    station.ingest_energy(decode_energy_events(request))
                writer.write(http_reply(response, credentials=chunked))
                await writer.drain()
                self.mqtt.record("api_response", path=path, size=len(response),
                                 framing="one_byte_chunks" if chunked else "content_length")
        except (ValueError, UnicodeError, asyncio.LimitOverrunError, asyncio.IncompleteReadError):
            writer.write(http_reply(b'{"code":400}', status=400))
            try:
                await writer.drain()
            except OSError:
                pass
        except (OSError, TimeoutError):
            pass
        finally:
            writer.close()
            self._clients.discard(task)

    async def _control(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        self._clients.add(task)
        try:
            if len(self._clients) > 8:
                return
            async with asyncio.timeout(10):
                request = json.loads(await reader.readline())
                if not isinstance(request, dict):
                    raise ValueError("Invalid request")
            action = request.get("command")
            name = request.pop("name", None)
            if action == "status" and name is None and len(self.stations) > 1:
                response = {"ok": True, "result": {"devices": [station.snapshot() for station in self.stations.values()]}}
                return
            if name is None and len(self.stations) == 1:
                name = self.config.name
            if not isinstance(name, str) or name not in self.stations:
                raise ValueError("Select a configured station by name")
            mqtt = self.stations[name]
            local_output = action == "set-ac-output" and mqtt.config.model == Model.C1000_GEN2
            local_countdown = action == "set-ac-countdown" and mqtt.config.model == Model.C1000_GEN2
            local_wireless = action in ("wireless-state", "wifi-rssi") and mqtt.config.model == Model.C1000_GEN2
            if action not in ("status", "readiness") and not (local_output or local_countdown or local_wireless) and action not in native_commands_for_model(mqtt.config.model):
                raise ValueError("This native control is not verified for the selected model")
            if action == "readiness" and mqtt.config.model == Model.C1000:
                raise ValueError("Controller readiness is unavailable for original C1000")
            if action != "status" and mqtt.config.model == Model.C1000:
                validate_command(action, {key: value for key, value in request.items() if key != "command"})
            async with asyncio.timeout(control_timeout(action, request)):
                if action == "status":
                    result = mqtt.snapshot()
                elif local_output:
                    if set(request) != {"command", "enabled"} or type(request["enabled"]) is not bool:
                        raise ValueError("Use an explicit enabled boolean")
                    result = await mqtt.set_ac_output_enabled(request["enabled"])
                elif local_countdown:
                    if set(request) != {"command", "seconds"}:
                        raise ValueError("Use only the explicit seconds field")
                    result = await mqtt.set_ac_countdown(request["seconds"])
                elif local_wireless:
                    if set(request) != {"command"}:
                        raise ValueError("Radio query accepts no extra fields")
                    result = await mqtt.wireless_state() if action == "wireless-state" else await mqtt.wifi_rssi()
                elif action == "set-charge-power":
                    result = await mqtt.set_ac_charging_power(request.get("watts"))
                elif action == "set-charge-cap":
                    result = await mqtt.set_charge_cap(request.get("upper"))
                elif action == "set-discharge-floor":
                    values = {key: value for key, value in request.items() if key != "command"}
                    validate_command(action, values)
                    result = await mqtt.set_discharge_floor(values["lower"])
                elif action == "set-clock-brightness":
                    values = {key: value for key, value in request.items() if key != "command"}
                    validate_command(action, values)
                    result = await mqtt.set_clock_brightness(values["window"], values["high"])
                elif action in ("set-temperature-unit", "set-off-grid-alert", "set-device-timeout", "set-fast-charge",
                                "set-display-brightness", "set-display-timeout", "set-port-memory", "set-light", "set-dc-power-saving", "set-ac-power-saving"):
                    values = {key: value for key, value in request.items() if key != "command"}
                    validate_command(action, values)
                    if action == "set-temperature-unit":
                        result = await mqtt.set_temperature_unit(values["fahrenheit"])
                    elif action == "set-device-timeout":
                        result = await mqtt.set_device_timeout(values["minutes"])
                    elif action == "set-fast-charge":
                        result = await mqtt.set_fast_charge_enabled(values["enabled"])
                    elif action == "set-display-brightness":
                        result = await mqtt.set_display_brightness(values["level"])
                    elif action == "set-display-timeout":
                        result = await mqtt.set_display_timeout(values["seconds"])
                    elif action == "set-light":
                        result = await mqtt.set_light_mode(values["mode"])
                    elif action == "set-dc-power-saving":
                        result = await mqtt.set_dc_power_saving_enabled(values["enabled"])
                    elif action == "set-ac-power-saving":
                        result = await mqtt.set_ac_power_saving_enabled(values["enabled"])
                    elif action == "set-port-memory":
                        result = await mqtt.set_port_memory(values["enabled"])
                    else:
                        result = await mqtt.set_off_grid_alert(values["enabled"])
                elif action == "set-backup-reserve":
                    result = await mqtt.set_backup_reserve(request.get("reserve"))
                elif action == "set-tou-plan":
                    periods = request.get("periods", [])
                    if not isinstance(periods, list) or len(periods) > 6:
                        raise ValueError("Invalid schedule")
                    parsed = []
                    for period in periods:
                        if not isinstance(period, dict) or set(period) != {"tariff", "start_hour", "end_hour"}:
                            raise ValueError("Invalid schedule period")
                        parsed.append(TouPeriod(**period))
                    result = await mqtt.set_tou_plan(parsed, enabled=request.get("enabled", False))
                elif action == "return-grid":
                    result = await mqtt.return_to_grid(timeout=request.get("timeout", 30))
                elif action == "readiness":
                    connection = mqtt.connection
                    if connection is None:
                        raise ConnectionError("Station is not connected")
                    reply = await connection.request(mqtt.commands.readiness())
                    fields = parse_tlvs(reply[1:])
                    # Opaque FD may contain an app identifier; never return it publicly.
                    result = {"controller_ready_prefix": fields.get(0xA1, b"").hex(),
                              "a2": fields.get(0xA2, b"").hex(), "a3": fields.get(0xA3, b"").hex()}
                else:
                    raise ValueError("Unsupported command")
                response = {"ok": True, "result": result}
        except PowerFlowTimeout as error:
            response = {"ok": False, "error": "PowerFlowTimeout", "result": error.snapshot}
        except (ValueError, OSError, TimeoutError, RuntimeError, asyncio.LimitOverrunError) as error:
            # Exception text is controlled by this package; avoid reflecting request data.
            response = {"ok": False, "error": type(error).__name__}
        finally:
            try:
                if "response" in locals():
                    writer.write(json.dumps(response).encode() + b"\n")
                    await writer.drain()
            except OSError:
                pass
            writer.close()
            self._clients.discard(task)

    async def stop(self) -> None:
        if self._ready_owned:
            (self.directory / "ready").unlink(missing_ok=True)
            self._ready_owned = False
        if self._heartbeat:
            self._heartbeat.cancel()
            await asyncio.gather(self._heartbeat, return_exceptions=True)
        for server in self._servers:
            server.close()
        if self._transport:
            self._transport.close()
        for task in list(self._clients):
            task.cancel()
        await asyncio.gather(*self._clients, return_exceptions=True)
        for server in self._servers:
            await server.wait_closed()
        self._servers.clear()
        if self._router:
            await self._router.stop()
        for station in self.stations.values():
            await station.stop()
        if self._socket_owned:
            self.socket_path.unlink(missing_ok=True)
            self._socket_owned = False


def control_timeout(command: str, fields: dict) -> int:
    """Bound command transport separately from each power-flow confirmation."""
    if command == "return-grid":
        duration = fields.get("timeout", 30)
        if type(duration) is int and 5 <= duration <= 120:
            return 2 * duration + 100
    if command == "set-tou-plan":
        return 120
    return 45


async def ap_service_request(directory: Path, command: str, **fields) -> dict:
    """Use the private Unix socket from the host namespace or another local process."""
    reader, writer = await asyncio.open_unix_connection(directory / "control.sock", limit=131072)
    try:
        writer.write(json.dumps({"command": command, **fields}).encode() + b"\n")
        await writer.drain()
        async with asyncio.timeout(control_timeout(command, fields) + 5):
            response = json.loads(await reader.readline())
        if not response.get("ok"):
            if response.get("error") == "PowerFlowTimeout":
                raise PowerFlowTimeout(response.get("result", {}))
            errors = {"ValueError": ValueError, "PermissionError": PermissionError,
                      "ConnectionError": ConnectionError, "TimeoutError": TimeoutError}
            if response.get("error") in errors:
                raise errors[response["error"]]("Native AP service command failed; inspect fresh status")
            raise RuntimeError(f"AP service request failed: {response.get('error', 'unknown')}")
        return response["result"]
    finally:
        writer.close()
        await writer.wait_closed()


async def _worker(directory: Path, allow_control: bool, energy_reports: bool = False) -> None:
    service = APService(load_ap_service(directory / "ap_service.json"), directory, allow_control=allow_control,
                               energy_reports=energy_reports)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        await service.start()
        await stop.wait()
    finally:
        await service.stop()


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Device-facing services; run inside the isolated AP namespace")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--allow-control", action="store_true")
    parser.add_argument("--energy-reports", action="store_true")
    args = parser.parse_args()
    asyncio.run(_worker(args.directory, args.allow_control, args.energy_reports))


if __name__ == "__main__":
    main()
