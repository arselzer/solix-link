"""Read-only adapter for the exact HA charging-controller decision rules."""

from __future__ import annotations

import time

from .charging_controller import (controller_decision, owner, validate_state, validate_surplus_config)
from .price_policy import PROTECTED, _number, validate_config
from .charging_policy import ChargingPolicyRequestError
from .command_coordination import make_preconditions


def cached_controller_snapshot(snapshot: dict, controls) -> dict:
    """Add cached prerequisites, without creating an executable request ticket."""
    result = {**snapshot, "controls": list(controls)}
    result["command_context"] = {"expected": make_preconditions(result)}
    return result


def preview_controller(snapshot: object, request: object, *, now: float | None = None) -> dict:
    """Caller-supplied ownership is a simulation, never proof of HA's live owner."""
    try:
        if (type(request) is not dict or set(request) != {"policy", "config", "signals", "ownership", "armed", "override", "action"}
                or type(request["policy"]) is not str or request["policy"] not in ("price", "surplus")
                or type(request["armed"]) is not bool or type(request["override"]) is not str
                or request["override"] not in (("none", "hold", "charge", "grid", "battery") if request["policy"] == "price" else ("none", "hold", "charge"))
                or type(request["action"]) is not str or request["action"] not in ("evaluate", "release", "reset")):
            raise ValueError
        policy = request["policy"]
        config = (validate_config if policy == "price" else validate_surplus_config)(request["config"])
        ownership = validate_state(request["ownership"])
        role = "price" if policy == "price" else "export"
        signals = request["signals"]
        if type(signals) is not dict or set(signals) != {role}:
            raise ValueError
        signal = signals[role]
        fields = {"value", "timestamp"} if policy == "price" else {"value", "timestamp", "unit", "positive_means"}
        if (type(signal) is not dict or set(signal) != fields
                or any(v is not None and not _number(v) for k, v in signal.items() if k in ("value", "timestamp"))
                or policy == "surplus" and (signal["unit"] != "W" or signal["positive_means"] != "export")):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise ChargingPolicyRequestError() from None
    now = time.time() if now is None else now
    if not _number(now) or not 0 <= now < 253402300800:
        raise ChargingPolicyRequestError()
    if type(snapshot) is not dict or type(snapshot.get("metrics")) is not dict:
        snapshot = {}
    else:
        snapshot = snapshot.copy()
        if type(snapshot.get("controls")) is not list:
            snapshot["controls"] = []
        context = snapshot.get("command_context")
        if (type(context) is not dict or type(context.get("expected")) is not dict
                or type(context["expected"].get("metrics")) is not dict):
            snapshot["command_context"] = {}
    result = controller_decision(snapshot, config, ownership, policy=policy, now=now,
        armed=request["armed"], override=request["override"], action=request["action"],
        **({"price": signal["value"], "price_timestamp": signal["timestamp"]} if policy == "price" else
           {"export": signal["value"], "export_timestamp": signal["timestamp"]}))
    command = result["command"]
    metrics = snapshot.get("metrics", {})
    current = {key: value for key in PROTECTED if (value := metrics.get(key)) is not None
               and (type(value) is int and abs(value) <= 2**53-1 or key == "software_version" and value == "1.1.4.9")}
    seen = snapshot.get("last_seen_timestamp")
    return {"schema_version": 1, "dry_run": True, "commands_sent": 0,
        "controller_rules": "ha_shared_charging", "policy_kind": policy,
        "eligible": result["eligible"], "decision": result["decision"] or ("unchanged" if result["eligible"] else "blocked"),
        "reasons": [result["reason"]], "would_send": command is not None,
        "proposed_settings": [command] if command and command["command"] == "set-charge-power" else [],
        "proposed_plan": {key: command[key] for key in ("enabled", "periods")} if command and command["command"] == "set-tou-plan" else None,
        "current_settings": current, "desired_settings": None,
        "telemetry_age_seconds": round(now-seen, 3) if _number(now) and _number(seen) else None,
        "owner": owner(ownership), "phase": ownership["phase"] if ownership else "unowned",
        "ownership_basis": "caller_supplied" if ownership else "assumed_unowned",
        "live_ha_ownership_verified": False, "executor_available": False, "electrical_behavior_verified": False}
