"""Small, single-station MQTT 3.1.1 TLS endpoint for the isolated SOLIX AP service.

This is a device protocol endpoint, not a general MQTT broker. It never bridges
traffic to Anker or another network. AC output control is restricted to the
private operator path for the noncritical C1000 Gen 2; C2000 is excluded.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
import ssl
import time
from typing import Callable

from .ap_service_config import APServiceConfig, private_write
from .diagnostics import decode_wifi_rssi
from .native_mqtt import (RADIO_NATIVE_PATTERN, RADIO_QUERY_RESPONSES, NativeMqttCommands, NativeMqttRequest,
                          decode_mqtt_telemetry, decode_native_wireless_state)
from .protocol import DATA_RESPONSE, Model, decode_telemetry, parse_packet, parse_tlvs, timezone_confer
from .tou import PowerFlowTimeout, TouPeriod, periods_from_d9, power_flow, validate_periods
from .plan_readback import plan_from_d9, validate_plan_readback
from .energy_store import NativeEnergyStore


def _original_settings(payload: bytes, *, require_inactive_ac_timer: bool = False) -> tuple[dict, bytes]:
    """Require a complete report; cached/partial telemetry cannot guard a write."""
    metrics, raw = decode_telemetry(payload, Model.C1000)
    required = ("ac_output_enabled", "dc_output_enabled", "ac_charging_power_limit_w",
                "device_timeout_minutes", "display_timeout_seconds", "display_brightness", "light_mode",
                "temperature_unit_fahrenheit", "ac_fast_charge_enabled", "ac_power_saving_mode_enabled",
                "dc_power_saving_mode_enabled")
    if (any(type(metrics.get(key)) is not int for key in required)
            or any(metrics[key] not in (0, 1) for key in required
                   if key.endswith("_enabled") or key == "temperature_unit_fahrenheit")):
        raise RuntimeError("Missing valid fresh original C1000 settings")
    flags = raw.get(0xF8)
    if not isinstance(flags, bytes) or len(flags) != 21 or flags[0] != 4:
        raise RuntimeError("Missing complete original C1000 F8 flags")
    if require_inactive_ac_timer and raw.get(0xA2) != b"\x03\x00\x00\x00\x00":
        raise RuntimeError("Original C1000 native AC Smart requires a fresh inactive AC timer")
    return {key: metrics[key] for key in required}, flags


def mqtt_packet(first: int, body: bytes) -> bytes:
    remaining = len(body)
    if remaining > 131072:
        raise ValueError("MQTT packet too large")
    result = bytearray([first])
    while True:
        digit = remaining % 128
        remaining //= 128
        result.append(digit | (128 if remaining else 0))
        if not remaining:
            return bytes(result) + body


async def read_mqtt(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    first = (await reader.readexactly(1))[0]
    remaining = 0
    for shift in range(0, 28, 7):
        digit = (await reader.readexactly(1))[0]
        remaining |= (digit & 127) << shift
        if not digit & 128:
            break
    else:
        raise ValueError("Invalid MQTT remaining length")
    if remaining > 131072:
        raise ValueError("MQTT packet too large")
    return first, await reader.readexactly(remaining)


def mqtt_string(body: bytes, position: int, *, allow_zero_client_id: bool = False) -> tuple[str, int]:
    if position + 2 > len(body):
        raise ValueError("Truncated MQTT string")
    size = int.from_bytes(body[position:position + 2], "big")
    end = position + 2 + size
    if end > len(body):
        raise ValueError("Truncated MQTT string")
    raw = body[position + 2:end]
    # A1763 startup can send an all-zero 17-byte client ID. TLS and the exact
    # station subscription identify the peer; this ID is never used for routing.
    if allow_zero_client_id and size == 17 and raw == bytes(17):
        return "", end
    value = raw.decode("utf-8")
    if "\x00" in value:
        raise ValueError("Invalid MQTT string")
    return value, end


def native_response(message: bytes, config: APServiceConfig, *, radio_query: bool = False):
    """Validate device identity before accepting any acknowledgement."""
    try:
        outer = json.loads(message)
        if not isinstance(outer, dict) or not isinstance(outer.get("payload"), str):
            raise ValueError
        inner = json.loads(outer["payload"])
        if not isinstance(inner, dict):
            raise ValueError
        if inner.get("sn") != config.device_serial or inner.get("pn") != config.product:
            return None
        if inner.get("encoding_type", 0) != 0:
            raise ValueError
        frame = parse_packet(base64.b64decode(inner["data"], validate=True))
        if frame.pattern == DATA_RESPONSE:
            return frame
        if (radio_query and config.model == Model.C1000_GEN2
                and frame.pattern == RADIO_NATIVE_PATTERN and frame.command.hex() in RADIO_QUERY_RESPONSES):
            return frame
        return None
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise ValueError("Invalid native response") from None


class LocalMqttServer:
    """Async native MQTT server with freshness tracking and telemetry callbacks.

    A status reply is required after every charging write. Timed-out requests
    close the connection, preventing a late ACK from satisfying another write.
    Device responses have their own sequence counter, so commands are serialized
    and matched by opcode within the current connection rather than msg_seq.
    """

    def __init__(self, config: APServiceConfig, directory: Path, *, allow_control: bool = False,
                 callback: Callable[[dict], None] | None = None) -> None:
        self.config = config
        self.directory = directory
        self.allow_control = allow_control
        self.callback = callback
        self.commands = NativeMqttCommands(config.device_serial, config.account_id, model=config.model)
        self.topic = self.commands.status().topic
        self.connection: _Connection | None = None
        self.metrics: dict = {}
        self.energy = NativeEnergyStore(config.model.value, directory / "energy-state.json")
        self.last_seen: float | None = None
        self.tou_plan_readback: dict | None = None
        self.wifi_signal: dict | None = None
        self.original_counters: dict | None = None
        self.error: str | None = None
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task] = set()
        self._clients: set[_Connection] = set()
        self._control_lock = asyncio.Lock()

    def snapshot(self) -> dict:
        from .wifi_signal import validate_wifi_signal
        connected = bool(self.connection and not self.connection.writer.is_closing())
        return {"name": self.config.name, "model": self.config.model.value, "protocol": "native_mqtt",
                "connected": connected,
                "available": bool(connected and self.last_seen and time.time() - self.last_seen < 30),
                "last_seen_timestamp": self.last_seen, "error": self.error, "metrics": self.metrics.copy(),
                "tou_plan_readback": validate_plan_readback(self.tou_plan_readback),
                "native_energy": self.energy.snapshot(),
                "original_counters": self.original_counters,
                "wifi_signal": validate_wifi_signal(self.wifi_signal, model=self.config.model.value,
                                                     protocol="native_mqtt"),
                "control_enabled": self.allow_control,
                "power_flow": power_flow(self.metrics) if self.config.model != Model.C1000 and connected and self.last_seen and time.time() - self.last_seen < 30 else "unknown"}

    def ingest_energy(self, reports: list[dict]) -> None:
        """Accept a routed HTTP upload without touching MQTT telemetry freshness."""
        self.energy.ingest(reports, firmware_version=self.metrics.get("software_version"))
        self.record("energy_report", reports=reports)
        self.changed()

    def ingest_original_counters(self, reports: list[dict]) -> None:
        from .original_counters import REPORT_NAME, validate_original_counters
        report = validate_original_counters({"schema_version": 1, "protobuf_name": REPORT_NAME,
            "units_verified": False, "layout_provenance": "main_1_5_9_encoder",
            "firmware_version": self.metrics.get("software_version"),
            "reported_at": time.time(), "reports": reports}, model=self.config.model.value, protocol="native_mqtt")
        if report is None:
            raise ValueError("Invalid original counter upload")
        self.original_counters = report
        self.record("original_counter_report", report=report)
        self.changed()

    def record(self, event: str, **fields) -> None:
        # Full protocol evidence is deliberately kept only in this private file.
        path = self.directory / "mqtt-events.jsonl"
        if path.is_symlink():
            raise ValueError("Capture file must not be a symlink")
        import os
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "a") as stream:
            stream.write(json.dumps({"time": time.time(), "event": event, **fields}) + "\n")

    def changed(self) -> None:
        snapshot = self.snapshot()
        private_write(self.directory / "status.json", json.dumps(snapshot))
        if self.callback:
            self.callback(snapshot)

    async def start(self, host: str | None = None, port: int = 8883) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.maximum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(self.directory / "server.pem", self.directory / "server-key.pem")
        context.load_verify_locations(self.directory / "ca.pem")
        context.verify_mode = ssl.CERT_REQUIRED
        self._server = await asyncio.start_server(self._accept, host or self.config.gateway, port, ssl=context,
                                                 ssl_handshake_timeout=10, ssl_shutdown_timeout=3, limit=262144)
        self.changed()

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if len(self._clients) >= 4:
            writer.close()
            return
        connection = _Connection(self, reader, writer)
        tls = writer.get_extra_info("ssl_object")
        self.record("tls_connected", version=tls.version() if tls else None,
                    client_certificate=bool(tls and tls.getpeercert()))
        self._clients.add(connection)
        task = asyncio.current_task()
        self._tasks.add(task)
        try:
            await connection.run()
        except (OSError, ValueError, asyncio.IncompleteReadError, TimeoutError) as error:
            self.record("connection_closed", reason=type(error).__name__)
        finally:
            await connection.close()
            self._clients.discard(connection)
            self._tasks.discard(task)
            if self.connection is connection:
                self.connection = None
                self.error = "Disconnected"
                self.changed()

    async def stop(self) -> None:
        if self._server:
            self._server.close()
        # Server.wait_closed() can wait for accepted clients. Close those before
        # awaiting the listener, otherwise an actively connected station hangs shutdown.
        for connection in list(self._clients):
            await connection.close()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._server:
            await self._server.wait_closed()
        self.connection = None
        self.changed()

    async def set_ac_charging_power(self, watts: int) -> dict:
        request = self.commands.ac_charging_power(watts)  # Validate before I/O.
        if self.config.model == Model.C1000:
            return await self._set_original_setting(request, "ac_charging_power_limit_w", watts)
        if self.config.model == Model.C1000_GEN2:
            return await self._set_c1000_setting(request, "ac_charging_power_limit_w", watts)
        if not self.allow_control:
            raise PermissionError("Native charging control is disabled")
        async with self._control_lock:
            connection = self.connection
            if connection is None:
                raise ConnectionError("Station is not connected")
            await connection.request(self.commands.status())
            before = self.metrics.copy()
            if "ac_output_enabled" not in before:
                raise RuntimeError("Missing fresh AC-output baseline")
            await connection.request(request)
            await connection.request(self.commands.status())
            if self.metrics.get("ac_output_enabled") != before["ac_output_enabled"]:
                raise RuntimeError("AC-output state changed unexpectedly")
            if self.metrics.get("ac_charging_power_limit_w") != watts:
                raise RuntimeError("Charging-power setting not confirmed by telemetry")
            return self.snapshot()

    async def set_charge_cap(self, percentage: int) -> dict:
        request = self.commands.charge_cap(percentage)
        if not self.allow_control:
            raise PermissionError("Native charging control is disabled")
        async with self._control_lock:
            connection = self.connection
            if connection is None:
                raise ConnectionError("Station is not connected")
            await connection.request(self.commands.status())
            before = self.metrics.copy()
            protected = ("ac_output_enabled", "min_charge_percentage", "backup_reserve_percentage",
                         "ac_charging_power_limit_w", "usage_mode", "tou_schedule_slot_count")
            if any(field not in before for field in protected):
                raise RuntimeError("Missing fresh charge-limit baseline")
            if percentage < before["backup_reserve_percentage"]:
                raise ValueError("Charge cap below backup reserve would also change the reserve; no write sent")
            await connection.request(request)
            await connection.request(self.commands.status())
            if any(self.metrics.get(field) != before[field] for field in protected):
                raise RuntimeError("Another setting changed during charge-cap confirmation")
            if self.metrics.get("max_charge_percentage") != percentage:
                raise RuntimeError("Charge cap not confirmed by telemetry")
            return self.snapshot()

    def _control_connection(self):
        if not self.allow_control:
            raise PermissionError("Native control is disabled")
        if self.connection is None:
            raise ConnectionError("Station is not connected")
        return self.connection

    async def set_temperature_unit(self, fahrenheit: bool) -> dict:
        request = self.commands.temperature_unit(fahrenheit)  # Model/type guard before I/O.
        if self.config.model == Model.C1000:
            return await self._set_original_setting(request, "temperature_unit_fahrenheit", int(fahrenheit))
        return await self._set_c1000_boolean(request, "temperature_unit_fahrenheit", fahrenheit)

    async def set_off_grid_alert(self, enabled: bool) -> dict:
        request = self.commands.off_grid_alert(enabled)
        return await self._set_c1000_boolean(request, "ac_off_grid_alert_enabled", enabled)

    async def set_device_timeout(self, minutes: int) -> dict:
        request = self.commands.device_timeout(minutes)
        if self.config.model == Model.C1000:
            return await self._set_original_setting(request, "device_timeout_minutes", minutes)
        return await self._set_c1000_setting(request, "device_timeout_minutes", minutes)

    async def set_fast_charge_enabled(self, enabled: bool) -> dict:
        request = self.commands.fast_charge(enabled)
        if self.config.model == Model.C1000:
            return await self._set_original_setting(request, "ac_fast_charge_enabled", int(enabled))
        return await self._set_c1000_setting(request, "ac_fast_charge_enabled", int(enabled))

    async def set_display_brightness(self, level: int) -> dict:
        request = self.commands.display_brightness(level)
        if self.config.model == Model.C1000:
            return await self._set_original_setting(request, "display_brightness", level)
        return await self._set_c1000_setting(request, "display_brightness", level)

    async def set_display_timeout(self, seconds: int) -> dict:
        request = self.commands.display_timeout(seconds)
        if self.config.model == Model.C1000:
            return await self._set_original_setting(request, "display_timeout_seconds", seconds)
        if self.config.model == Model.C2000_GEN2:
            return await self._set_c2000_screen_timeout(request, seconds)
        return await self._set_c1000_setting(request, "display_timeout_seconds", seconds)

    async def _set_c2000_screen_timeout(self, request: NativeMqttRequest, seconds: int) -> dict:
        """Change only the BLE-established screen field, protecting full raw state.

        This native route has synthetic coverage, not a C2000 hardware replay.
        No retries, output commands or automatic restoration are sent.
        """
        async with self._control_lock:
            connection = self._control_connection()

            async def fresh():
                reply = await connection.request(self.commands.status())
                if not reply or reply[0] != 0:
                    raise RuntimeError("C2000 status request failed")
                metrics, fields = decode_telemetry(reply[1:], self.config.model)
                a4, d9 = fields.get(0xA4, b""), fields.get(0xD9, b"")
                periods_from_d9(d9)
                if (len(a4) != 34 or a4[0] != 4
                        or metrics.get("software_version") != "2.1.6.4"
                        or metrics.get("display_timeout_seconds") not in (30, 60)
                        or any(len(fields.get(tag, b"")) < size or fields[tag][0] != 4
                               for tag, size in ((0xA7, 5), (0xB2, 4)))
                        or any(type(metrics.get(key)) is not int or metrics[key] not in (0, 1)
                               for key in ("ac_output_enabled", "dc_output_enabled", "ac_input_connected"))
                        or any(type(metrics.get(key)) is not int or metrics[key] != 0 for key in
                               ("ac_output_timer_remaining_seconds", "dc_output_timer_remaining_seconds"))):
                    raise ValueError("C2000 screen timeout requires main 2.1.6.4, complete settings and inactive countdowns")
                return a4, d9, metrics

            before_a4, before_d9, before = await fresh()
            expected = bytearray(before_a4)
            expected[16:18] = seconds.to_bytes(2, "little")
            await connection.request(request)
            a4, d9, metrics = await fresh()
            if (a4 != bytes(expected) or d9[2:] != before_d9[2:]
                    or any(metrics[key] != before[key] for key in
                           ("ac_output_enabled", "dc_output_enabled", "ac_input_connected"))):
                raise RuntimeError("C2000 screen timeout or protected settings not confirmed; setting may have changed")
            return self._tou_result(d9, metrics)

    async def set_light_mode(self, mode: int) -> dict:
        """Set original C1000 light mode without writing output switches."""
        request = self.commands.light_mode(mode)
        return await self._set_original_setting(request, "light_mode", mode)

    async def set_dc_power_saving_enabled(self, enabled: bool) -> dict:
        """Set C1000 DC Smart with DC off and fresh configuration protected."""
        request = self.commands.dc_power_saving(enabled)
        if self.config.model == Model.C1000_GEN2:
            return await self._set_c1000_setting(request, "dc_power_saving_mode_enabled", int(enabled))
        return await self._set_original_setting(request, "dc_power_saving_mode_enabled", int(enabled))

    async def set_ac_power_saving_enabled(self, enabled: bool) -> dict:
        """Set original AC Smart with AC off, no timer and complete F8 guarded."""
        request = self.commands.ac_power_saving(enabled)
        if self.config.model == Model.C1000_GEN2:
            return await self._set_c1000_setting(request, "ac_power_saving_mode_enabled", int(enabled))
        return await self._set_original_setting(request, "ac_power_saving_mode_enabled", int(enabled))

    async def set_ac_output_enabled(self, enabled: bool) -> dict:
        """Local C1000 Gen 2 operator control; excluded from HTTP and HA capabilities."""
        request = self.commands.ac_output(enabled)
        async with self._control_lock:
            connection = self._control_connection()
            before_a4, before_d9, before = await self._fresh_c1000_settings(connection)
            if (before.get("software_version") != "1.1.4.9"
                    or before["ac_output_timeout_seconds"] != 0 or before["dc_output_timeout_seconds"] != 0):
                raise ValueError("AC output requires main 1.1.4.9 and inactive countdowns; no write sent")
            await connection.request(request)
            for sample in range(2):
                if sample:
                    await asyncio.sleep(1)
                a4, d9, metrics = await self._fresh_c1000_settings(connection)
                expected = bytearray(before_a4)
                expected[22] = a4[22]
                if (metrics["ac_output_enabled"] != int(enabled) or a4 != bytes(expected)
                        or d9[2:] != before_d9[2:] or any(metrics[k] != before[k] for k in
                            ("dc_output_enabled", "ac_input_connected"))):
                    raise RuntimeError("AC output or protected settings not confirmed; inspect fresh status")
            return self._tou_result(d9, metrics)

    async def set_ac_countdown(self, seconds: int) -> dict:
        """Confirm a private C1000-only timer; no output command or retry.

        A positive timer will eventually stop AC. Cancel well before expiry;
        zero readback cannot establish that a queued stop has been revoked.
        """
        request = self.commands.ac_countdown(seconds)  # Model/type guard before I/O.
        async with self._control_lock:
            connection = self._control_connection()
            before_a4, before_d9, before = await self._fresh_c1000_settings(connection)
            if before.get("software_version") != "1.1.4.9":
                raise ValueError("AC countdown requires main 1.1.4.9; no write sent")
            if seconds and (before["ac_output_enabled"] != 1
                            or before["ac_output_timeout_seconds"] != 0
                            or before["dc_output_timeout_seconds"] != 0
                            or before.get("ac_power_saving_mode_enabled") != 0
                            or before.get("usage_mode") != "standard"
                            or before.get("active_tariff") != "none"):
                raise ValueError("New AC countdown requires AC on, Smart off, Standard and inactive AC/DC timers; no write sent")
            await connection.request(request)
            previous = seconds
            for sample in range(2):
                if sample:
                    await asyncio.sleep(1)
                a4, d9, metrics = await self._fresh_c1000_settings(connection)
                remaining = metrics["ac_output_timeout_seconds"]
                # This confirms recent timer storage, not its future expiry.
                # A long delay or unexpected fast consumption leaves an
                # uncertain result instead of assuming seconds follow wall time.
                if ((seconds == 0 and remaining != 0)
                        or (seconds and not seconds - 30 <= remaining <= previous)):
                    raise RuntimeError("AC countdown not confirmed; inspect fresh status before cancellation")
                expected = bytearray(before_a4)
                expected[1:5] = a4[1:5]
                if metrics["dc_output_timeout_seconds"] > before["dc_output_timeout_seconds"]:
                    raise RuntimeError("DC countdown increased during AC countdown confirmation")
                expected[9:13] = a4[9:13]  # Another timer may advance naturally.
                expected[22] = a4[22]  # Normal runtime display activity.
                if (a4 != bytes(expected) or d9[2:] != before_d9[2:]
                        or any(metrics[key] != before[key] for key in
                               ("ac_output_enabled", "dc_output_enabled", "ac_input_connected"))):
                    raise RuntimeError("Protected setting or output changed during AC countdown confirmation")
                previous = remaining
            result = self._tou_result(d9, metrics)
            result["timer_scope"] = "remaining AC countdown; zero cannot revoke a queued stop"
            return result

    async def _fresh_original_settings(self, connection, *, require_inactive_ac_timer: bool = False) -> tuple[dict, bytes]:
        reply = await connection.request(self.commands.status())
        if not reply or reply[0] != 0:
            raise RuntimeError("Original C1000 status request failed")
        return _original_settings(reply[1:], require_inactive_ac_timer=require_inactive_ac_timer)

    async def _set_original_setting(self, request: NativeMqttRequest, metric: str, value: int) -> dict:
        if self.config.model != Model.C1000 or metric not in (
                "ac_charging_power_limit_w", "device_timeout_minutes", "display_brightness",
                "display_timeout_seconds", "light_mode", "temperature_unit_fahrenheit",
                "dc_power_saving_mode_enabled", "ac_fast_charge_enabled", "ac_power_saving_mode_enabled"):
            raise ValueError("Unsupported original C1000 native setting")
        async with self._control_lock:
            connection = self._control_connection()
            ac_smart = metric == "ac_power_saving_mode_enabled"
            before, flags = await self._fresh_original_settings(connection, require_inactive_ac_timer=ac_smart)
            expected_flags = flags
            if metric in ("dc_power_saving_mode_enabled", "ac_power_saving_mode_enabled"):
                domain = "ac" if ac_smart else "dc"
                if before[f"{domain}_output_enabled"] != 0:
                    raise RuntimeError(f"Original C1000 native {domain.upper()} Smart requires the {domain.upper()} output to be off; no write sent")
                changed_flags = bytearray(flags)
                changed_flags[2 if ac_smart else 1] = value + 1  # Normal 1 / Smart 2.
                expected_flags = bytes(changed_flags)
            expected = {**before, metric: value}
            try:
                # Connected original firmware suppresses setter ACKs. Publish
                # once, then confirm via two explicit full status requests.
                await connection.send_original_setting(request)
                for _ in range(2):
                    after, after_flags = await self._fresh_original_settings(connection, require_inactive_ac_timer=ac_smart)
                    connection.check_original_setting_response()
                    if after != expected or after_flags != expected_flags:
                        raise RuntimeError("Protected original C1000 settings or F8 flags changed")
            except (RuntimeError, TimeoutError, ConnectionError, OSError) as error:
                raise RuntimeError("Original C1000 confirmation failed; the setting may have changed") from error
            except asyncio.CancelledError:
                connection.writer.close()
                raise
            finally:
                connection.finish_original_setting()
            return self.snapshot()

    async def set_port_memory(self, enabled: bool) -> dict:
        request = self.commands.port_memory(enabled)
        return await self._set_c1000_setting(request, "port_memory_enabled", int(enabled))

    async def set_discharge_floor(self, lower: int) -> dict:
        """Confirm a lower limit without changing reserve or other settings.

        Firmware can raise reserve as a side effect. Reject that combination
        before writing; do not automatically adjust reserve or retry failures.
        A failed confirmation can leave the requested setting applied.
        """
        request = self.commands.discharge_floor(lower)  # Model/type/range guard before I/O.
        async with self._control_lock:
            connection = self._control_connection()
            before_a4, before_d9, before = await self._fresh_c1000_settings(connection)
            upper, old_lower, reserve = (before[key] for key in (
                "max_charge_percentage", "min_charge_percentage", "backup_reserve_percentage"))
            if before_a4[24] != upper or before_a4[25] != old_lower:
                raise RuntimeError("Conflicting discharge-floor readback; no write sent")
            if (upper not in (80, 85, 90, 95, 100) or old_lower not in (1, 5, 10, 15, 20)
                    or not 5 <= reserve <= 100 or reserve % 5):
                raise RuntimeError("Invalid C1000 charge-limit baseline; no write sent")
            if not lower + 5 <= reserve <= upper:
                raise ValueError("Discharge floor would alter reserve or conflict with charge cap; no write sent")
            await connection.request(request)
            a4, d9, metrics = await self._fresh_c1000_settings(connection)
            if metrics["min_charge_percentage"] != lower:
                raise RuntimeError("Discharge floor not confirmed by telemetry; settings may have changed")
            expected_a4 = bytearray(before_a4)
            expected_a4[25] = lower  # Live A4 mirror of D9[5], including type04.
            for start, key in ((1, "ac_output_timeout_seconds"), (9, "dc_output_timeout_seconds")):
                if metrics[key] > before[key]:
                    raise RuntimeError("Output timer changed during setting confirmation")
                expected_a4[start:start + 4] = a4[start:start + 4]
            expected_d9 = bytearray(before_d9)
            expected_d9[5] = lower
            # The active tariff is runtime state and may change at a boundary.
            if (a4 != bytes(expected_a4) or d9[2:] != bytes(expected_d9[2:])
                    or any(metrics[key] != before[key] for key in (
                        "ac_output_enabled", "dc_output_enabled", "ac_input_connected"))):
                raise RuntimeError("Protected setting changed; settings may have changed")
            return self._tou_result(d9, metrics)

    async def _fresh_c1000_settings(self, connection) -> tuple[bytes, bytes, dict]:
        if self.config.model != Model.C1000_GEN2:
            raise ValueError("Setting requires C1000 Gen 2 only")
        reply = await connection.request(self.commands.status())
        a4, d9, metrics, _ = self._c1000_settings_report(reply)
        return a4, d9, metrics

    async def wireless_state(self) -> dict:
        """Read radio application flags; this neither enables nor pairs BLE."""
        request = self.commands.wireless_state()  # Model guard before device I/O.
        result = await self._radio_query(request, lambda reply: {
            "wireless_state": decode_native_wireless_state(reply)})
        result["scope"] = "Radio application state; physical advertising is not reported"
        return result

    async def wifi_rssi(self) -> dict:
        """Query radio AP-info; an unavailable observation returns null."""
        request = self.commands.wifi_rssi()  # Model guard before device I/O.
        # Invalidate the previous observation even if this query fails. A
        # failed confirmation must not leave an old success looking current.
        self.wifi_signal = None
        self.changed()
        result = await self._radio_query(request, lambda reply: {
            "wifi_rssi_dbm": decode_wifi_rssi(reply)})
        result["rssi_available"] = result["wifi_rssi_dbm"] is not None
        result["scope"] = "Radio AP-info query; unavailable RSSI is null"
        self.wifi_signal = {"schema_version": 1, "source": "radio_ap_info", "main_version": "1.1.4.9",
                            **{key: result[key] for key in ("radio_version", "settings_unchanged",
                                                          "observed_at", "wifi_rssi_dbm")}}
        self.changed()
        return result

    async def _radio_query(self, request: NativeMqttRequest,
                           decode: Callable[[bytes], dict]) -> dict:
        async with self._control_lock:
            connection = self.connection
            if connection is None:
                raise ConnectionError("Station is not connected")
            before_a4, before_d9, before = await self._fresh_c1000_settings(connection)
            if (before.get("software_version") != "1.1.4.9"
                    or before.get("software_version_module") != "0.3.3.0"):
                raise ValueError("Radio query requires main 1.1.4.9 / radio 0.3.3.0; no radio request sent")
            observation = decode(await connection.request(request))
            a4, d9, metrics = await self._fresh_c1000_settings(connection)
            expected = bytearray(before_a4)
            for key, offset in (("ac_output_timeout_seconds", 1), ("dc_output_timeout_seconds", 9)):
                if metrics[key] > before[key]:
                    raise RuntimeError("Output countdown increased during radio query")
                expected[offset:offset + 4] = a4[offset:offset + 4]
            expected[22] = a4[22]  # Runtime display activity can change naturally.
            if (a4 != bytes(expected) or d9[2:] != before_d9[2:]
                    or any(metrics[key] != before[key] for key in
                           ("ac_output_enabled", "dc_output_enabled", "ac_input_connected"))):
                raise RuntimeError("Protected setting or output changed during radio query")
            return {"model": self.config.model.value, "radio_version": "0.3.3.0",
                    **observation, "settings_unchanged": True, "observed_at": time.time()}

    def _c1000_settings_report(self, reply: bytes) -> tuple[bytes, bytes, dict, dict[int, bytes]]:
        if self.config.model != Model.C1000_GEN2:
            raise ValueError("Setting requires C1000 Gen 2 only")
        if not reply or reply[0] != 0:
            raise RuntimeError("Status request failed")
        metrics, fields = decode_telemetry(reply[1:], self.config.model)
        a4, d9 = fields.get(0xA4, b""), fields.get(0xD9, b"")
        if len(a4) != 34 or a4[0] != 4:
            raise RuntimeError("Missing complete C1000 settings baseline")
        periods_from_d9(d9)
        if any(len(fields.get(tag, b"")) < size or fields[tag][0] != 4
               for tag, size in ((0xA7, 5), (0xB2, 4))):
            raise RuntimeError("Missing valid C1000 output/settings baseline")
        boolean_fields = ("ac_output_enabled", "dc_output_enabled", "ac_input_connected",
                          "temperature_unit_fahrenheit", "ac_off_grid_alert_enabled", "ac_fast_charge_enabled",
                          "display_enabled")
        if any(type(metrics.get(key)) is not int or metrics[key] not in (0, 1)
               for key in boolean_fields):
            raise RuntimeError("Missing valid C1000 output/settings baseline")
        metrics.pop("serial_number", None)
        return a4, d9, metrics, fields

    async def set_clock_brightness(self, window: int, high: bool) -> dict:
        """Confirm a scalar selector while the clock is disabled and has no transfer.

        DA omits hidden theme state. Never include A2 or assets, or use 0092.
        Failed confirmation leaves an uncertain setting; do not automatically retry.
        """
        request = self.commands.clock_brightness(window, high)
        async with self._control_lock:
            connection = self._control_connection()

            async def fresh():
                a4, d9, metrics, fields = self._c1000_settings_report(
                    await connection.request(self.commands.status()))
                da = fields.get(0xDA, b"")
                if (len(da) != 24 or da[0] != 4 or metrics.get("software_version") != "1.1.4.9"
                        or da[1] & 0x80 or da[2] != 0 or da[18] not in (0, 1) or da[19] not in (0, 1)
                        or metrics["usage_mode"] != "standard" or metrics["active_tariff"] != "none"
                        or metrics["ac_output_timeout_seconds"] != 0 or metrics["dc_output_timeout_seconds"] != 0):
                    raise ValueError("Clock brightness requires main 1.1.4.9, complete inactive DA, Standard mode and no countdowns")
                return a4, d9, da, metrics

            before_a4, before_d9, before_da, before = await fresh()
            expected_da = bytearray(before_da)
            expected_da[17 + window] = int(high)
            await connection.request(request)
            for sample in range(2):
                if sample:
                    await asyncio.sleep(1)
                a4, d9, da, metrics = await fresh()
                expected_a4 = bytearray(before_a4)
                expected_a4[22] = a4[22]  # Runtime LCD activity may change.
                if (da != bytes(expected_da) or a4 != bytes(expected_a4) or d9[2:] != before_d9[2:]
                        or any(metrics[key] != before[key] for key in
                               ("ac_output_enabled", "dc_output_enabled", "ac_input_connected"))):
                    raise RuntimeError("Clock brightness or protected settings not confirmed; the setting may have changed")
            return self._tou_result(d9, metrics)

    async def _set_c1000_boolean(self, request: NativeMqttRequest, metric: str, value: bool) -> dict:
        if self.config.model != Model.C1000_GEN2 or type(value) is not bool:
            raise ValueError("Boolean setting requires C1000 Gen 2 and a boolean value")
        if metric not in ("temperature_unit_fahrenheit", "ac_off_grid_alert_enabled"):
            raise ValueError("Unsupported C1000 boolean setting")
        return await self._set_c1000_setting(request, metric, int(value))

    async def _set_c1000_setting(self, request: NativeMqttRequest, metric: str, value: int) -> dict:
        """Confirm one setting against fresh raw configuration and output states.

        No write retry or automatic restoration: a failed confirmation can leave
        changed settings. Inspect fresh status before deciding the next action.
        """
        if metric not in ("temperature_unit_fahrenheit", "ac_off_grid_alert_enabled", "device_timeout_minutes",
                          "ac_fast_charge_enabled", "ac_charging_power_limit_w", "display_brightness",
                          "display_timeout_seconds", "port_memory_enabled", "dc_power_saving_mode_enabled", "ac_power_saving_mode_enabled"):
            raise ValueError("Unsupported C1000 setting")

        async with self._control_lock:
            connection = self._control_connection()
            before_a4, before_d9, before = await self._fresh_c1000_settings(connection)
            if metric in ("dc_power_saving_mode_enabled", "ac_power_saving_mode_enabled") and (
                    before.get("software_version") != "1.1.4.9"
                    or before["ac_output_enabled" if metric.startswith("ac_") else "dc_output_enabled"] != 0
                    or type(before.get(metric)) is not int
                    or before.get(metric) not in (0, 1)
                    or before["ac_output_timeout_seconds"] != 0
                    or before["dc_output_timeout_seconds"] != 0):
                raise ValueError("C1000 Gen 2 Smart requires main 1.1.4.9, its output off and inactive AC/DC countdowns; no write sent")
            if metric == "display_brightness" and (
                    before["usage_mode"] != "standard" or before["active_tariff"] != "none"
                    or before.get("clock_screen_enabled") != 0
                    or before.get("clock_screen_transfer_status_raw") != 0):
                raise ValueError("Brightness requires Standard mode and an inactive clock screen; no write sent")
            if metric == "ac_fast_charge_enabled" and value and (
                    before["usage_mode"] != "standard" or before["active_tariff"] != "none"
                    or before["ac_input_connected"] != 1):
                raise ValueError("Fast charge requires Standard mode and connected mains; no write sent")
            await connection.request(request)
            protected = ("ac_output_enabled", "dc_output_enabled", "ac_input_connected")
            expected = bytearray(before_a4)
            if metric == "temperature_unit_fahrenheit":
                expected[20] = int(value)
            elif metric == "device_timeout_minutes":
                expected[14:16] = value.to_bytes(2, "little")
            elif metric == "ac_fast_charge_enabled":
                expected[21] = int(value)
            elif metric == "ac_charging_power_limit_w":
                expected[5:7] = value.to_bytes(2, "little")
            elif metric == "display_brightness":
                expected[18] = value
            elif metric == "display_timeout_seconds":
                expected[16:18] = value.to_bytes(2, "little")
            elif metric == "port_memory_enabled":
                expected[23] = value
            elif metric == "dc_power_saving_mode_enabled":
                expected[13] = value
            elif metric == "ac_power_saving_mode_enabled":
                expected[8] = value
            else:
                expected[32] = (expected[32] & ~2) | (int(value) << 1)
            for sample in range(2 if metric in ("ac_fast_charge_enabled", "dc_power_saving_mode_enabled", "ac_power_saving_mode_enabled") else 1):
                if sample:
                    # Observe a second fresh report after asynchronous policy work.
                    await asyncio.sleep(1)
                a4, d9, metrics = await self._fresh_c1000_settings(connection)
                if metrics[metric] != int(value):
                    raise RuntimeError("Setting not confirmed by telemetry; settings may have changed")
                if metric in ("ac_fast_charge_enabled", "ac_charging_power_limit_w", "display_brightness",
                              "display_timeout_seconds", "port_memory_enabled", "dc_power_saving_mode_enabled", "ac_power_saving_mode_enabled"):
                    # A4[22] is runtime display-timer activity. Fast-charge
                    # charge-power and display events wake it; expiry may clear
                    # it. The saved preferences remain protected independently.
                    expected[22] = a4[22]
                # Remaining timer seconds may decrease, but never increase.
                for start, key in ((1, "ac_output_timeout_seconds"), (9, "dc_output_timeout_seconds")):
                    if metrics[key] > before[key]:
                        raise RuntimeError("Output timer changed during setting confirmation")
                    expected[start:start + 4] = a4[start:start + 4]
                # A tariff boundary may change active tariff without changing the plan.
                if (a4 != bytes(expected) or d9[2:] != before_d9[2:]
                        or any(metrics[key] != before[key] for key in protected)):
                    raise RuntimeError("Protected setting changed; settings may have changed")
                if metric == "display_brightness" and any(
                        metrics.get(key) != value for key, value in before.items()
                        if key.startswith("clock_screen_")):
                    raise RuntimeError("Clock-screen configuration changed during brightness confirmation")
            return self._tou_result(d9, metrics)

    async def _fresh_tou(self, connection, *, timeout: float = 12) -> tuple[bytes, dict]:
        reply = await connection.request(self.commands.status(), timeout=timeout)
        if not reply or reply[0] != 0:
            raise RuntimeError("Status request failed")
        fields = parse_tlvs(reply[1:])
        d9 = fields.get(0xD9, b"")
        periods_from_d9(d9)  # Never treat a truncated/malformed plan as a baseline.
        metrics, _ = decode_telemetry(reply[1:], self.config.model)
        metrics.pop("serial_number", None)
        return d9, metrics

    @staticmethod
    def _tou_protected(before: dict, after: dict | None = None) -> None:
        required = ("ac_output_enabled", "ac_input_connected", "max_charge_percentage",
                    "min_charge_percentage", "ac_charging_power_limit_w", "ac_fast_charge_enabled")
        # C1000's existing decoder names this countdown differently.
        timer = ("ac_output_timer_remaining_seconds" if "ac_output_timer_remaining_seconds" in before
                 else "ac_output_timeout_seconds")
        required += (timer,)
        optional = ("dc_output_enabled", "dc_output_timer_remaining_seconds", "dc_output_timeout_seconds", "ac_power_saving_mode_enabled",
                    "dc_power_saving_mode_enabled", "device_timeout_minutes", "port_memory_enabled",
                    "display_timeout_seconds")
        if any(k not in before for k in required):
            raise RuntimeError("Missing fresh Time-of-Use baseline")
        if after is not None and any(after.get(k) != before[k] for k in (*required, *optional) if k in before):
            raise RuntimeError("Protected setting changed during Time-of-Use control")

    async def _tou_ready(self, connection, metrics: dict) -> None:
        self._tou_protected(metrics)
        if self.config.model == Model.C1000_GEN2:
            # Use the freshly decoded status, never the cached snapshot. Active
            # backup plans override tariff charging suppression and saved caps.
            disaster_active = metrics.get("disaster_preparation_active")
            if type(disaster_active) is not int or disaster_active != 0:
                raise ValueError(
                    "Time-of-Use activation requires fresh confirmation that disaster preparation is inactive")
        if metrics["ac_output_enabled"] != 1 or metrics["ac_input_connected"] != 1:
            raise RuntimeError("Time-of-Use activation requires enabled AC output and connected mains")
        if metrics["ac_fast_charge_enabled"] != 0:
            raise ValueError("Disable fast charge first; active tariffs can clear that setting")
        timer = metrics.get("ac_output_timer_remaining_seconds", metrics.get("ac_output_timeout_seconds"))
        if timer != 0:
            raise ValueError("Active AC-output timer prevents Time-of-Use activation")
        if not metrics["min_charge_percentage"] + 5 <= metrics["backup_reserve_percentage"] <= metrics["max_charge_percentage"]:
            raise ValueError("Current reserve is outside the upper/lower charge bounds")
        reply = await connection.request(self.commands.readiness())
        if not reply or reply[0] != 0 or parse_tlvs(reply[1:]).get(0xA1) != b"\x34":
            raise RuntimeError("Controller network readiness is not confirmed")

    def _tou_result(self, d9: bytes, metrics: dict) -> dict:
        result = self.snapshot()
        result.update(metrics=metrics, power_flow=power_flow(metrics),
                      tou_plan=[p.to_dict() for p in periods_from_d9(d9)], settings_confirmed=True)
        return result

    async def _write_tou(self, connection, periods, enabled, before) -> tuple[bytes, dict]:
        await connection.request(self.commands.tou_plan(periods, enabled=enabled))
        d9, metrics = await self._fresh_tou(connection)
        self._tou_protected(before, metrics)
        if (d9[2] != int(enabled) or periods_from_d9(d9) != tuple(periods)
                or d9[3] != before["backup_reserve_percentage"]):
            raise RuntimeError("Time-of-Use plan not confirmed by telemetry")
        if not enabled and d9[1] != 0:
            raise RuntimeError("Standard mode still reports an active tariff")
        return d9, metrics

    async def set_backup_reserve(self, percentage: int) -> dict:
        """Change reserve only, preserving outputs, limits, mode and schedule."""
        request = self.commands.backup_reserve(percentage)
        async with self._control_lock:
            connection = self._control_connection()
            before_d9, before = await self._fresh_tou(connection)
            self._tou_protected(before)
            if not before["min_charge_percentage"] + 5 <= percentage <= before["max_charge_percentage"]:
                raise ValueError("Reserve must be between the lower limit plus 5 and the upper charge limit")
            await connection.request(request)
            d9, metrics = await self._fresh_tou(connection)
            self._tou_protected(before, metrics)
            if (d9[3] != percentage or d9[2] != before_d9[2]
                    or periods_from_d9(d9) != periods_from_d9(before_d9)):
                raise RuntimeError("Reserve or preserved schedule not confirmed by telemetry")
            return self._tou_result(d9, metrics)

    async def set_tou_plan(self, periods=(), *, enabled: bool = False) -> dict:
        """Store in Standard first; explicit activation may persist until cleared.

        This replaces the old plan. A settings confirmation is not a power-flow
        confirmation. Use return_to_grid() to clear a plan and verify grid input.
        """
        periods = validate_periods(periods)
        self.commands.tou_plan(periods, enabled=enabled)  # Validate before I/O.
        all_day = len(periods) == 1 and periods[0].start_hour == 0 and periods[0].end_hour == 24
        if (enabled and not all_day and self.config.model == Model.C1000_GEN2
                and timezone_confer(self.config.timezone_name)[0] == bytes(4)):
            raise ValueError(
                "C1000 UTC synchronization can retain an old timezone offset; "
                "use a nonzero-offset local timezone and verify its clock before activating hourly plans")
        async with self._control_lock:
            connection = self._control_connection()
            before_d9, before = await self._fresh_tou(connection)
            self._tou_protected(before)
            if before_d9[2] not in (0, 1):
                raise ValueError("Only Standard and Time-of-Use baselines are supported")
            if enabled:
                await self._tou_ready(connection, before)
            attempted = False
            try:
                attempted = True
                d9, metrics = await self._write_tou(connection, periods, False, before)
                if enabled:
                    d9, metrics = await self._write_tou(connection, periods, True, before)
                return self._tou_result(d9, metrics)
            except BaseException:
                if attempted:
                    try:
                        await connection.request(self.commands.tou_plan(periods_from_d9(before_d9), enabled=bool(before_d9[2])))
                        await self._fresh_tou(connection)
                    except (OSError, RuntimeError, ValueError, TimeoutError):
                        self.record("tou_restore_unconfirmed")
                raise

    async def _wait_grid(self, connection, before: dict, timeout: int) -> tuple[bytes, dict]:
        deadline = asyncio.get_running_loop().time() + timeout
        consecutive = 0
        while asyncio.get_running_loop().time() < deadline:
            remaining = deadline - asyncio.get_running_loop().time()
            try:
                d9, metrics = await self._fresh_tou(connection, timeout=min(12, max(0.01, remaining)))
            except TimeoutError:
                break
            self._tou_protected(before, metrics)
            if d9[3] != before["backup_reserve_percentage"]:
                raise RuntimeError("Reserve changed during grid-return confirmation")
            consecutive = consecutive + 1 if power_flow(metrics) == "grid" else 0
            self.record("grid_return_sample", power_flow=power_flow(metrics),
                        metrics={k: metrics.get(k) for k in ("battery_percentage", "battery_status",
                                 "ac_input_power_w", "ac_output_power_w", "ac_output_enabled",
                                 "usage_mode", "active_tariff", "backup_reserve_percentage")})
            if consecutive >= 3:
                return d9, metrics
            await asyncio.sleep(min(2, max(0, deadline - asyncio.get_running_loop().time())))
        raise PowerFlowTimeout(self.snapshot())

    async def return_to_grid(self, *, timeout: int = 30) -> dict:
        """Replace a plan with Standard; confirm power flow, preserving reserve.

        If grid is not supplying the load, select the all-day tariff3 path first.
        No output-switch or charge-limit command is included. Timeout describes
        each confirmation phase, not the total transport time. Failure can leave
        changed settings; inspect fresh status. Zero load cannot confirm flow.
        """
        if type(timeout) is not int or not 5 <= timeout <= 120:
            raise ValueError("Grid confirmation timeout must be 5–120 seconds")
        async with self._control_lock:
            connection = self._control_connection()
            _, before = await self._fresh_tou(connection)
            self._tou_protected(before)
            failure = None
            try:
                if power_flow(before) != "grid":
                    await self._tou_ready(connection, before)
                    await self._write_tou(connection, (TouPeriod("off_peak", 0, 24),), True, before)
                    await self._wait_grid(connection, before, timeout)
            except BaseException as error:
                failure = error
            finally:
                # Clear even when the first confirmation fails. Do not lower
                # reserve or report success solely from an ACK/Standard setting.
                try:
                    await self._write_tou(connection, (), False, before)
                except (OSError, RuntimeError, ValueError, TimeoutError):
                    self.record("grid_return_clear_unconfirmed")
                    if failure is None:
                        raise
            if failure is not None:
                if isinstance(failure, PowerFlowTimeout):
                    raise PowerFlowTimeout(self.snapshot()) from None
                raise failure
            d9, metrics = await self._wait_grid(connection, before, timeout)
            result = self._tou_result(d9, metrics)
            result["grid_power_confirmed"] = True
            return result


class _Connection:
    def __init__(self, server: LocalMqttServer, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.server, self.reader, self.writer = server, reader, writer
        self.connected = False
        self.subscribed = False
        self.pending: tuple[str, asyncio.Future] | None = None
        self._response_aliases: tuple[str, ...] = ()
        self._response_pattern = DATA_RESPONSE
        self._original_ack: tuple[str, bytes | None] | None = None
        self.lock = asyncio.Lock()
        self.poller: asyncio.Task | None = None
        self._command_ready_at = 0.0

    async def send(self, first: int, body: bytes) -> None:
        self.writer.write(mqtt_packet(first, body))
        await self.writer.drain()

    async def close(self) -> None:
        if self.poller and self.poller is not asyncio.current_task():
            self.poller.cancel()
            await asyncio.gather(self.poller, return_exceptions=True)
        if self.pending and not self.pending[1].done():
            self.pending[1].set_exception(ConnectionError("Station disconnected"))
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except OSError:
            pass

    async def request(self, request: NativeMqttRequest, timeout: float = 12) -> bytes:
        if request.response_pattern not in (DATA_RESPONSE, RADIO_NATIVE_PATTERN):
            raise ValueError("Unsupported native response namespace")
        if request.response_pattern == RADIO_NATIVE_PATTERN and (
                self.server.config.model != Model.C1000_GEN2 or request.response_command not in RADIO_QUERY_RESPONSES
                or request.response_aliases):
            raise ValueError("Only verified C1000 Gen 2 radio queries are supported")
        async with self.lock:
            if self.writer.is_closing() or not self.subscribed:
                raise ConnectionError("Station command subscription is unavailable")
            # C1000 can publish before startup/provisioning has settled. An
            # immediate status request was lost live; delayed requests worked.
            delay = self._command_ready_at - asyncio.get_running_loop().time()
            if delay > 0:
                await asyncio.sleep(delay)
                if self.writer.is_closing() or not self.subscribed:
                    raise ConnectionError("Station disconnected during startup")
            future = asyncio.get_running_loop().create_future()
            self.pending = (request.response_command, future)
            self._response_aliases = request.response_aliases
            self._response_pattern = request.response_pattern
            topic = request.topic.encode()
            try:
                self.server.record("request", payload=request.payload)
                await self.send(0x30, len(topic).to_bytes(2, "big") + topic + request.payload.encode())
                response = await asyncio.wait_for(future, timeout)
                if not response or response[0] != 0:
                    raise RuntimeError("Station rejected native request")
                return response
            except (TimeoutError, asyncio.CancelledError):
                self.writer.close()
                raise
            finally:
                self.pending = None
                self._response_aliases = ()
                self._response_pattern = DATA_RESPONSE

    async def send_original_setting(self, request: NativeMqttRequest) -> None:
        """Send one verified original setter, accepting an optional ACK only."""
        if (self.server.config.model != Model.C1000 or request.response_aliases
                or request.response_command not in ("0844", "0845", "0846", "084c", "084f", "0850", "085e", "0876", "0877")):
            raise ValueError("Unsupported original C1000 native setting")
        async with self.lock:
            if self.writer.is_closing() or not self.subscribed:
                raise ConnectionError("Station command subscription is unavailable")
            if self._original_ack is not None:
                raise RuntimeError("Original setting confirmation is already active")
            self._original_ack = (request.response_command, None)
            topic = request.topic.encode()
            self.server.record("request", payload=request.payload)
            try:
                await self.send(0x30, len(topic).to_bytes(2, "big") + topic + request.payload.encode())
            except (asyncio.CancelledError, OSError):
                self.writer.close()
                raise

    def check_original_setting_response(self) -> None:
        if self._original_ack is not None:
            response = self._original_ack[1]
            if response is not None and (not response or response[0] != 0):
                raise RuntimeError("Station rejected original native setting")

    def finish_original_setting(self) -> None:
        self._original_ack = None

    async def poll(self) -> None:
        try:
            while not self.writer.is_closing():
                await self.request(self.server.commands.status())
                await asyncio.sleep(5)
        except (OSError, TimeoutError, RuntimeError):
            self.writer.close()

    async def run(self) -> None:
        while not self.writer.is_closing():
            first, body = await asyncio.wait_for(read_mqtt(self.reader), timeout=120)
            kind, flags = first >> 4, first & 15
            self.server.record("packet", first=first, body_hex=body.hex())
            if kind == 1 and not self.connected and flags == 0:
                protocol, pos = mqtt_string(body, 0)
                if protocol != "MQTT" or pos + 4 > len(body) or body[pos] != 4:
                    raise ValueError("Only MQTT 3.1.1 is supported")
                connect_flags = body[pos + 1]
                if connect_flags & 1 or (connect_flags & 0x18) == 0x18 or (not connect_flags & 4 and connect_flags & 0x38) or (connect_flags & 0x40 and not connect_flags & 0x80):
                    raise ValueError("Invalid MQTT CONNECT flags")
                _client_id, end = mqtt_string(body, pos + 4, allow_zero_client_id=(
                    self.server.config.model in (Model.C1000, Model.C1000_GEN2) and bool(connect_flags & 2)))
                if not _client_id and body[pos + 4:pos + 6] == b"\x00\x11":
                    self.server.record("zero_filled_client_id", length=17)
                if connect_flags & 4:
                    _, end = mqtt_string(body, end)
                    # Will payload is binary; validate its length without UTF-8 decoding.
                    size = int.from_bytes(body[end:end + 2], "big")
                    end += 2 + size
                if connect_flags & 0x80:
                    _, end = mqtt_string(body, end)
                if connect_flags & 0x40:
                    size = int.from_bytes(body[end:end + 2], "big")
                    end += 2 + size
                if end != len(body):
                    raise ValueError("Malformed MQTT CONNECT")
                self.connected = True
                await self.send(0x20, b"\x00\x00")
            elif not self.connected:
                raise ValueError("MQTT CONNECT is required")
            elif kind == 8 and flags == 2:
                if len(body) < 5 or body[:2] == b"\x00\x00":
                    raise ValueError("Invalid MQTT subscription")
                pos, grants = 2, bytearray()
                allowed = False
                while pos < len(body):
                    topic, pos = mqtt_string(body, pos)
                    if pos >= len(body) or body[pos] > 2:
                        raise ValueError("Invalid subscription QoS")
                    # Accept only the one station request topic; no wildcard subscriptions.
                    accepted = topic == self.server.topic and body[pos] <= 1
                    grants.append(body[pos] if accepted else 0x80)
                    allowed |= accepted
                    pos += 1
                await self.send(0x90, body[:2] + grants)
                if allowed and not self.subscribed:
                    old = self.server.connection
                    if old and old is not self:
                        await old.close()
                    self.subscribed = True
                    self._command_ready_at = asyncio.get_running_loop().time() + (
                        15 if self.server.config.model == Model.C1000_GEN2 else 0)
                    self.server.connection = self
                    self.server.last_seen = None
                    self.server.tou_plan_readback = None
                    self.server.error = None
                    self.server.changed()
                    self.poller = asyncio.create_task(self.poll())
            elif kind == 3:
                qos = (flags >> 1) & 3
                if qos > 1:
                    raise ValueError("Only MQTT QoS 0/1 is supported")
                topic, pos = mqtt_string(body, 0)
                if qos:
                    if pos + 2 > len(body) or body[pos:pos + 2] == b"\x00\x00":
                        raise ValueError("Invalid MQTT packet ID")
                    await self.send(0x40, body[pos:pos + 2])
                    pos += 2
                # Retained messages and another device cannot establish freshness or ACK a command.
                parts = topic.split("/")
                if flags & 1 or not self.subscribed or len(parts) < 4 or parts[1:4] != ["anker_power", self.server.config.product, self.server.config.device_serial]:
                    continue
                message = body[pos:]
                try:
                    frame = native_response(message, self.server.config,
                        radio_query=bool(self.pending and self.pending[0] in RADIO_QUERY_RESPONSES
                                         and self._response_pattern == RADIO_NATIVE_PATTERN))
                    if frame is None:
                        continue
                    update = decode_mqtt_telemetry(message, model=self.server.config.model,
                                                   expected_serial=self.server.config.device_serial)
                    if update:
                        self.server.metrics = {k: v for k, v in update.metrics.items() if k != "serial_number"}
                        self.server.last_seen = time.time()
                        if 0xD9 in update.raw_tlvs:
                            self.server.tou_plan_readback = plan_from_d9(
                                update.raw_tlvs[0xD9], self.server.config.model, self.server.last_seen)
                        self.server.error = None
                        self.server.changed()
                    command = frame.command.hex()
                    if self._original_ack and command == self._original_ack[0]:
                        previous = self._original_ack[1]
                        # A duplicate success must not erase an explicit
                        # rejection received during this confirmation window.
                        if previous is None or (previous and previous[0] == 0):
                            self._original_ack = (command, frame.payload)
                    if (self.pending and command == self.pending[0]
                            and frame.pattern == self._response_pattern and not self.pending[1].done()):
                        self.pending[1].set_result(frame.payload)
                    elif (self.server.config.model == Model.C1000 and update is not None
                          and command == "0405" and command in self._response_aliases and self.pending
                          and self.pending[0] == "0840" and not self.pending[1].done()):
                        # The connected original controller defers 0040 into a
                        # full 0405 report. Normalize it to the success-prefixed
                        # direct reply shape, after identity/retained checks and
                        # telemetry decoding. It cannot acknowledge a write.
                        try:
                            _original_settings(frame.payload)
                        except RuntimeError:
                            self.server.record("incomplete_original_status")
                        else:
                            self.pending[1].set_result(b"\x00" + frame.payload)
                except ValueError:
                    self.server.record("decode_error")
            elif kind == 12 and flags == 0 and not body:
                await self.send(0xD0, b"")
            elif kind == 14 and flags == 0 and not body:
                return
            else:
                raise ValueError("Unsupported MQTT packet")
