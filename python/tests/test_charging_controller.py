"""Shared owner, protected restoration and one-step surplus control."""

from copy import deepcopy

import pytest

from solix_link.charging_controller import confirmed_state, controller_decision, validate_store
from test_price_policy import CONFIG, NOW, owned, station as price_station

SURPLUS_CONFIG = dict(cooldown=300, minimum_reserve=20, idle_watts=100, maximum_watts=1000,
    export_start=600, export_stop=300, export_max_age=30, target_export_w=100,
    deadband_w=50, maximum_step_w=100, positive_export_confirmed=True)


def station(**metrics):
    value = price_station(**metrics)
    value["controls"].append("set-charge-power")
    return value


def decide(snapshot=None, state=None, **kwargs):
    return controller_decision(snapshot or station(), SURPLUS_CONFIG, state, **{
        "policy": "surplus", "now": NOW, "armed": True,
        "export": 800, "export_timestamp": NOW, **kwargs})


def surplus_owned():
    result = decide()
    return {**confirmed_state(station(ac_charging_power_limit_w=400), result["next_state"], now=NOW),
            "changed_at": NOW-600}


@pytest.mark.parametrize("export,watts,override,expected", [(800,300,"none",400),
    (-500,300,"none",200), (0,300,"charge",400), (140,300,"none",200),
    (600,1000,"none",1000), (700,100,"none",200)])
def test_bounded_nonzero_steps(export, watts, override, expected):
    s = station(ac_charging_power_limit_w=watts); before = deepcopy(s)
    result = decide(s, export=export, override=override)
    assert result["eligible"]
    assert result["command"] == (None if expected == watts else {"command":"set-charge-power", "watts":expected})
    assert result["electrical_behavior_verified"] is False and s == before


def test_owners_cannot_take_over_or_release_each_other():
    price = owned()
    for action in ("evaluate", "release", "reset"):
        result = decide(price_station("charge"), price, action=action)
        assert result["reason"] == "another_policy_owns_station" and result["next_state"] == price
        surplus = surplus_owned()
        result = controller_decision(station(ac_charging_power_limit_w=400), CONFIG, surplus,
            policy="price", now=NOW, armed=True, action=action, price=.1, price_timestamp=NOW)
        assert result["reason"] == "another_policy_owns_station" and result["command"] is None


def test_release_restores_only_the_confirmed_original_power():
    state = surplus_owned()
    result = decide(station(ac_charging_power_limit_w=400), state, action="release", export=None)
    assert result["command"] == {"command":"set-charge-power", "watts":300}
    confirmed = confirmed_state(station(), result["next_state"], now=NOW)
    assert confirmed["protected"]["ac_charging_power_limit_w"] == 300
    assert confirmed["baseline_power_w"] == 300
    assert decide(station(), confirmed, action="release")["next_state"] is None


@pytest.mark.parametrize("key,value", [("ac_output_enabled",0),("dc_output_enabled",1),
    ("ac_charging_power_limit_w",500),("backup_reserve_percentage",25),("max_charge_percentage",95)])
def test_manual_intervention_blocks_surplus_and_restoration(key,value):
    state = surplus_owned()
    s = station(ac_charging_power_limit_w=400, **({key:value} if key != "ac_charging_power_limit_w" else {}))
    if key == "ac_charging_power_limit_w": s = station(ac_charging_power_limit_w=value)
    result = decide(s, state, action="release")
    assert result["command"] is None and result["next_state"]["phase"] == "blocked"


@pytest.mark.parametrize("problem", ["stale", "future", "nan", "pending", "hold", "disarmed", "plan", "original", "c2000", "missing_context"])
def test_no_control_with_incomplete_or_unsafe_inputs(problem):
    s, state, kwargs = station(), None, {}
    if problem == "stale": kwargs["export_timestamp"] = NOW-30
    if problem == "future": kwargs["export_timestamp"] = NOW+6
    if problem == "nan": kwargs["export"] = float("nan")
    if problem == "pending": state = decide()["next_state"]
    if problem == "hold": kwargs["override"] = "hold"
    if problem == "disarmed": kwargs["armed"] = False
    if problem == "plan": s = price_station("charge")
    if problem in ("original","c2000"): s["model"] = "c1000" if problem == "original" else "c2000_gen2"
    if problem == "missing_context": s.pop("command_context")
    assert decide(s, state, **kwargs)["command"] is None


def test_configured_reserve_and_export_sign_are_required_before_arming():
    assert decide(station(backup_reserve_percentage=10))["reason"] == "configure_reserve_before_arming"
    result = controller_decision(station(), {**SURPLUS_CONFIG,"positive_export_confirmed":False}, None,
        policy="surplus", now=NOW, armed=True, export=800, export_timestamp=NOW)
    assert result["reason"] == "export_sign_confirmation_required"


def test_reset_never_changes_watts_and_requires_original_power():
    state = {**surplus_owned(), "phase":"blocked"}
    assert decide(station(ac_charging_power_limit_w=400),state,action="reset")["command"] is None
    assert decide(station(ac_charging_power_limit_w=400),state,action="reset")["next_state"] == state
    assert decide(station(),state,action="reset")["next_state"] is None


def test_legacy_price_store_and_new_surplus_store_validate_without_conversion():
    states = {"price":owned(),"solar":surplus_owned()}
    assert validate_store({"stations":states}) == states
    with pytest.raises(ValueError): validate_store({"stations":{"bad":{**surplus_owned(),"baseline_power_w":0}}})
