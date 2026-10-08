"""Pure decisions for an owned all-day price plan; no I/O or command execution.

Only the C1000 Gen 2 1.1.4.9 readbacks currently establish all required guards.
An empty Standard plan is the only restorable baseline. A persisted pending
transition must be reconciled manually, never inferred successful from an ACK.
"""

from __future__ import annotations

import math

PROTECTED = (
    "ac_charging_power_limit_w", "max_charge_percentage", "min_charge_percentage",
    "backup_reserve_percentage", "ac_fast_charge_enabled", "ac_output_enabled",
    "dc_output_enabled", "ac_input_connected", "clock_screen_enabled",
    "clock_screen_transfer_status_raw", "disaster_preparation_active",
    "ac_output_timeout_seconds", "dc_output_timeout_seconds", "software_version",
)
CONFIG_KEYS = {"charge_start", "charge_stop", "discharge_stop", "discharge_start",
               "cooldown", "price_max_age", "minimum_reserve", "soc_resume_margin"}
DECISIONS = ("grid", "charge", "battery")


def _number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _integer(value: object, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def validate_config(config: object) -> dict:
    if (type(config) is not dict or set(config) != CONFIG_KEYS
            or any(not _number(config[k]) or not -1000 <= config[k] <= 1000 for k in
                   ("charge_start", "charge_stop", "discharge_stop", "discharge_start"))
            or not config["charge_start"] < config["charge_stop"] < config["discharge_stop"] < config["discharge_start"]
            or not _integer(config["cooldown"], 60, 86400)
            or not _integer(config["price_max_age"], 1, 3600)
            or not _integer(config["minimum_reserve"], 5, 95)
            or not _integer(config["soc_resume_margin"], 1, 25)):
        raise ValueError("Invalid price policy configuration or threshold order")
    return config.copy()


def plan_for(decision: str) -> dict:
    if decision not in DECISIONS:
        raise ValueError("Invalid price decision")
    return {"enabled": decision != "grid", "periods": [] if decision == "grid" else [
        {"tariff": "off_peak" if decision == "charge" else "peak", "start_hour": 0, "end_hour": 24}]}


def validate_state(value: object) -> dict | None:
    if value is None:
        return None
    if (type(value) is not dict or set(value) != {"phase", "decision", "changed_at", "protected"}
            or value["phase"] not in ("active", "pending", "blocked")
            or value["decision"] not in DECISIONS
            or not _number(value["changed_at"]) or not 0 <= value["changed_at"] < 253402300800
            or type(value["protected"]) is not dict or set(value["protected"]) != set(PROTECTED)
            or value["protected"].get("software_version") != "1.1.4.9"
            or any(type(v) is not int for k, v in value["protected"].items() if k != "software_version")):
        raise ValueError("Invalid saved price policy ownership")
    return {**value, "protected": value["protected"].copy()}


def validate_store(value: object) -> dict:
    if value is None:
        return {}
    if (type(value) is not dict or set(value) != {"stations"} or type(value["stations"]) is not dict
            or len(value["stations"]) > 32
            or any(type(k) is not str or not 0 < len(k) <= 256 for k in value["stations"])):
        raise ValueError("Invalid saved price policy store")
    result = {key: validate_state(state) for key, state in value["stations"].items()}
    if any(state is None for state in result.values()):
        raise ValueError("Invalid saved price policy store")
    return result


def _baseline(snapshot: dict, now: float) -> tuple[dict, dict]:
    if (snapshot.get("model") != "c1000_gen2" or snapshot.get("protocol") != "native_mqtt"
            or snapshot.get("connected") is not True or snapshot.get("available") is not True):
        raise ValueError("qualified_connected_c1000_gen2_required")
    seen = snapshot.get("last_seen_timestamp")
    plan = snapshot.get("tou_plan_readback")
    if (not _number(seen) or not -5 <= now - seen < 30
            or type(plan) is not dict or plan.get("schema_version") != 1
            or plan.get("source") != "status_d9" or not _number(plan.get("reported_at"))
            or not -5 <= now - plan["reported_at"] < 30):
        raise ValueError("fresh_saved_plan_required")
    actual = {key: plan.get(key) for key in ("enabled", "periods")}
    if type(actual["enabled"]) is not bool or not any(actual == plan_for(d) for d in DECISIONS):
        raise ValueError("empty_or_owned_all_day_plan_required")
    m = snapshot.get("metrics", {})
    protected = {key: m.get(key) for key in PROTECTED}
    if (protected["software_version"] != "1.1.4.9"
            or any(type(v) is not int for k, v in protected.items() if k != "software_version")
            or any(protected[k] != 0 for k in ("ac_fast_charge_enabled", "clock_screen_enabled",
                "clock_screen_transfer_status_raw", "disaster_preparation_active",
                "ac_output_timeout_seconds", "dc_output_timeout_seconds"))
            or protected["ac_output_enabled"] != 1 or protected["ac_input_connected"] != 1
            or protected["dc_output_enabled"] not in (0, 1)
            or not 100 <= protected["ac_charging_power_limit_w"] <= 1200
            or protected["ac_charging_power_limit_w"] % 100
            or protected["max_charge_percentage"] not in (80, 85, 90, 95, 100)
            or protected["min_charge_percentage"] not in (1, 5, 10, 15, 20)
            or protected["backup_reserve_percentage"] % 5
            or not max(5, protected["min_charge_percentage"] + 5)
                   <= protected["backup_reserve_percentage"] <= protected["max_charge_percentage"]):
        raise ValueError("protected_charging_baseline_required")
    if (m.get("usage_mode") != ("time_of_use" if actual["enabled"] else "standard")
            or m.get("tou_schedule_slot_count") != len(actual["periods"])
            or m.get("active_tariff") != (actual["periods"][0]["tariff"] if actual["enabled"] else "none")):
        raise ValueError("plan_activation_readback_required")
    context = snapshot.get("command_context")
    if (type(context) is not dict or type(context.get("expected")) is not dict
            or context["expected"].get("tou_plan_readback") != plan
            or any(context["expected"].get("metrics", {}).get(k) != v for k, v in protected.items())
            or "set-tou-plan" not in snapshot.get("controls", [])):
        raise ValueError("guarded_gateway_commands_required")
    return protected, actual


def confirmed_state(snapshot: dict, pending: dict, *, now: float) -> dict:
    """Only matching fresh plan AND protected readbacks clear the write latch."""
    pending = validate_state(pending)
    if pending is None or pending["phase"] != "pending":
        raise ValueError("Pending price policy transition required")
    protected, actual = _baseline(snapshot, now)
    if protected != pending["protected"] or actual != plan_for(pending["decision"]):
        raise ValueError("Price plan or protected settings not confirmed")
    return {**pending, "phase": "active", "changed_at": now}


def price_decision(snapshot: dict, config: dict, state: dict | None, *, now: float,
                   price: object = None, price_timestamp: object = None, armed: bool = False,
                   override: str = "none", action: str = "evaluate") -> dict:
    """Propose one plan command. Persist its pending state before executing it.

    Release restores only the empty Standard plan this policy acquired. Reset
    is read-only and requires that baseline already be present. Hold/disarmed
    never silently clears a persistent tariff plan.
    """
    config, state = validate_config(config), validate_state(state)
    if (not _number(now) or not 0 <= now < 253402300800 or type(armed) is not bool
            or override not in ("none", "hold", "grid", "charge", "battery")
            or action not in ("evaluate", "release", "reset")):
        raise ValueError("Invalid price policy request")
    result = {"eligible": False, "reason": "disarmed", "decision": None, "command": None,
              "next_state": state, "electrical_behavior_verified": False}

    def blocked(reason):
        result["reason"] = reason
        return result

    if action == "evaluate" and (not armed or override == "hold"):
        return blocked("manual_hold" if override == "hold" else "disarmed")
    if action == "release" and state is None:
        return blocked("unowned")
    if state is not None and state["changed_at"] > now:
        return blocked("policy_clock_regressed")
    if state is not None and state["phase"] != "active" and action != "reset":
        return blocked("manual_reconciliation_required")
    try:
        protected, actual = _baseline(snapshot, now)
    except ValueError as err:
        if state is not None and str(err) in ("protected_charging_baseline_required",
                "plan_activation_readback_required", "empty_or_owned_all_day_plan_required"):
            result["next_state"] = {**state, "phase": "blocked"}
        return blocked(str(err))
    if action == "reset":
        if actual != plan_for("grid"):
            return blocked("restore_empty_standard_plan_before_reset")
        result.update(eligible=True, reason="ownership_reset", next_state=None)
        return result
    if state is None:
        if actual != plan_for("grid"):
            return blocked("empty_standard_baseline_required")
    elif protected != state["protected"] or actual != plan_for(state["decision"]):
        result["next_state"] = {**state, "phase": "blocked"}
        return blocked("manual_intervention_detected")
    emergency = False
    if action == "release":
        decision = "grid"
    else:
        if (not _number(price) or not -1000 <= price <= 1000 or not _number(price_timestamp)
                or not -5 <= now - price_timestamp < config["price_max_age"]):
            return blocked("fresh_finite_price_required")
        soc = snapshot.get("metrics", {}).get("battery_percentage")
        if not _integer(soc, 0, 100) or protected["backup_reserve_percentage"] < config["minimum_reserve"]:
            return blocked("configure_reserve_before_arming")
        previous = state["decision"] if state else "grid"
        cheap = price <= config["charge_stop" if previous == "charge" else "charge_start"]
        expensive = price >= config["discharge_stop" if previous == "battery" else "discharge_start"]
        decision = override if override != "none" else "charge" if cheap else "battery" if expensive else "grid"
        reserve = protected["backup_reserve_percentage"]
        if soc < reserve:
            emergency = True
            decision = "charge"
        elif decision == "battery" and soc <= reserve + (0 if previous == "battery" else config["soc_resume_margin"]):
            decision = "grid"
    result["decision"] = decision
    if actual == plan_for(decision):
        result.update(eligible=True, reason="unchanged")
        if action == "release":
            result["next_state"] = None
        return result
    # Reserve protection and explicit release can leave a battery plan without
    # waiting out the cooldown. No other change bypasses it, including overrides.
    leaving_battery = state is not None and state["decision"] == "battery" and decision == "grid"
    if state and action != "release" and not leaving_battery and not emergency and now - state["changed_at"] < config["cooldown"]:
        return blocked("cooldown")
    pending = {"phase": "pending", "decision": decision, "changed_at": now, "protected": protected}
    result.update(eligible=True, reason="plan_change", next_state=pending,
                  command={"command": "set-tou-plan", **plan_for(decision)})
    return result
