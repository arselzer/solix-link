"""Guarded AC Smart and local-only output prep on synthetic C1000 Gen 2."""
import asyncio
import pytest

from solix_link.commands import native_commands_for_model, validate_command
from solix_link.protocol import Model
from test_gen2_native_dc_smart import DcStation
from test_native_boolean_settings import service, unpack


class AcStation(DcStation):
    async def request(self, request):
        command, fields = unpack(request)
        if command == "0101":
            self.requests.append((command, fields))
            if not self.ignore:
                if 0xA6 in fields: self.a4[8] = fields[0xA6][1]
                else: self.a7[1] = fields[0xA2][1]
            self.mutate()
            if self.fail_write: raise TimeoutError("Synthetic lost ACK")
            return b"\0"
        return await super().request(request)


def setup(tmp_path, **options):
    server, _ = service(tmp_path, **options)
    server.connection = station = AcStation(server)
    return server, station


def test_ac_off_smart_roundtrip_output_restoration(tmp_path):
    async def run():
        server, station = setup(tmp_path)
        before = bytes(station.a4), bytes(station.d9), bytes(station.a7), bytes(station.b2)
        with pytest.raises(ValueError): await server.set_ac_power_saving_enabled(True)
        assert all(r[0] == "0100" for r in station.requests)
        await server.set_ac_output_enabled(False)
        await server.set_ac_power_saving_enabled(True)
        await server.set_ac_power_saving_enabled(False)
        await server.set_ac_output_enabled(True)
        assert (bytes(station.a4), bytes(station.d9), bytes(station.a7), bytes(station.b2)) == before
        writes = [fields for command, fields in station.requests if command == "0101"]
        assert [set(f) for f in writes] == [{0xA1, 0xA2, 0xFD}, {0xA1, 0xA6, 0xFD}, {0xA1, 0xA6, 0xFD}, {0xA1, 0xA2, 0xFD}]
    asyncio.run(run())


@pytest.mark.parametrize("issue", ["ac_on", "ac_timer", "dc_timer", "flag", "firmware"])
def test_ac_smart_unknown_or_unsafe_sends_no_write(tmp_path, issue):
    server, station = setup(tmp_path); station.a7[1] = 0
    if issue == "ac_on": station.a7[1] = 1
    elif issue == "ac_timer": station.a4[1] = 1
    elif issue == "dc_timer": station.a4[9] = 1
    elif issue == "flag": station.a4[8] = 2
    elif issue == "firmware": station.version = b"\4" + bytes((10, 4, 1, 1)) + bytes(24)
    with pytest.raises(ValueError): asyncio.run(server.set_ac_power_saving_enabled(True))
    assert all(r[0] == "0100" for r in station.requests)


@pytest.mark.parametrize("target,index", [("a4", 18), ("d9", 3), ("a7", 1), ("b2", 1)])
def test_protected_mutations_fail_without_retry(tmp_path, target, index):
    server, station = setup(tmp_path); station.a7[1] = 0
    station.mutate = lambda: getattr(station,target).__setitem__(index,getattr(station,target)[index]^1)
    with pytest.raises((RuntimeError, ValueError)): asyncio.run(server.set_ac_power_saving_enabled(True))
    assert len([r for r in station.requests if r[0] == "0101"]) == 1


def test_c2000_requires_separate_permission_and_c1000_output_stays_private(tmp_path):
    for model in (Model.C1000_GEN2, Model.C1000):
        assert "set-ac-output" not in native_commands_for_model(model)
    assert "set-ac-output" in native_commands_for_model(Model.C2000_GEN2)
    with pytest.raises(ValueError): validate_command("set-ac-output", {"enabled":False})
    server, station = setup(tmp_path,model=Model.C2000_GEN2)
    with pytest.raises(PermissionError): asyncio.run(server.set_ac_output_enabled(False))
    assert not station.requests
    server, station = setup(tmp_path,allow_control=False)
    with pytest.raises(PermissionError): asyncio.run(server.set_ac_output_enabled(False))
    assert not station.requests


def test_failed_output_ack_does_not_retry(tmp_path):
    server, station = setup(tmp_path);station.fail_write=True
    with pytest.raises(TimeoutError): asyncio.run(server.set_ac_output_enabled(False))
    assert station.a7[1] == 0
    assert [r[0] for r in station.requests] == ["0100", "0101"]
