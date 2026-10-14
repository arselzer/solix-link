"""Synthetic recovery candidate and private RPC guards; no station access."""

import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from solix_link.ap_service import APService
from solix_link.c2000_power_recovery import recovery_output
from solix_link.commands import native_commands_for_model, validate_command
from solix_link.protocol import Model, tlv
from test_c2000_screen_timeout import C2000Station
from test_native_boolean_settings import service, unpack


class RecoveryStation(C2000Station):
    async def request(self, request):
        command, fields = unpack(request)
        if command == "0101":
            self.requests.append((command, fields))
            if not self.ignore:
                self.a7[1] = fields[0xA2][1]
                self.a7[2:4] = (45 if self.a7[1] else 0).to_bytes(2, "little")
            self.mutate()
            if self.fail_write:
                raise TimeoutError("Synthetic lost ACK")
            return b"\0"
        reply = await super().request(request)
        return reply + tlv(0xA5, bytes((4, 25, 0, 100, 100)))


def make(tmp_path, **kwargs):
    server, _ = service(tmp_path, model=Model.C2000_GEN2, **kwargs)
    station = RecoveryStation(server)
    server.connection = station
    return server, station


def test_on_probe_and_off_on_restore_touch_only_the_explicit_output(tmp_path):
    async def run():
        server, station = make(tmp_path)
        baseline = bytes(station.a4), bytes(station.d9)
        for enabled in (True, False, True):
            result = await recovery_output(server, enabled, acknowledge_power_interruption=not enabled)
            assert result["metrics"]["ac_output_enabled"] == int(enabled)
            assert result["settings_confirmed"] and not result["electrical_behavior_verified"]
        assert (bytes(station.a4), bytes(station.d9)) == baseline
        writes = [fields for command, fields in station.requests if command == "0101"]
        assert len(writes) == 3
        assert all(set(fields) == {0xA1, 0xA2, 0xFD} for fields in writes)
    asyncio.run(run())


@pytest.mark.parametrize("problem", ["no_ack", "bad_boolean", "wrong_model", "read_only", "wrong_firmware",
    "ac_timer", "dc_timer", "missing_a4", "missing_d9", "low_soc", "tou", "mains_off", "dc_on",
    "bad_ac_type", "bad_dc_type", "short_ac"])
def test_bad_off_baselines_cannot_send_an_output_write(tmp_path, problem):
    server, station = make(tmp_path, allow_control=problem != "read_only")
    enabled, ack = False, True
    if problem == "no_ack": ack = False
    if problem == "bad_boolean": enabled = 0
    if problem == "wrong_model": server.config = replace(server.config, model=Model.C1000_GEN2)
    if problem == "wrong_firmware": station.version = bytes((5, 6, 1, 2))
    if problem == "ac_timer": station.a4[1] = 1
    if problem == "dc_timer": station.a4[9] = 1
    if problem == "missing_a4": station.missing.add(0xA4)
    if problem == "missing_d9": station.missing.add(0xD9)
    if problem == "tou": station.d9[2] = 1
    if problem == "mains_off": station.a7[4] = 0
    if problem == "dc_on": station.b2[1] = 1
    if problem == "bad_ac_type": station.a7[0] = 1
    if problem == "bad_dc_type": station.b2[0] = 1
    if problem == "short_ac": station.a7 = station.a7[:5]
    if problem == "low_soc":
        original = station.request
        async def low(request):
            reply = await original(request)
            if unpack(request)[0] == "0100": reply = reply[:-7] + tlv(0xA5, bytes((4, 25, 0, 10, 100)))
            return reply
        station.request = low
    with pytest.raises((ValueError, PermissionError)):
        asyncio.run(recovery_output(server, enabled, acknowledge_power_interruption=ack))
    assert not any(command == "0101" for command, _ in station.requests)


@pytest.mark.parametrize("problem", ["ignored", "lost_ack", "charging_changed", "dc_changed", "backup_changed"])
def test_failed_confirmation_is_uncertain_and_never_repeats_off(tmp_path, problem):
    server, station = make(tmp_path)
    station.ignore = problem == "ignored"
    station.fail_write = problem == "lost_ack"
    def mutate():
        if problem == "charging_changed": station.a4[5] ^= 1
        if problem == "dc_changed": station.b2[1] = 1
        if problem == "backup_changed": station.d9[20] ^= 1
    station.mutate = mutate
    with pytest.raises((RuntimeError, TimeoutError)):
        asyncio.run(recovery_output(server, False, acknowledge_power_interruption=True))
    assert sum(command == "0101" for command, _ in station.requests) == 1


def test_recovery_never_becomes_an_http_or_ha_capability():
    assert "recovery-ac-output" not in native_commands_for_model(Model.C2000_GEN2)
    with pytest.raises(ValueError):
        validate_command("recovery-ac-output", {"enabled": False, "acknowledge_power_interruption": True})


@pytest.mark.parametrize("extra", [{}, {"unexpected": True}])
def test_private_socket_accepts_only_exact_maintenance_fields(monkeypatch, extra):
    from solix_link import c2000_power_recovery
    method = AsyncMock(return_value={"settings_confirmed": True})
    monkeypatch.setattr(c2000_power_recovery, "recovery_output", method)
    async def run():
        mqtt = SimpleNamespace(config=SimpleNamespace(model=Model.C2000_GEN2))
        worker = object.__new__(APService)
        worker._clients = set()
        worker.config = SimpleNamespace(name="test")
        worker.stations = {"test": mqtt}
        reader = asyncio.StreamReader()
        reader.feed_data(json.dumps({"command": "recovery-ac-output", "enabled": True,
            "acknowledge_power_interruption": False, **extra}).encode()+b"\n")
        class Writer:
            data = b""
            def write(self, value): self.data += value
            async def drain(self): pass
            def close(self): pass
        writer = Writer()
        await worker._control(reader, writer)
        assert json.loads(writer.data)["ok"] is (not extra)
        if extra: method.assert_not_awaited()
        else: method.assert_awaited_once_with(mqtt, True, acknowledge_power_interruption=False)
    asyncio.run(run())
