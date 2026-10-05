"""Passive gateway reports explain stale states without exporting identities."""

import asyncio
import json

import pytest

from http_helpers import api_client
from test_gateway import Gateway
from solix_link.diagnostics import gateway_diagnostics
from solix_link.server import create_app


def station(**overrides):
    return {
        "name": "PRIVATE-SYNTHETIC-NAME", "model": "c1000_gen2", "protocol": "native_mqtt",
        "connected": True, "available": True, "last_seen_timestamp": 1000,
        "metrics": {"software_version": "1.1.4.9"}, **overrides,
    }


@pytest.mark.parametrize(("changes", "now", "reason"), [
    ({}, 1029.99, "ready"),
    ({}, 1030, "stale_telemetry"),
    ({"protocol": "prime"}, 1089.99, "ready"),
    ({"protocol": "prime"}, 1090, "stale_telemetry"),
    ({"connected": False}, 1001, "disconnected"),
    ({"last_seen_timestamp": None}, 1001, "awaiting_telemetry"),
    ({"last_seen_timestamp": True}, 1001, "awaiting_telemetry"),
    ({"last_seen_timestamp": "private timestamp"}, 1001, "awaiting_telemetry"),
    ({"last_seen_timestamp": float("nan")}, 1001, "awaiting_telemetry"),
    ({"last_seen_timestamp": float("inf")}, 1001, "awaiting_telemetry"),
    ({"last_seen_timestamp": 10**400}, 1001, "awaiting_telemetry"),
    ({}, 994.99, "clock_skew"),
    ({}, 995, "ready"),
    ({"available": False}, 1001, "unavailable"),
    ({"available": "yes"}, 1001, "unavailable"),
])
def test_availability_explanations_use_transport_freshness(changes, now, reason):
    report = gateway_diagnostics([station(**changes)], now=now)
    result = report["stations"][0]
    assert result["availability_reason"] == reason
    assert result["available"] == (reason == "ready")
    assert report["available_station_count"] == int(reason == "ready")
    assert result["next_step"]


def test_report_only_exports_fixed_metadata_and_valid_version():
    private = "PRIVATE-SYNTHETIC"
    source = station(model=private, protocol=private, error=f"RuntimeError: {private}",
                     address=private, account_id=private,
                     metrics={"software_version": private, "serial_number": private,
                              "battery_percentage": private, "password": private})
    report = gateway_diagnostics([source, None, station()], now=1001)
    assert private not in json.dumps(report)
    assert report["station_count"] == 2
    assert report["stations"][0]["model"] == "unknown"
    assert report["stations"][0]["software_version"] is None
    assert report["stations"][1]["software_version"] == "1.1.4.9"
    assert source["account_id"] == private


def test_gateway_endpoint_is_authenticated_passive_and_uncached(monkeypatch):
    gateway = Gateway()
    snapshot = gateway.snapshot("ups")
    snapshot.update(station())
    gateway.devices = {snapshot["name"]: object()}
    snapshot["last_seen_timestamp"] = 1000
    gateway.snapshots = lambda: [snapshot]
    monkeypatch.setattr("solix_link.diagnostics.time.time", lambda: 1001)

    async def run():
        async with api_client(create_app(gateway, token="test", allow_control=True)) as client:
            assert (await client.get("/diagnostics")).status_code == 401
            headers = {"Authorization": "Bearer test"}
            response = await client.get("/diagnostics", headers=headers)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            assert response.json()["stations"][0]["availability_reason"] == "ready"
            assert "PRIVATE-SYNTHETIC" not in response.text
            assert (await client.head("/diagnostics", headers=headers)).status_code == 200
        assert not gateway.calls

    asyncio.run(run())


def test_setup_check_route_is_authenticated_optional_and_redacts_failures():
    gateway = Gateway()
    checks = []
    async def check():
        checks.append(True)
        return {"ok": True, "read_only": True, "live_confirmation_required": True, "profiles": []}
    async def fail():
        raise RuntimeError("PRIVATE-SYNTHETIC-CREDENTIAL")

    async def run():
        async with api_client(create_app(gateway, token="test")) as client:
            assert (await client.get("/setup-check")).status_code == 401
            headers = {"Authorization": "Bearer test"}
            assert (await client.get("/setup-check", headers=headers)).status_code == 404
            gateway.check_setup = check
            result = await client.get("/setup-check", headers=headers)
            assert result.status_code == 200 and result.json()["read_only"]
            assert result.headers["cache-control"] == "no-store"
            assert (await client.head("/setup-check", headers=headers)).status_code == 200
            assert len(checks) == 2
            gateway.check_setup = fail
            result = await client.get("/setup-check", headers=headers)
            assert result.status_code == 503 and result.json() == {"error": "SetupCheckFailed"}
        assert not gateway.calls

    asyncio.run(run())
