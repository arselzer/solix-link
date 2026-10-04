"""Synthetic HTTP and terminal previews, with no device command or profile read."""
import asyncio
from copy import deepcopy
import json
import time

import pytest

from http_helpers import api_client
from test_adaptive_policy import request, snapshot
from test_server_readonly_features import ReadOnlyGateway
from solix_link.charging_policy import ChargingPolicyRequestError
from solix_link.server import create_app
from solix_link.terminal_preview import manual_preview


def fresh_request(kind="surplus"):
    body = request(kind)
    now = time.time()
    body["state"]["last_changed_at"] = now - 300
    for signal in body["signals"].values():
        signal["timestamp"] = now
    return body


@pytest.mark.parametrize("kind", ["surplus", "price_tou"])
@pytest.mark.parametrize("control_enabled", [False, True])
def test_adaptive_endpoint_is_authenticated_cached_and_never_executes(kind, control_enabled):
    gateway = ReadOnlyGateway()
    gateway.current = snapshot()
    before = deepcopy(gateway.current)
    async def run():
        async with api_client(create_app(gateway, token="synthetic-token", allow_control=control_enabled)) as client:
            path = "/devices/ups/adaptive-preview"
            body = fresh_request(kind)
            assert (await client.post(path, json=body)).status_code == 401
            response = await client.post(path, json=body, headers={"Authorization": "Bearer synthetic-token"})
            assert response.status_code == 200
            result = response.json()
            assert result["eligible"] and result["commands_sent"] == 0
            assert result["dry_run"] and not result["executor_available"]
            assert result["decision"] == ("charge" if kind == "surplus" else "battery")
            assert response.headers["cache-control"] == "no-store"
            assert "PRIVATE" not in response.text and "ups" not in response.text
            assert (await client.post("/devices/ups/charging-preview", json=body,
                                     headers={"Authorization": "Bearer synthetic-token"})).status_code == 400
        assert not gateway.calls and gateway.current == before
    asyncio.run(run())


@pytest.mark.parametrize("payload", [b"{", b"\xff", b'{"config":{},"config":{},"signals":{},"state":{}}',
                                    b'{"config":{},"signals":{},"state":{},"execute":true}',
                                    b'{"config":{},"signals":{"price":{"value":NaN}},"state":{}}'])
def test_adaptive_endpoint_rejects_malformed_input_and_size_without_actions(payload):
    gateway = ReadOnlyGateway()
    async def run():
        async with api_client(create_app(gateway)) as client:
            path = "/devices/ups/adaptive-preview"
            response = await client.post(path, content=payload)
            assert response.status_code == 400
            assert response.json() == {"error": "InvalidChargingPreview", "commands_sent": 0,
                                       "settings_may_have_changed": False}
            assert (await client.post(path, content=b"x" * 4097)).status_code == 413
            assert (await client.post("/devices/missing/adaptive-preview", json=fresh_request())).status_code == 404
        assert not gateway.calls
    asyncio.run(run())


@pytest.mark.parametrize("kind", ["surplus", "price_tou"])
def test_terminal_adaptive_selection_keeps_file_state_and_snapshot_unchanged(tmp_path, kind):
    body, station = request(kind), snapshot()
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(body))
    before, raw = deepcopy(station), path.read_bytes()
    fields = {"export_value": "-100", "export_age": "0"} if kind == "surplus" else {"price_value": "0.04", "price_age": "0"}
    result = manual_preview(station, path, adaptive=True, now=1000, **fields)
    assert result["decision"] == ("idle" if kind == "surplus" else "charge")
    assert result["commands_sent"] == 0 and not result["executor_available"]
    assert station == before and path.read_bytes() == raw
    with pytest.raises(ChargingPolicyRequestError):
        manual_preview(station, path, now=1000, **fields)


def test_adaptive_flag_on_cached_ap_cli_is_explicit():
    from solix_link.cli import parser
    args = parser().parse_args(["ap-service-charging-preview", "--directory", "/synthetic/ap",
                               "--request-file", "/synthetic/request.json", "--adaptive"])
    assert args.adaptive and args.command == "ap-service-charging-preview"
