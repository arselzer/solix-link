"""Saved plans must not inherit freshness from unrelated MQTT telemetry."""

import asyncio
import base64
import json

import pytest

from solix_link import mqtt_intercept
from solix_link.plan_readback import plan_from_d9, plan_is_fresh, validate_plan_readback
from solix_link.protocol import DATA_RESPONSE, Model, build_packet, tlv
from test_mqtt_startup import Session, make_server, mqtt_string


def d9(*, enabled=True):
    return bytes([4, 0, int(enabled), 20, 95, 1, 2, 3, 0, 6, 1, 6, 24]) + bytes(19)


def plan():
    return plan_from_d9(d9(), Model.C1000_GEN2, 1000)


def test_exact_d9_and_copying():
    assert plan()["periods"] == [dict(tariff="off_peak", start_hour=0, end_hour=6),
                                  dict(tariff="peak", start_hour=6, end_hour=24)]
    for invalid in (d9()[:-1], d9() + b"\x00", d9()[:2] + b"\x02" + d9()[3:]):
        assert plan_from_d9(invalid, Model.C1000_GEN2, 1000) is None
    assert plan_from_d9(d9(), Model.C1000, 1000) is None
    value = plan()
    copy = validate_plan_readback(value)
    copy["periods"][0]["end_hour"] = 24
    assert value["periods"][0]["end_hour"] == 6


@pytest.mark.parametrize("change", [
    {"schema_version": True}, {"enabled": 1}, {"reported_at": float("nan")},
    {"reported_at": float("inf")}, {"reported_at": True}, {"reported_at": 10**400},
    {"periods": []}, {"source": "PRIVATE"}, {"account_id": "PRIVATE"},
    {"periods": [{"tariff": "peak", "start_hour": False, "end_hour": 24}]},
    {"periods": [{"tariff": "peak", "start_hour": 0, "end_hour": 24}] * 2},
    {"periods": [{"tariff": "peak", "start_hour": 0, "end_hour": 24, "id": "PRIVATE"}]},
])
def test_invalid_plan_is_not_exposed(change):
    assert validate_plan_readback({**plan(), **change}) is None


def test_plan_freshness_is_independent_and_model_scoped():
    snapshot = dict(model="c1000_gen2", protocol="native_mqtt", available=True, connected=True,
                    last_seen_timestamp=1029, tou_plan_readback=plan())
    assert plan_is_fresh(snapshot, now=1029)
    assert not plan_is_fresh(snapshot, now=1030)
    assert not plan_is_fresh(snapshot, now=994)
    assert not plan_is_fresh({**snapshot, "model": "c1000"}, now=1029)
    assert not plan_is_fresh({**snapshot, "connected": False}, now=1029)


@pytest.mark.parametrize("model", [Model.C1000_GEN2, Model.C2000_GEN2])
def test_mqtt_partial_malformed_retained_foreign_and_reconnect(tmp_path, monkeypatch, model):
    async def run():
        _, server = make_server(tmp_path, monkeypatch, model)
        now = [1000.0]
        monkeypatch.setattr(mqtt_intercept.time, "time", lambda: now[0])
        session = Session(server)
        await session.connect()

        async def feed(body, *, retained=False, foreign=False):
            serial = "OTHER" if foreign else server.config.device_serial
            frame = build_packet(DATA_RESPONSE, bytes.fromhex("0421"), body)
            message = json.dumps({"payload": json.dumps({"pn": server.config.product, "sn": serial,
                "data": base64.b64encode(frame).decode()})}).encode()
            topic = f"dt/anker_power/{server.config.product}/{serial}/param_info"
            session.reader.feed_data(mqtt_intercept.mqtt_packet(0x31 if retained else 0x30,
                                                              mqtt_string(topic) + message))
            if not retained and not foreign:
                await asyncio.wait_for(server.activations.get(), 1)
            else:
                await asyncio.sleep(0)  # One bounded scheduler turn, no wall-clock wait.

        try:
            await feed(tlv(0xD9, d9()))
            assert server.snapshot()["tou_plan_readback"] == plan()
            detached = server.snapshot()
            detached["tou_plan_readback"]["periods"].clear()
            assert server.snapshot()["tou_plan_readback"]["periods"]
            now[0] = 1031
            await feed(tlv(0xA1, b"\x34"))
            assert server.last_seen == 1031
            assert server.snapshot()["tou_plan_readback"]["reported_at"] == 1000
            assert not plan_is_fresh(server.snapshot(), now=1031)
            await feed(tlv(0xD9, d9()), retained=True)
            await feed(tlv(0xD9, d9()), foreign=True)
            assert server.snapshot()["tou_plan_readback"]["reported_at"] == 1000
            await feed(tlv(0xA1, b"\x34") + tlv(0xD9, d9()[:-1]))
            assert server.snapshot()["tou_plan_readback"] is None
            await feed(tlv(0xD9, d9(enabled=False)))
            assert server.snapshot()["tou_plan_readback"]["enabled"] is False
        finally:
            await session.close()
        replacement = Session(server)
        try:
            await replacement.connect()
            assert server.snapshot()["tou_plan_readback"] is None
        finally:
            await replacement.close()
    asyncio.run(run())
