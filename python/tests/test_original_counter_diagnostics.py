"""Original report parsing stays separate from calibrated energy accounting."""

import asyncio
import json
import time

import pytest

from solix_link.original_counters import (REPORT_NAME, decode_original_counter_events,
    original_counter_value, validate_original_counters)
from solix_link.protocol import Model, decode_telemetry, tlv
from solix_link.ap_service_config import APServiceConfig
from solix_link import initialize_ap_service
from solix_link.ap_service import APService
from solix_link.mqtt_intercept import LocalMqttServer
from solix_link.telemetry_values import expansion_value
from test_energy_report import field, request
from test_native_boolean_settings import service


def report(now=None, count=1):
    now = time.time() if now is None else now
    return dict(schema_version=1, protobuf_name=REPORT_NAME, units_verified=False,
        layout_provenance="main_1_5_9_encoder", firmware_version="1.7.1", reported_at=now,
        reports=[dict(counters={f"counter_{i}_raw": i*123 for i in range(1, 9)}, event_timestamp=now)] * count)


def test_original_numbers_never_inherit_gen2_channel_names_or_units():
    payload = field(1, b"private-id") + field(19, b"".join(field(i, i*123) for i in range(1, 9)))
    data = {**request(payload), "protobuf_name": REPORT_NAME}
    records = decode_original_counter_events(data)
    assert records[0]["counters"] == report()["reports"][0]["counters"]
    assert "private" not in json.dumps(records) and "energy" not in json.dumps(records)


@pytest.mark.parametrize("payload", [b"", field(19, 1), field(19, field(1, b"bad")),
    field(19, field(1, 1) + field(1, 2)), field(19, field(1, 1)) + field(19, field(2, 2)), field(18, b"unrelated")])
def test_malformed_or_ambiguous_raw_report_rejected(payload):
    with pytest.raises(ValueError):
        decode_original_counter_events({**request(payload), "protobuf_name": REPORT_NAME})


def test_upload_is_diagnostic_and_does_not_refresh_station_or_energy(tmp_path):
    config = APServiceConfig("test", "wlan_unused", "phy9", "AT", "A1761SYNTHETIC01", "a" * 40, model=Model.C1000)
    server = LocalMqttServer(config, tmp_path)
    server.last_seen = 123
    server.metrics["software_version"] = "1.7.1"
    before = server.energy.snapshot()
    server.ingest_original_counters(report()["reports"])
    assert server.last_seen == 123 and server.energy.snapshot() == before
    raw = server.snapshot()["original_counters"]
    assert raw["firmware_version"] == "1.7.1" and not raw["installed_layout_verified"]
    assert not raw["units_verified"] and original_counter_value(raw, 3, model="c1000", protocol="native_mqtt") == 369


@pytest.mark.parametrize("problem", ["stale", "future", "gen2", "ble", "batch", "float", "bool", "overflow"])
def test_unordered_or_invalid_counters_cannot_be_presented_as_current(problem):
    raw = report(now=1000, count=2 if problem == "batch" else 1)
    now, model, protocol = 1000, "c1000", "native_mqtt"
    if problem == "stale": now += 7200
    if problem == "future": now -= 6
    if problem == "gen2": model = "c2000_gen2"
    if problem == "ble": protocol = "prime"
    if problem == "float": raw["reports"][0]["counters"]["counter_1_raw"] = 1.0
    if problem == "bool": raw["reports"][0]["counters"]["counter_1_raw"] = True
    if problem == "overflow": raw["reports"][0]["counters"]["counter_1_raw"] = 2**64
    assert original_counter_value(raw, 1, model=model, protocol=protocol, now=now) is None


def test_public_raw_validator_drops_unrelated_data():
    raw = report()
    raw["private_identity"] = "private"
    raw["reports"][0]["unknown"] = "private"
    clean = validate_original_counters(raw, model="c1000", protocol="native_mqtt")
    assert "private" not in json.dumps(clean)


@pytest.mark.parametrize("count,expected", [(0, None), (1, 75), (2, None), (255, None), (True, None)])
def test_expansion_requires_explicit_supported_presence(count, expected):
    metrics = dict(expansion_battery_count=count, expansion_battery_percentage=75, expansion_temperature_c=25)
    assert expansion_value("expansion_battery_percentage", metrics, model="c1000") == expected
    assert expansion_value("expansion_battery_percentage", metrics, model="c2000_gen2") is None


def test_c2000_unknown_presence_byte_is_not_false_absence():
    for flag in (0, 1, 255):
        value = b"\x00" + bytes(12) + bytes((flag, 0, 0))
        metrics, _ = decode_telemetry(tlv(0xC0, value), Model.C2000_GEN2)
        assert metrics.get("expansion_battery_count") == (flag if flag in (0, 1) else None)


def test_original_http_upload_is_routed_and_cross_schema_rejected(tmp_path):
    async def run():
        config = APServiceConfig("test", "wlan_unused", "phy9", "AT", "A1761SYNTHETIC01", "a" * 40, model=Model.C1000)
        initialize_ap_service(tmp_path/"ap", config)
        ap = APService(config, tmp_path/"ap")
        listener = await asyncio.start_server(ap._api, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        async def upload(schema):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            payload = {**request(field(19, field(1, 123))), "protobuf_name": schema, "device_sn": config.device_serial}
            body = json.dumps(payload).encode()
            writer.write((f"POST /equipment/logging/upload_pb_events HTTP/1.1\r\nHost: synthetic\r\nContent-Length: {len(body)}\r\n\r\n").encode()+body)
            await writer.drain(); reply = await reader.read()
            writer.close(); await writer.wait_closed()
            return reply
        try:
            assert b"200 OK" in await upload(REPORT_NAME)
            raw = ap.stations["test"].snapshot()["original_counters"]
            assert raw["reports"][0]["counters"] == {"counter_1_raw": 123}
            assert b"400 Bad Request" in await upload("charging_pps_series_c_0009")
        finally:
            listener.close(); await listener.wait_closed()
    asyncio.run(run())
