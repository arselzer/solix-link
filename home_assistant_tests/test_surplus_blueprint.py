"""Render service blueprint; station guards live in the shared owner."""

from pathlib import Path
from jinja2.nativetypes import NativeEnvironment
import pytest
import yaml


class Loader(yaml.SafeLoader):
    pass


Loader.add_constructor("!input", lambda _, node: node.value)
PATH = Path(__file__).resolve().parents[1] / "blueprints/automation/solix_link/surplus_charging.yaml"
BLUEPRINT = yaml.load(PATH.read_text(), Loader=Loader)


@pytest.mark.parametrize("armed,override,run,mode", [("on","none",True,"apply"),
    ("on","hold",True,"apply"),("on","charge",True,"apply"),
    ("on","unknown",False,"apply"),("off","unknown",True,"release"),
    ("off","charge",True,"release"),("unavailable","none",False,"release")])
def test_arming_hold_and_disarm_dispatch_one_owned_operation(armed,override,run,mode):
    env = NativeEnvironment()
    env.globals.update(is_state=lambda entity,value: armed == value,
                       states=lambda entity: override)
    context = dict(armed_entity="input_boolean.armed",override_entity="input_select.override")
    render = lambda text: env.from_string(text).render(context)
    assert render(BLUEPRINT["conditions"][0]["value_template"]) is run
    action = BLUEPRINT["actions"][0]
    assert action["action"] == "solix_link.charging_policy"
    assert render(action["data"]["mode"]) == mode
    assert render(action["data"]["armed"]) is (armed == "on")
    assert action["data"]["policy"] == "surplus"


def test_blueprint_starts_disabled_without_direct_writes_or_helper_latch():
    assert BLUEPRINT["initial_state"] is False and BLUEPRINT["mode"] == "single"
    assert len(BLUEPRINT["actions"]) == 1
    assert BLUEPRINT["blueprint"]["input"]["positive_export_confirmed"]["default"] is False
    serialized = repr(BLUEPRINT["actions"])
    assert all(word not in serialized for word in ("number.set_value","switch.turn","set-backup-reserve","command_latch"))
    assert BLUEPRINT["actions"][0]["data"]["export_entity"] == "export_sensor"
