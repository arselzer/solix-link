"""Exact pure controller parity and HTTP/terminal dry-run boundaries."""

import asyncio
from copy import deepcopy
import json
import time

import pytest

from http_helpers import api_client
from solix_link.charging_policy import ChargingPolicyRequestError
from solix_link.charging_controller import controller_decision
from solix_link.controller_preview import preview_controller
from solix_link.terminal_preview import manual_preview
from solix_link.server import create_app
from solix_link.cli import parser
from test_charging_controller import NOW, CONFIG, SURPLUS_CONFIG, station, surplus_owned
from test_price_policy import owned
from test_server_readonly_features import ReadOnlyGateway


def request(policy="surplus", **changes):
    role = "export" if policy == "surplus" else "price"
    signal = {"value": 800 if policy == "surplus" else .4, "timestamp": NOW}
    if role == "export": signal.update(unit="W", positive_means="export")
    return {"policy": policy, "config": deepcopy(SURPLUS_CONFIG if policy == "surplus" else CONFIG),
            "signals": {role: signal}, "ownership": None, "armed": True, "override": "none", "action": "evaluate", **changes}


@pytest.mark.parametrize("policy", ["price", "surplus"])
@pytest.mark.parametrize("problem", ["none", "reserve", "stale_plan", "other_owner", "pending", "hold", "c2000", "controls"])
def test_preview_and_ha_decision_share_all_guards_without_mutation(policy, problem):
    s, r = station(), request(policy)
    if problem == "reserve": r["config"]["minimum_reserve"] = 25
    if problem == "stale_plan": s["tou_plan_readback"]["reported_at"] -= 30
    if problem == "other_owner": r["ownership"] = surplus_owned() if policy == "price" else owned()
    if problem == "pending": r["ownership"] = {**(owned() if policy == "price" else surplus_owned()), "phase": "pending"}
    if problem == "hold": r["override"] = "hold"
    if problem == "c2000": s["model"] = "c2000_gen2"
    if problem == "controls": s["controls"] = []
    s.update(owner_id="PRIVATE", serial_number="PRIVATE")
    before = deepcopy((s, r))
    role = "price" if policy == "price" else "export"
    expected = controller_decision(s, r["config"], r["ownership"], policy=policy, now=NOW,
        armed=r["armed"], override=r["override"], action=r["action"],
        **{role: r["signals"][role]["value"], role+"_timestamp": NOW})
    preview = preview_controller(s, r, now=NOW)
    assert preview["eligible"] == expected["eligible"]
    assert preview["reasons"] == [expected["reason"]]
    assert preview["would_send"] == (expected["command"] is not None)
    assert preview["commands_sent"] == 0 and preview["dry_run"] and not preview["executor_available"]
    assert not preview["live_ha_ownership_verified"] and not preview["electrical_behavior_verified"]
    assert (s, r) == before and "PRIVATE" not in json.dumps(preview)


def test_configured_reserve_cannot_be_raised_by_the_exact_preview():
    r = request(); r["config"]["minimum_reserve"] = 25
    result = preview_controller(station(), r, now=NOW)
    assert result["reasons"] == ["configure_reserve_before_arming"]
    assert not result["proposed_settings"] and not result["would_send"]


@pytest.mark.parametrize("field,value", [("controls", None), ("controls", "set-tou-plan"),
    ("command_context", {"expected": {"metrics": None}}), ("command_context", None)])
def test_malformed_offline_prerequisites_block_without_mutating_the_input(field, value):
    s = station(); s[field] = value
    before = deepcopy(s)
    result = preview_controller(s, request(), now=NOW)
    assert result["reasons"] == ["guarded_gateway_commands_required"]
    assert not result["would_send"] and s == before


@pytest.mark.parametrize("now", [float("nan"), -1, "PRIVATE"])
def test_invalid_preview_clocks_have_a_fixed_error(now):
    with pytest.raises(ChargingPolicyRequestError, match="^InvalidChargingPreview$"):
        preview_controller(station(), request(), now=now)


def test_supplied_matching_owner_can_preview_release_without_acquiring_or_clearing_it():
    state = surplus_owned()
    r = request(ownership=state, action="release", armed=False)
    result = preview_controller(station(ac_charging_power_limit_w=400), r, now=NOW)
    assert result["proposed_settings"] == [{"command": "set-charge-power", "watts": 300}]
    assert result["phase"] == "active" and result["owner"] == "surplus"
    assert result["ownership_basis"] == "caller_supplied" and r["ownership"] == state


@pytest.mark.parametrize("changes", [{"execute": True}, {"ownership": {"id": "PRIVATE"}},
    {"policy": "PRIVATE"}, {"signals": {"export": {"value": "PRIVATE", "timestamp": NOW}}}, {"armed": 1}])
def test_invalid_requests_are_fixed_errors(changes):
    with pytest.raises(ChargingPolicyRequestError, match="^InvalidChargingPreview$"):
        preview_controller(station(), request(**changes), now=NOW)


def test_terminal_file_and_manual_signals_use_the_same_controller(tmp_path):
    r = request(); path = tmp_path/"request.json"; path.write_text(json.dumps(r))
    before = path.read_bytes()
    result = manual_preview(station(), path, controller=True, export_value="-500", export_age="0", now=NOW)
    assert result["proposed_settings"] == [{"command": "set-charge-power", "watts": 200}]
    assert path.read_bytes() == before and result["controller_rules"] == "ha_shared_charging"
    args = parser().parse_args(["charging-preview", "--snapshot-file", "snapshot.json", "--request-file", str(path), "--controller"])
    assert args.controller and not args.adaptive
    args = parser().parse_args(["ap-service-charging-preview", "--directory", str(tmp_path), "--request-file", str(path), "--controller"])
    assert args.controller and not args.adaptive


@pytest.mark.parametrize("disaster", [0, 1])
def test_read_only_terminal_retains_guard_settings_without_activating_controls(monkeypatch, disaster):
    from solix_link.gateway_client import GatewayClient
    from solix_link.protocol import Model
    from solix_link.tui import Target, TuiBackend, controls_for, public_snapshot

    monkeypatch.setattr(time, "time", lambda: NOW)
    s = station(disaster_preparation_active=disaster)
    s["metrics"]["serial_number"] = "PRIVATE"
    s["controls"].append("PRIVATE")
    raw = GatewayClient._station(s)
    backend = TuiBackend([])
    backend.target = Target("demo", "Demo", Model.C1000_GEN2, gateway=True)
    backend.gateway_snapshot = raw
    cached = public_snapshot(raw)
    candidate = backend.preview_snapshot(cached)
    assert not raw["control_enabled"] and controls_for(backend.target) == ()
    assert candidate["metrics"]["disaster_preparation_active"] == disaster
    assert candidate["command_context"]["expected"]["metrics"]["clock_screen_enabled"] == 0
    assert "PRIVATE" not in repr(candidate)
    result = preview_controller(candidate, request(), now=NOW)
    assert result["eligible"] is (disaster == 0)
    assert result["commands_sent"] == 0 and not result["executor_available"]


@pytest.mark.parametrize("allow_control", [False, True])
def test_gateway_preview_checks_auth_capabilities_and_never_reaches_commands(allow_control):
    gateway = ReadOnlyGateway()
    gateway.current = station(now=time.time()); gateway.current["name"] = "ups"
    r = request(); r["signals"]["export"]["timestamp"] = time.time()
    async def run():
        async with api_client(create_app(gateway, token="token", allow_control=allow_control)) as client:
            route = "/devices/ups/controller-preview"
            assert (await client.post(route, json=r)).status_code == 401
            response = await client.post(route, json=r, headers={"Authorization": "Bearer token"})
            assert response.status_code == 200
            result = response.json()
            assert result["eligible"] is allow_control
            assert result["commands_sent"] == 0 and not gateway.calls
            if not allow_control: assert result["reasons"] == ["guarded_gateway_commands_required"]
            assert (await client.post(route, content=b"x"*4097, headers={"Authorization": "Bearer token"})).status_code == 413
    asyncio.run(run())
