"""HA command envelopes and explanations with cached synthetic data only."""

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from solix_link.command_coordination import CommandCoordinator, EXTRA_METRICS, PREFERENCE_METRICS, make_preconditions
from solix_link.control_availability import REASONS, control_availability
from test_freshness_entities import NOW, coordinator, platform, snapshot
from test_gateway_contract import api, status


def additions(raw):
    context = CommandCoordinator(clock=lambda: NOW).context(raw["name"])
    context["expected"] = make_preconditions(raw)
    return {"command_context": context, "control_availability": control_availability(raw,
        ["set-charge-power"], ["set-charge-power"], gateway_enabled=True, now=NOW)}


def test_standalone_ha_whitelists_match_sdk():
    assert api.CONTROL_REASONS == REASONS.keys()
    assert api.EXPECTED_METRICS == EXTRA_METRICS | PREFERENCE_METRICS


def test_parsers_copy_known_fields_and_omit_private_data():
    raw = status()
    raw.update(additions(raw))
    raw["command_context"]["password"] = "PRIVATE"
    raw["control_availability"]["serial_number"] = "PRIVATE"
    raw["control_availability"]["commands"][0]["account_id"] = "PRIVATE"
    before = deepcopy(raw)
    parsed = api.parse_snapshot(raw)
    assert "PRIVATE" not in json.dumps(parsed)
    parsed["command_context"]["expected"]["metrics"].clear()
    parsed["control_availability"]["commands"][0]["reasons"].append("changed")
    assert raw == before


@pytest.mark.parametrize("field,value", [("gateway_instance", "PRIVATE"), ("issued_at", True),
    ("issued_at", float("inf")), ("issued_at", 10**400), ("busy", 1), ("request_window_seconds", 1),
    ("schema_version", True), ("expected", None)])
def test_malformed_context_is_ignored(field, value):
    raw = status()
    raw.update(additions(raw))
    raw["command_context"][field] = value
    assert "command_context" not in api.parse_snapshot(raw)


@pytest.mark.parametrize("field,value", [("reasons", ["PRIVATE"]), ("missing_metrics", ["owner_id"]),
    ("ready", 1), ("command", []), ("command", "set-ac-output")])
def test_malformed_explanations_do_not_become_entity_attributes(field, value):
    raw = status()
    raw.update(additions(raw))
    raw["control_availability"]["commands"][0][field] = value
    assert "control_availability" not in api.parse_snapshot(raw)


@pytest.mark.parametrize("context", [True, False])
@pytest.mark.parametrize("fail", [True, False])
def test_ha_sends_one_post_with_guarded_context_or_legacy_fallback(context, fail):
    async def run():
        raw = status()
        if context:
            raw.update(additions(raw))
        calls = []
        client = api.GatewayClient(None, "http://synthetic.test", "synthetic-token")
        async def request(method, path, body=None):
            calls.append((method, path, deepcopy(body)))
            if method == "POST" and fail:
                raise api.GatewayCommandError("Unknown result")
            return raw
        client._request = request
        payload = {"command": "set-charge-power", "watts": 300}
        if fail:
            with pytest.raises(api.GatewayCommandError):
                await client.async_command(raw["name"], payload)
        else:
            await client.async_command(raw["name"], payload)
        assert [call[0] for call in calls] == ["GET", "POST"]
        body = calls[1][2]
        if context:
            assert body["expected"] == raw["command_context"]["expected"]
            assert body["coordination"]["gateway_instance"] == raw["command_context"]["gateway_instance"]
            assert body["coordination"]["issued_at"] == NOW
            assert len(body["coordination"]["request_id"]) == 32
        else:
            assert body == payload
        assert payload == {"command":"set-charge-power", "watts":300}
    asyncio.run(run())


def test_owned_policy_keeps_exact_decision_baseline_without_replacing_preconditions():
    async def run():
        raw = status(); raw.update(additions(raw))
        baseline = api.parse_snapshot(raw)
        calls = []
        client = api.GatewayClient(None, "http://synthetic.test", "synthetic-token")
        async def request(method, path, body=None):
            assert method == "POST", "A second GET would silently replace the ownership baseline"
            calls.append(deepcopy(body)); return raw
        client._request = request
        await client.async_command(raw["name"], {"command": "set-charge-power", "watts": 300}, expected_snapshot=baseline)
        assert len(calls) == 1 and calls[0]["expected"] == baseline["command_context"]["expected"]
    asyncio.run(run())


def test_diagnostic_sensor_explains_stale_controls_without_claiming_power_state(platform):
    raw = snapshot(available=False, metrics={"ac_charging_power_limit_w": 300})
    raw.update(additions(raw))
    parsed = platform.api.parse_snapshot(raw)
    coord = coordinator({raw["name"]: parsed})
    description = next(item for item in platform.sensor.DESCRIPTIONS if item.key == "control_availability")
    entity = platform.sensor.SolixSensor(coord, raw["name"], description)
    assert entity.available and entity.native_value == 6
    assert description.entity_category == "diagnostic" and description.state_class is None
    attrs = entity.extra_state_attributes
    assert attrs["preflight_only"] and attrs["backend_validation_required"]
    assert all("telemetry_unavailable" in row["reasons"] for row in attrs["commands"])
    attrs["commands"][0]["reasons"].clear()
    assert entity.extra_state_attributes["commands"][0]["reasons"]
    coord.last_update_success = False
    assert not entity.available
    coord.data[raw["name"]].pop("control_availability")
    assert entity.native_value is None


def test_explanations_are_optional_discovered_once_without_device_io(platform):
    raw = snapshot()
    coord = coordinator({raw["name"]: platform.api.parse_snapshot(raw)})
    entry = SimpleNamespace(runtime_data=coord, async_on_unload=lambda callback: None)
    entities = []
    asyncio.run(platform.sensor.async_setup_entry(None, entry, entities.extend))
    assert not any(entity.entity_description.key == "control_availability" for entity in entities)
    raw.update(additions(raw))
    coord.data[raw["name"]] = platform.api.parse_snapshot(raw)
    coord.listeners[0]()
    coord.listeners[0]()
    assert sum(entity.entity_description.key == "control_availability" for entity in entities) == 1
