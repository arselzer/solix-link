"""Notification-only blueprint/template checks, not HA runtime execution."""

from pathlib import Path
from types import SimpleNamespace

from jinja2.nativetypes import NativeEnvironment
import pytest
import yaml

from test_charging_blueprint import BlueprintLoader, Input, resolve


DOCUMENT = yaml.load((Path(__file__).resolve().parents[1] /
    "blueprints/automation/solix_link/ups_alerts.yaml").read_text(), Loader=BlueprintLoader)


def test_alerts_start_disabled_and_have_no_command_shutdown_or_cloud_action():
    assert DOCUMENT["initial_state"] is False and DOCUMENT["mode"] == "queued"
    events = {row["id"] for row in DOCUMENT["trigger"]}
    assert events == {"mains_lost", "mains_restored", "telemetry_lost", "telemetry_restored", "battery_low", "battery_recovered"}
    assert all(row["from"] in ("on", "off") and row["to"] in ("on", "off") for row in DOCUMENT["trigger"])
    assert DOCUMENT["action"][1]["default"] == Input("notification_actions")
    default = DOCUMENT["blueprint"]["input"]["notification_actions"]["default"]
    assert default == [{"action":"persistent_notification.create", "data": {
        "title":"SOLIX Link UPS observation", "message":"{{ alert_message }}"}}]


@pytest.mark.parametrize("failure", [None, "disarmed", "wrong_station", "wrong_role", "same_entity"])
def test_alert_entity_ownership_and_roles_are_required(failure):
    inputs = {"station":"synthetic", "armed":"input_boolean.alerts", "mains":"binary_sensor.mains",
        "telemetry":"binary_sensor.telemetry", "low_reserve":"binary_sensor.low"}
    roles = {inputs["mains"]:"ac_input_connected", inputs["telemetry"]:"telemetry_available",
             inputs["low_reserve"]:"battery_reserve_low"}
    members = set(roles)
    if failure == "wrong_station":
        members.remove(inputs["mains"])
    if failure == "wrong_role":
        roles[inputs["telemetry"]] = "ac_input_connected"
    if failure == "same_entity":
        inputs["telemetry"] = inputs["mains"]
    env = NativeEnvironment()
    env.globals.update(is_state=lambda entity, state: failure != "disarmed",
        device_entities=lambda station: members, state_attr=lambda entity, attribute: roles.get(entity))
    template = DOCUMENT["condition"][0]["value_template"]
    assert bool(env.from_string(template).render(**inputs)) is (failure is None)


@pytest.mark.parametrize("event", [row["id"] for row in DOCUMENT["trigger"]])
def test_distinct_alert_messages_and_communication_uncertainty(event):
    env = NativeEnvironment()
    template = DOCUMENT["action"][0]["variables"]["alert_message"]
    message = env.from_string(template).render(trigger=SimpleNamespace(id=event))
    assert isinstance(message, str) and message
    if event == "telemetry_lost":
        assert "electrical state is unknown" in message
        assert "Mains input reported disconnected" not in message
