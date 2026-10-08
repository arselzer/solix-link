"""One durable owner for price plans and surplus watts; pure decisions only."""

from __future__ import annotations

from .price_policy import (
    PROTECTED, _baseline, _integer, _number, confirmed_state as confirm_price,
    plan_for, price_decision, validate_state as validate_price,
)

SURPLUS_KEYS = {"cooldown", "minimum_reserve", "idle_watts", "maximum_watts",
                "export_start", "export_stop", "export_max_age", "target_export_w",
                "deadband_w", "maximum_step_w", "positive_export_confirmed"}


def owner(state: dict | None) -> str | None:
    return None if state is None else state.get("policy", "price")


def validate_state(value: object) -> dict | None:
    if type(value) is not dict or "policy" not in value:
        return validate_price(value)
    if (set(value) != {"policy", "phase", "decision", "changed_at", "protected", "baseline_power_w"}
            or value["policy"] != "surplus" or value["decision"] not in ("idle", "charge")
            or not _integer(value["baseline_power_w"], 100, 1200) or value["baseline_power_w"] % 100):
        raise ValueError("Invalid surplus ownership")
    checked = validate_price({key: "grid" if key == "decision" else value[key]
                              for key in ("phase", "decision", "changed_at", "protected")})
    watts = checked["protected"]["ac_charging_power_limit_w"]
    if not _integer(watts, 100, 1200) or watts % 100:
        raise ValueError("Invalid surplus ownership")
    return {**value, "protected": checked["protected"]}


def validate_store(value: object) -> dict:
    if value is None:
        return {}
    if (type(value) is not dict or set(value) != {"stations"} or type(value["stations"]) is not dict
            or len(value["stations"]) > 32
            or any(type(k) is not str or not 0 < len(k) <= 256 for k in value["stations"])):
        raise ValueError("Invalid charging ownership store")
    result = {key: validate_state(state) for key, state in value["stations"].items()}
    if any(state is None for state in result.values()):
        raise ValueError("Invalid charging ownership store")
    return result


def validate_surplus_config(config: object) -> dict:
    if (type(config) is not dict or set(config) != SURPLUS_KEYS
            or type(config["positive_export_confirmed"]) is not bool
            or not _integer(config["cooldown"], 60, 86400)
            or not _integer(config["minimum_reserve"], 5, 95)
            or not _integer(config["export_max_age"], 1, 3600)
            or any(not _integer(config[k], 100, 1200) or config[k] % 100
                   for k in ("idle_watts", "maximum_watts", "maximum_step_w"))
            or config["idle_watts"] > config["maximum_watts"]
            or any(not _number(config[k]) or not 0 <= config[k] <= 20000
                   for k in ("export_start", "export_stop", "target_export_w", "deadband_w"))
            or config["export_start"] <= config["export_stop"]):
        raise ValueError("Invalid surplus policy configuration")
    return config.copy()


def controller_decision(snapshot: dict, config: dict, state: dict | None, *, policy: str,
                        now: float, armed: bool = False, override: str = "none",
                        action: str = "evaluate", price=None, price_timestamp=None,
                        export=None, export_timestamp=None) -> dict:
    state = validate_state(state)
    if policy not in ("price", "surplus"):
        raise ValueError("Invalid charging policy")
    if owner(state) not in (None, policy):
        return {"eligible": False, "reason": "another_policy_owns_station", "decision": None,
                "command": None, "next_state": state, "electrical_behavior_verified": False}
    if policy == "price":
        return price_decision(snapshot, config, state, now=now, armed=armed, override=override,
                              action=action, price=price, price_timestamp=price_timestamp)
    config = validate_surplus_config(config)
    if (not _number(now) or not 0 <= now < 253402300800 or type(armed) is not bool
            or override not in ("none", "hold", "charge") or action not in ("evaluate", "release", "reset")):
        raise ValueError("Invalid surplus request")
    result = {"eligible": False, "reason": "disarmed", "decision": None, "command": None,
              "next_state": state, "electrical_behavior_verified": False}

    def blocked(reason):
        result["reason"] = reason
        return result

    if action == "evaluate" and (not armed or override == "hold"):
        return blocked("manual_hold" if override == "hold" else "disarmed")
    if action == "release" and state is None:
        return blocked("unowned")
    if state and state["changed_at"] > now:
        return blocked("policy_clock_regressed")
    if state and state["phase"] != "active" and action != "reset":
        return blocked("manual_reconciliation_required")
    try:
        protected, actual = _baseline(snapshot, now)
    except ValueError as err:
        if state and str(err) in ("protected_charging_baseline_required", "plan_activation_readback_required",
                                  "empty_or_owned_all_day_plan_required"):
            result["next_state"] = {**state, "phase": "blocked"}
        return blocked(str(err))
    if actual != plan_for("grid"):
        if state:
            result["next_state"] = {**state, "phase": "blocked"}
        return blocked("empty_standard_baseline_required")
    watts = protected["ac_charging_power_limit_w"]
    if action == "reset":
        if state and watts != state["baseline_power_w"]:
            return blocked("restore_original_power_before_reset")
        result.update(eligible=True, reason="ownership_reset", next_state=None)
        return result
    if state and protected != state["protected"]:
        result["next_state"] = {**state, "phase": "blocked"}
        return blocked("manual_intervention_detected")
    if "set-charge-power" not in snapshot.get("controls", []):
        return blocked("guarded_gateway_commands_required")
    if action == "release":
        target, decision = state["baseline_power_w"], state["decision"]
    else:
        if not config["positive_export_confirmed"]:
            return blocked("export_sign_confirmation_required")
        if (not _number(export) or not -20000 <= export <= 20000 or not _number(export_timestamp)
                or not -5 <= now - export_timestamp < config["export_max_age"]):
            return blocked("fresh_finite_export_required")
        soc = snapshot.get("metrics", {}).get("battery_percentage")
        if not _integer(soc, 0, 100) or protected["backup_reserve_percentage"] < config["minimum_reserve"]:
            return blocked("configure_reserve_before_arming")
        if not config["idle_watts"] <= watts <= config["maximum_watts"]:
            return blocked("current_power_outside_policy_range")
        emergency = soc < protected["backup_reserve_percentage"]
        active = watts > config["idle_watts"]
        opportunity = export >= config["export_stop" if active else "export_start"]
        if emergency or override == "charge":
            target, decision = config["maximum_watts"], "charge"
        elif not opportunity:
            target, decision = config["idle_watts"], "idle"
        else:
            error = export - config["target_export_w"]
            target = watts if abs(error) <= config["deadband_w"] else int((watts + error) // 100) * 100
            decision = "charge"
        target = max(config["idle_watts"], min(config["maximum_watts"], target))
        target = max(watts - config["maximum_step_w"], min(watts + config["maximum_step_w"], target))
        if state and now - state["changed_at"] < config["cooldown"]:
            return blocked("cooldown")
    result["decision"] = decision
    if target == watts:
        result.update(eligible=True, reason="unchanged")
        if action == "release":
            result["next_state"] = None
        return result
    pending = {"policy": "surplus", "phase": "pending", "decision": decision, "changed_at": now,
               "protected": {**protected, "ac_charging_power_limit_w": target},
               "baseline_power_w": state["baseline_power_w"] if state else watts}
    result.update(eligible=True, reason="power_change", next_state=pending,
                  command={"command": "set-charge-power", "watts": target})
    return result


def confirmed_state(snapshot: dict, pending: dict, *, now: float) -> dict:
    pending = validate_state(pending)
    if owner(pending) == "price":
        return confirm_price(snapshot, pending, now=now)
    if pending is None or pending["phase"] != "pending":
        raise ValueError("Pending charging transition required")
    protected, actual = _baseline(snapshot, now)
    if actual != plan_for("grid") or protected != pending["protected"]:
        raise ValueError("Surplus watts or protected settings not confirmed")
    return {**pending, "phase": "active", "changed_at": now}
