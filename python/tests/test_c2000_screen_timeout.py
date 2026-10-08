"""Synthetic C2000 native screen writes; no physical native replay claimed."""

import asyncio
import time

import pytest

from solix_link.protocol import Model, decode_telemetry, tlv
from test_native_display_preferences import PreferenceStation
from test_native_boolean_settings import service, unpack


class C2000Station(PreferenceStation):
    version = bytes((4, 6, 1, 2))

    async def request(self, request):
        reply = await super().request(request)
        if unpack(request)[0] == "0100":
            reply += tlv(0xF9, self.version)
            self.server.metrics, _ = decode_telemetry(reply[1:], Model.C2000_GEN2)
            self.server.last_seen = time.time()
        return reply


@pytest.mark.parametrize("problem", ["none", "wrong_firmware", "ac_timer", "dc_timer", "missing_d9",
    "ignored", "lost_ack", "output_changed", "power_changed", "unknown_byte_changed", "backup_changed", "read_only"])
def test_model_specific_screen_timeout_preserves_full_settings(tmp_path, problem):
    async def run():
        server, _ = service(tmp_path, model=Model.C2000_GEN2, allow_control=problem != "read_only")
        station = C2000Station(server)
        server.connection = station
        if problem == "wrong_firmware": station.version = bytes((5, 6, 1, 2))
        if problem == "ac_timer": station.a4[1] = 1
        if problem == "dc_timer": station.a4[9] = 1
        if problem == "missing_d9": station.missing.add(0xD9)
        station.ignore = problem == "ignored"
        station.fail_write = problem == "lost_ack"
        def mutate():
            if problem == "output_changed": station.a7[1] = 0
            if problem == "power_changed": station.a4[5] ^= 1
            if problem == "unknown_byte_changed": station.a4[28] ^= 1
            if problem == "backup_changed": station.d9[20] ^= 1
        station.mutate = mutate
        original = bytes(station.a4), bytes(station.d9)
        if problem == "none":
            result = await server.set_display_timeout(60)
            assert result["settings_confirmed"] and result["metrics"]["display_timeout_seconds"] == 60
            await server.set_display_timeout(30)
            assert (bytes(station.a4), bytes(station.d9)) == original
        else:
            with pytest.raises((ValueError, RuntimeError, PermissionError, TimeoutError)):
                await server.set_display_timeout(60)
        writes = [fields for command, fields in station.requests if command == "0103"]
        assert len(writes) == (2 if problem == "none" else 0 if problem in
            ("wrong_firmware", "ac_timer", "dc_timer", "missing_d9", "read_only") else 1)
        assert all(set(fields) == {0xA1, 0xA4, 0xFD} for fields in writes)
    asyncio.run(run())


@pytest.mark.parametrize("value", [0, 10, 20, 300, 1800, True, 60.0, "60", None])
def test_no_generic_c1000_range_on_c2000(tmp_path, value):
    server, station = service(tmp_path, model=Model.C2000_GEN2)
    with pytest.raises(ValueError):
        asyncio.run(server.set_display_timeout(value))
    assert station.requests == []
