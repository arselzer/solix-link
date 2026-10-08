"""Pure ownership, hysteresis and interruption tests with synthetic baselines."""

from copy import deepcopy

import pytest

from solix_link.command_coordination import make_preconditions
from solix_link.price_policy import confirmed_state, plan_for, price_decision, validate_store

NOW = 1900000000.0
CONFIG = dict(charge_start=.15, charge_stop=.20, discharge_stop=.30, discharge_start=.35,
              cooldown=300, price_max_age=300, minimum_reserve=20, soc_resume_margin=5)


def station(decision="grid", now=NOW, **metrics):
    plan = plan_for(decision)
    s = dict(name="synthetic", model="c1000_gen2", protocol="native_mqtt", connected=True, available=True,
        last_seen_timestamp=now, controls=["set-tou-plan"], metrics=dict(
        battery_percentage=80, software_version="1.1.4.9", ac_charging_power_limit_w=300,
        max_charge_percentage=100, min_charge_percentage=1, backup_reserve_percentage=20,
        ac_fast_charge_enabled=0, ac_output_enabled=1, dc_output_enabled=0, ac_input_connected=1,
        clock_screen_enabled=0, clock_screen_transfer_status_raw=0, disaster_preparation_active=0,
        ac_output_timeout_seconds=0, dc_output_timeout_seconds=0, usage_mode="standard" if decision == "grid" else "time_of_use",
        active_tariff="none" if decision == "grid" else plan["periods"][0]["tariff"],
        tou_schedule_slot_count=len(plan["periods"])),
        tou_plan_readback=dict(schema_version=1, source="status_d9", reported_at=now, **plan))
    s["metrics"].update(metrics)
    s["command_context"] = dict(schema_version=1, gateway_instance="a" * 32, issued_at=now,
        request_window_seconds=3600, preconditions_supported=True, busy=False, expected=make_preconditions(s, now=now))
    return s


def decide(s=None, state=None, **kwargs):
    return price_decision(s or station(), CONFIG, state, **dict(now=NOW, armed=True, price=.1,
        price_timestamp=NOW) | kwargs)


def owned(decision="charge", changed_at=NOW-600):
    pending = decide(price=.1 if decision == "charge" else .4)["next_state"]
    state = confirmed_state(station(decision), pending, now=NOW)
    return {**state, "changed_at": changed_at}


@pytest.mark.parametrize("price,expected", [(-.1, "charge"), (.15, "charge"), (.25, "grid"), (.35, "battery")])
def test_decisions_do_not_modify_baseline_or_assign_electrical_proof(price, expected):
    s = station(); before = deepcopy(s)
    result = decide(s, price=price)
    assert result["eligible"] and result["decision"] == expected
    assert result["command"] == (None if expected == "grid" else {"command": "set-tou-plan", **plan_for(expected)})
    assert not result["electrical_behavior_verified"] and s == before


@pytest.mark.parametrize("decision,price,expected", [("charge", .20, "charge"), ("charge", .21, "grid"),
    ("battery", .30, "battery"), ("battery", .29, "grid")])
def test_hysteresis_uses_confirmed_action(decision, price, expected):
    result = decide(station(decision), owned(decision), price=price)
    assert result["decision"] == expected


@pytest.mark.parametrize("key,value", [("software_version", "1.1.5.0"), ("ac_fast_charge_enabled", 1),
    ("disaster_preparation_active", 1), ("clock_screen_enabled", 1), ("clock_screen_transfer_status_raw", 1),
    ("ac_output_timeout_seconds", 1), ("dc_output_timeout_seconds", 1), ("ac_input_connected", 0),
    ("ac_output_enabled", 0), ("backup_reserve_percentage", 10), ("battery_percentage", True)])
def test_no_commands_without_complete_guards(key, value):
    result = decide(station(**{key: value}))
    assert not result["eligible"] and result["command"] is None


@pytest.mark.parametrize("problem", ["original", "c2000", "stale_status", "stale_plan", "stale_price",
    "nan_price", "future_price", "missing_context", "missing_plan", "disarmed", "hold", "owned_plan_without_state"])
def test_ownership_and_freshness(problem):
    s, kwargs = station(), {}
    if problem in ("original", "c2000"): s["model"] = "c1000" if problem == "original" else "c2000_gen2"
    if problem == "stale_status": s["last_seen_timestamp"] -= 30
    if problem == "stale_plan": s["tou_plan_readback"]["reported_at"] -= 30
    if problem == "stale_price": kwargs["price_timestamp"] = NOW-300
    if problem == "nan_price": kwargs["price"] = float("nan")
    if problem == "future_price": kwargs["price_timestamp"] = NOW+6
    if problem == "missing_context": s.pop("command_context")
    if problem == "missing_plan": s.pop("tou_plan_readback")
    if problem == "disarmed": kwargs["armed"] = False
    if problem == "hold": kwargs["override"] = "hold"
    if problem == "owned_plan_without_state": s = station("charge")
    assert decide(s, **kwargs)["command"] is None


def test_pending_and_manual_intervention_require_read_only_reset():
    pending = decide()["next_state"]
    assert decide(station("charge"), pending)["reason"] == "manual_reconciliation_required"
    state = owned()
    changed = decide(station("charge", ac_charging_power_limit_w=500), state)
    assert changed["reason"] == "manual_intervention_detected" and changed["next_state"]["phase"] == "blocked"
    reset = decide(station(), changed["next_state"], action="reset")
    assert reset["eligible"] and reset["next_state"] is None and reset["command"] is None
    assert not decide(station("charge"), state, action="reset")["eligible"]


def test_release_uses_only_owned_empty_standard_baseline_and_no_unknown_restore():
    result = decide(station("charge"), owned(), action="release", price=None)
    assert result["command"] == {"command": "set-tou-plan", "enabled": False, "periods": []}
    assert decide(station("battery"), owned(), action="release")["command"] is None
    assert decide(action="release")["reason"] == "unowned"


def test_reserve_and_cooldown_override_boundaries():
    state = owned("battery", changed_at=NOW-5)
    assert decide(station("battery"), state, price=.1)["reason"] == "cooldown"
    # Leaving battery use at reserve bypasses cooldown, including forced battery.
    result = decide(station("battery", battery_percentage=20), state, override="battery")
    assert result["decision"] == "grid" and result["command"] is not None
    result = decide(station("battery", battery_percentage=19), state)
    assert result["decision"] == "charge" and result["command"] is not None
    assert decide(station(battery_percentage=25), override="battery")["decision"] == "grid"
    assert decide(station(battery_percentage=26), override="battery")["decision"] == "battery"


def test_confirmation_requires_full_matching_plan_not_ack():
    pending = decide()["next_state"]
    with pytest.raises(ValueError): confirmed_state(station(), pending, now=NOW)
    with pytest.raises(ValueError): confirmed_state(station("charge", ac_output_enabled=0), pending, now=NOW)
    assert confirmed_state(station("charge"), pending, now=NOW)["phase"] == "active"
    assert validate_store({"stations": {"synthetic": pending}})["synthetic"] == pending
    with pytest.raises(ValueError): validate_store({"stations": {"synthetic": {}}})
