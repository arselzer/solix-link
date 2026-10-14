"""Power gates, uncertain OFF restoration and client cancellation, all synthetic."""

import asyncio
from dataclasses import replace
import json
from unittest.mock import AsyncMock

import pytest

from solix_link.ac_output_permission import ACOutputPermission, POWER_COMMANDS
from solix_link.c2000_power_recovery import resume_restore
from solix_link.commands import commands_for_transport, validate_command
from solix_link.protocol import Model
from test_c2000_power_recovery import make
from test_native_boolean_settings import service


def test_default_locked_and_permission_changes_send_no_station_command(tmp_path):
    async def run():
        server, station = service(tmp_path, model=Model.C2000_GEN2)
        assert not server.snapshot()["ac_power_control"]["enabled"]
        with pytest.raises(PermissionError):
            await server.set_ac_output_enabled(False, acknowledge_power_interruption=True)
        with pytest.raises(PermissionError):
            await server.configure_ac_output_permission(True, acknowledge_high_impact=False)
        await server.configure_ac_output_permission(True, acknowledge_high_impact=True)
        assert server.snapshot()["ac_power_control"]["enabled"]
        await server.configure_ac_output_permission(False, acknowledge_high_impact=False)
        assert not server.snapshot()["ac_power_control"]["enabled"]
        assert station.requests == []
    asyncio.run(run())


@pytest.mark.parametrize("problem", ["identity", "public_mode", "symlink", "malformed", "duplicate", "bool_version"])
def test_invalid_saved_permission_fails_closed_without_overwriting_evidence(tmp_path, problem):
    server, _ = make(tmp_path)
    path = server.ac_output_permission.path
    if problem == "identity":
        other = ACOutputPermission(replace(server.config, device_serial="A1783SYNTHETIC002"), tmp_path)
        assert not other.snapshot()["enabled"] and not other.snapshot()["storage_valid"]
        with pytest.raises(ValueError): other.configure(True, acknowledge_high_impact=True)
        return
    if problem == "public_mode": path.chmod(0o644)
    if problem == "symlink":
        target = tmp_path / "synthetic-other"
        path.rename(target)
        path.symlink_to(target)
    if problem == "malformed": path.write_text("invalid")
    if problem == "duplicate": path.write_text('{"enabled":true,"enabled":false}')
    if problem == "bool_version":
        body = json.loads(path.read_text())
        body["schema_version"] = True
        path.write_text(json.dumps(body))
    before = path.read_bytes()
    assert server.ac_output_permission.snapshot()["storage_valid"] is False
    assert server.ac_output_permission.snapshot()["enabled"] is False
    with pytest.raises(ValueError): server.ac_output_permission.configure(True, acknowledge_high_impact=True)
    assert path.read_bytes() == before


@pytest.mark.parametrize("fields", [
    {"off_seconds": 0, "acknowledge_power_interruption": True, "controller_independent": True},
    {"off_seconds": 61, "acknowledge_power_interruption": True, "controller_independent": True},
    {"off_seconds": True, "acknowledge_power_interruption": True, "controller_independent": True},
    {"off_seconds": 20, "acknowledge_power_interruption": False, "controller_independent": True},
    {"off_seconds": 20, "acknowledge_power_interruption": True, "controller_independent": False},
])
def test_bad_cycle_fields_are_rejected_before_io(tmp_path, fields):
    server, station = make(tmp_path)
    with pytest.raises((ValueError, PermissionError)):
        asyncio.run(server.cycle_ac_output(**fields))
    assert station.requests == [] and not server.ac_output_permission.pending()


def test_cancelled_client_and_disabled_gate_cannot_cancel_worker_on_restoration(tmp_path, monkeypatch):
    async def run():
        server, station = make(tmp_path)
        holding, release = asyncio.Event(), asyncio.Event()
        async def sleep(seconds):
            if seconds == 20:
                assert station.a7[1] == 0 and server.ac_output_permission.pending()
                holding.set()
                await release.wait()
        monkeypatch.setattr("solix_link.c2000_power_recovery.asyncio.sleep", sleep)
        client = asyncio.create_task(server.cycle_ac_output(20,
            acknowledge_power_interruption=True, controller_independent=True))
        await asyncio.wait_for(holding.wait(), 2)
        client.cancel()
        with pytest.raises(asyncio.CancelledError): await client
        await server.configure_ac_output_permission(False, acknowledge_high_impact=False)
        assert not server._ac_power_task.cancelled()
        release.set()
        result = await asyncio.wait_for(server._ac_power_task, 2)
        assert result["metrics"]["ac_output_enabled"] == 1
        assert not server.ac_output_permission.pending()
        assert not server.ac_output_permission.enabled()
        assert [fields[0xA2][1] for command, fields in station.requests if command == "0101"] == [0, 1]
    asyncio.run(run())


def test_lost_off_ack_never_retries_off_and_still_restores_on(tmp_path, monkeypatch):
    async def run():
        server, station = make(tmp_path)
        original = station.request
        from test_native_boolean_settings import unpack
        async def request(packet):
            command, fields = unpack(packet)
            station.fail_write = command == "0101" and fields[0xA2][1] == 0
            return await original(packet)
        station.request = request
        monkeypatch.setattr("solix_link.c2000_power_recovery.asyncio.sleep", AsyncMock())
        with pytest.raises(TimeoutError):
            await server.cycle_ac_output(20, acknowledge_power_interruption=True, controller_independent=True)
        assert station.a7[1] == 1 and not server.ac_output_permission.pending()
        assert [fields[0xA2][1] for command, fields in station.requests if command == "0101"] == [0, 1]
    asyncio.run(run())


def test_failed_journal_write_prevents_off(tmp_path, monkeypatch):
    server, station = make(tmp_path)
    def fail(): raise OSError("Synthetic storage failure")
    monkeypatch.setattr(server.ac_output_permission, "arm", fail)
    with pytest.raises(OSError):
        asyncio.run(server.cycle_ac_output(20, acknowledge_power_interruption=True, controller_independent=True))
    assert not any(command == "0101" for command, _ in station.requests)


@pytest.mark.parametrize("problem", ["dc_on", "timer"])
def test_unsafe_cycle_baseline_cannot_arm_or_write(tmp_path, problem):
    server, station = make(tmp_path)
    if problem == "dc_on": station.b2[1] = 1
    if problem == "timer": station.a4[1] = 1
    with pytest.raises(ValueError):
        asyncio.run(server.cycle_ac_output(20, acknowledge_power_interruption=True, controller_independent=True))
    assert not server.ac_output_permission.pending()
    assert not any(command == "0101" for command, _ in station.requests)


def test_saved_restore_intent_survives_restart_with_permission_locked(tmp_path, monkeypatch):
    async def run():
        original, _ = make(tmp_path)
        original.ac_output_permission.arm()
        original.ac_output_permission.configure(False, acknowledge_high_impact=False)
        restarted, station = service(tmp_path, model=Model.C2000_GEN2)
        from test_c2000_power_recovery import RecoveryStation
        station = RecoveryStation(restarted)
        station.a7[1] = 0
        restarted.connection = station
        monkeypatch.setattr("solix_link.c2000_power_recovery.asyncio.sleep", AsyncMock())
        result = await resume_restore(restarted)
        assert result["metrics"]["ac_output_enabled"] == 1 and not restarted.ac_output_permission.pending()
        assert not restarted.ac_output_permission.enabled()
        assert [fields[0xA2][1] for command, fields in station.requests if command == "0101"] == [1]
    asyncio.run(run())


def test_uncertain_on_keeps_intent_and_retries_only_on(tmp_path, monkeypatch):
    async def run():
        server, station = make(tmp_path)
        server.ac_output_permission.arm()
        station.a7[1] = 0
        station.ignore = True
        monkeypatch.setattr("solix_link.c2000_power_recovery.asyncio.sleep", AsyncMock())
        with pytest.raises(RuntimeError): await resume_restore(server)
        assert server.ac_output_permission.pending()
        assert [fields[0xA2][1] for command, fields in station.requests if command == "0101"] == [1, 1, 1]
    asyncio.run(run())


def test_reconnect_resumes_saved_on_once_without_enabling_permission(tmp_path, monkeypatch):
    async def run():
        server, station = make(tmp_path)
        server.ac_output_permission.arm()
        server.ac_output_permission.configure(False, acknowledge_high_impact=False)
        station.a7[1] = 0
        monkeypatch.setattr("solix_link.c2000_power_recovery.asyncio.sleep", AsyncMock())
        server.changed()
        await asyncio.wait_for(server._ac_power_task, 2)
        server.changed()
        assert station.a7[1] == 1 and not server.ac_output_permission.pending()
        assert not server.ac_output_permission.enabled()
        assert [fields[0xA2][1] for command, fields in station.requests if command == "0101"] == [1]
    asyncio.run(run())


def test_shutdown_restores_before_closing_live_connection(tmp_path, monkeypatch):
    async def run():
        server, station = make(tmp_path)
        holding = asyncio.Event()
        async def sleep(seconds):
            if seconds == 20:
                holding.set()
                await asyncio.Event().wait()
        async def close():
            assert station.a7[1] == 1 and not server.ac_output_permission.pending()
        station.close = close
        server._clients.add(station)
        monkeypatch.setattr("solix_link.c2000_power_recovery.asyncio.sleep", sleep)
        client = asyncio.create_task(server.cycle_ac_output(20,
            acknowledge_power_interruption=True, controller_independent=True))
        await asyncio.wait_for(holding.wait(), 2)
        await asyncio.wait_for(server.stop(), 2)
        with pytest.raises(asyncio.CancelledError): await client
        assert station.a7[1] == 1 and server.connection is None
        assert [fields[0xA2][1] for command, fields in station.requests if command == "0101"] == [0, 1]
    asyncio.run(run())


def test_public_power_commands_are_native_c2000_only_and_strictly_acknowledged():
    for model in Model:
        for transport in ("prime", "legacy", "native_mqtt"):
            supported = set(commands_for_transport(model, transport)) & POWER_COMMANDS
            assert supported == (POWER_COMMANDS if model == Model.C2000_GEN2 and transport == "native_mqtt" else set())
    with pytest.raises(PermissionError):
        validate_command("set-ac-output", {"enabled": False, "acknowledge_power_interruption": False})
