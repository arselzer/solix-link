"""Native display preferences must confirm fresh settings and preserve outputs."""

import asyncio
import time

import pytest

from solix_link.native_mqtt import NativeMqttCommands
from solix_link.protocol import Model, decode_telemetry, tlv
from test_native_boolean_settings import Station, service, unpack


CASES = (
    ("display_brightness", "set_display_brightness", 2, 0xA3, 18, 1, "display_brightness"),
    ("display_timeout", "set_display_timeout", 60, 0xA4, 16, 2, "display_timeout_seconds"),
    ("port_memory", "set_port_memory", False, 0xA8, 23, 1, "port_memory_enabled"),
)


class PreferenceStation(Station):
    def __init__(self, server):
        super().__init__(server)
        self.da = bytearray(24)
        self.da[0] = 4
        self.clock_missing = False

    async def request(self, request):
        command, fields = unpack(request)
        if command == "0103":
            self.requests.append((command, fields))
            if not self.ignore:
                for tag, offset, width in ((0xA3, 18, 1), (0xA4, 16, 2), (0xA8, 23, 1)):
                    if tag in fields:
                        self.a4[offset:offset + width] = fields[tag][1:1 + width]
            self.mutate()
            if self.fail_write:
                raise TimeoutError("Synthetic lost acknowledgement")
            return b"\0"
        reply = await super().request(request)
        if not self.clock_missing:
            reply += tlv(0xDA, self.da)
        self.server.metrics, _ = decode_telemetry(reply[1:], self.server.config.model)
        self.server.last_seen = time.time()
        return reply


def preferences(tmp_path, **kwargs):
    server, _ = service(tmp_path, **kwargs)
    station = PreferenceStation(server)
    server.connection = station
    return server, station


@pytest.mark.parametrize("builder,method,value,tag,offset,width,metric", CASES)
def test_only_requested_preference_is_encoded(builder, method, value, tag, offset, width, metric):
    request = getattr(NativeMqttCommands("SYNTHETIC", "a" * 40, model=Model.C1000_GEN2), builder)(value)
    command, fields = unpack(request)
    assert command == "0103" and request.response_command == "0903"
    assert set(fields) == {0xA1, tag, 0xFD}
    assert fields[tag] == bytes((width,)) + int(value).to_bytes(width, "little")


@pytest.mark.parametrize("builder,method,value,tag,offset,width,metric", CASES)
@pytest.mark.parametrize("problem", ["none", "display_wake", "ignored", "charge_power", "charge_cap",
                                     "reserve", "output", "memory", "language", "missing_baseline", "lost_ack"])
def test_fresh_round_trip_and_configuration_guards(tmp_path, builder, method, value, tag, offset, width, metric, problem):
    async def run():
        server, station = preferences(tmp_path)
        station.a4[22] = 0
        original_a4, original_d9 = bytes(station.a4), bytes(station.d9)
        initial = int.from_bytes(station.a4[offset:offset + width], "little")
        if builder == "port_memory":
            initial = bool(initial)
        station.ignore = problem == "ignored"
        station.fail_write = problem == "lost_ack"
        if problem == "missing_baseline":
            station.missing.add(0xD9)
        def mutate():
            if problem == "display_wake": station.a4[22] = 1
            if problem == "charge_power": station.a4[5] ^= 1
            if problem == "charge_cap": station.d9[4] = 95
            if problem == "reserve": station.d9[3] = 15
            if problem == "output": station.a7[1] = 0
            if problem == "memory": station.a4[24] ^= 1
            if problem == "language": station.a4[26] ^= 1
        station.mutate = mutate
        if problem in ("none", "display_wake"):
            changed = await getattr(server, method)(value)
            assert changed["settings_confirmed"] and changed["metrics"][metric] == int(value)
            assert changed["metrics"]["ac_output_enabled"] == 1
            restored = await getattr(server, method)(initial)
            assert restored["metrics"][metric] == int(initial)
            expected = bytearray(original_a4); expected[22] = station.a4[22]
            assert bytes(station.a4) == bytes(expected) and bytes(station.d9) == original_d9
        else:
            with pytest.raises((ValueError, RuntimeError, TimeoutError)):
                await getattr(server, method)(value)
        writes = [fields for command, fields in station.requests if command == "0103"]
        assert len(writes) == (2 if problem in ("none", "display_wake") else 0 if problem == "missing_baseline" else 1)
    asyncio.run(run())


@pytest.mark.parametrize("builder,method,invalid", [
    ("display_brightness", "set_display_brightness", x) for x in (0, 4, 255, True, 2.0, "2", None)
] + [
    ("display_timeout", "set_display_timeout", x) for x in (-1, 65535, True, 60.0, "60", None)
] + [
    ("port_memory", "set_port_memory", x) for x in (0, 1, "true", None)
])
def test_invalid_values_fail_before_io(tmp_path, builder, method, invalid):
    server, station = preferences(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(getattr(server, method)(invalid))
    assert station.requests == []


@pytest.mark.parametrize("builder,method,value,tag,offset,width,metric", CASES)
def test_c2000_and_read_only_service_refuse_before_io(tmp_path, builder, method, value, tag, offset, width, metric):
    if builder != "display_timeout":
        server, station = preferences(tmp_path, model=Model.C2000_GEN2)
        with pytest.raises(ValueError):
            asyncio.run(getattr(server, method)(value))
        assert station.requests == []
    server, station = preferences(tmp_path, allow_control=False)
    with pytest.raises(PermissionError):
        asyncio.run(getattr(server, method)(value))
    assert station.requests == []


@pytest.mark.parametrize("problem", ["missing_clock", "enabled_clock", "pending_asset", "tariff_mode", "clock_changes"])
def test_brightness_excludes_clock_screen_or_active_tariffs(tmp_path, problem):
    server, station = preferences(tmp_path)
    station.clock_missing = problem == "missing_clock"
    if problem == "enabled_clock": station.da[1] = 0x80
    if problem == "pending_asset": station.da[2] = 1
    if problem == "tariff_mode": station.d9[2] = 1
    if problem == "clock_changes": station.mutate = lambda: station.da.__setitem__(16, 1)
    with pytest.raises((ValueError, RuntimeError)):
        asyncio.run(server.set_display_brightness(2))
    assert sum(command == "0103" for command, _ in station.requests) == int(problem == "clock_changes")
