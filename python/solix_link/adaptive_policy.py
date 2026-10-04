"""Opt-in, pure Gen 2 surplus and tariff proposals; no command executor."""

from __future__ import annotations

import time

from .charging_policy import (
    ChargingPolicyRequestError, _integer, _number, _timestamp,
    preview_charging_policy,
)

COMMON = frozenset({"kind", "armed", "command_latch", "cooldown",
                    "manual_override", "minimum_reserve", "idle_watts", "maximum_watts"})
SURPLUS = frozenset({"export_start", "export_stop", "export_max_age",
                     "target_export_w", "deadband_w", "maximum_step_w"})
PRICE = frozenset({"charge_start", "charge_stop", "discharge_start", "discharge_stop",
                   "price_max_age", "soc_resume_margin"})


def _parse(request: object) -> tuple[dict, dict]:
    if type(request) is not dict or set(request) != {"config", "signals", "state"}:
        raise ChargingPolicyRequestError()
    config, signals, state = (request[k] for k in ("config", "signals", "state"))
    if type(config) is not dict or type(config.get("kind")) is not str:
        raise ChargingPolicyRequestError()
    kind = config["kind"]
    if kind not in ("surplus", "price_tou") or set(config) != COMMON | (SURPLUS if kind == "surplus" else PRICE):
        raise ChargingPolicyRequestError()
    if (type(state) is not dict or set(state) != {"previous_decision", "last_changed_at"}
            or type(state["previous_decision"]) is not str
            or state["previous_decision"] not in ("idle", "charge", "grid", "battery")
            or not _timestamp(state["last_changed_at"])
            or type(config["manual_override"]) is not str
            or config["manual_override"] not in ("none", "hold", "charge", "grid", "battery")
            or kind == "surplus" and config["manual_override"] in ("grid", "battery")):
        raise ChargingPolicyRequestError()
    if kind == "surplus":
        if (not _integer(config["target_export_w"], 0, 20000)
                or not _integer(config["deadband_w"], 0, 1000)
                or not _integer(config["maximum_step_w"], 100, 1800, 100)):
            raise ChargingPolicyRequestError()
        thresholds = (-1, 1)
    else:
        keys = ("charge_start", "charge_stop", "discharge_stop", "discharge_start")
        if (any(not _number(config[k]) or not -1000 <= config[k] <= 1000 for k in keys)
                or not config["charge_start"] < config["charge_stop"] < config["discharge_stop"] < config["discharge_start"]
                or not _integer(config["soc_resume_margin"], 1, 25)):
            raise ChargingPolicyRequestError()
        thresholds = (config["charge_start"], config["charge_stop"])
    # The established preview validates arming, latch, model, transport,
    # nonzero power ranges, caps, reserve, freshness and Standard-mode baseline.
    base = {
        "config": {
            "signal_mode": "export" if kind == "surplus" else "price",
            "armed": config["armed"], "command_latch": config["command_latch"],
            "latch_changed_at": state["last_changed_at"], "cooldown": config["cooldown"],
            "charging_watts": config["maximum_watts"], "idle_watts": config["idle_watts"],
            "minimum_reserve": config["minimum_reserve"],
            "price_start": thresholds[0], "price_stop": thresholds[1],
            "price_max_age": config.get("price_max_age", 30),
            "export_start": config.get("export_start", 600),
            "export_stop": config.get("export_stop", 300),
            "export_max_age": config.get("export_max_age", 30),
        },
        "signals": signals,
    }
    return config.copy(), base


def preview_adaptive_policy(snapshot: object, request: object, *, now: float | None = None) -> dict:
    """Return candidate settings/plan from saved inputs, without mutating inputs.

    Tariff proposals require Standard mode and zero stored tariff slots. The
    current gateway snapshot cannot prove ownership or exact hours of an active
    plan; it is insufficient for automatically replacing or restoring one.
    State describes the previous *preview*, not a confirmed executed action.
    """
    config, base = _parse(request)
    now = time.time() if now is None else now
    result = preview_charging_policy(snapshot, base, now=now)
    result.update(policy_kind=config["kind"], proposed_settings=[], desired_settings=None,
                  proposed_plan=None, executor_available=False,
                  electrical_behavior_verified=False, next_preview_state=None)
    if not result["eligible"]:
        return result
    state = request["state"].copy()
    metrics = snapshot["metrics"].copy()
    if state["last_changed_at"] > now:
        return _blocked(result, "policy_state_future")
    if config["manual_override"] == "hold":
        return _blocked(result, "manual_hold")
    if config["kind"] == "price_tou":
        if metrics.get("tou_schedule_slot_count") != 0 or type(metrics.get("tou_schedule_slot_count")) is not int:
            return _blocked(result, "empty_saved_tariff_plan_required")
    current = result["current_settings"]
    reserve = max(config["minimum_reserve"], current["backup_reserve_percentage"])
    emergency = metrics["battery_percentage"] < reserve
    desired = {**current, "backup_reserve_percentage": reserve}
    reasons = []
    if emergency:
        reasons.append("below_effective_reserve")
    if config["manual_override"] != "none":
        reasons.append("manual_override")
    if config["kind"] == "surplus":
        watts = current["ac_charging_power_limit_w"]
        if not config["idle_watts"] <= watts <= config["maximum_watts"]:
            return _blocked(result, "current_power_outside_policy_range")
        export = base["signals"]["export"]["value"]
        active = watts > config["idle_watts"]
        opportunity = export >= config["export_stop" if active else "export_start"]
        if emergency or config["manual_override"] == "charge":
            target = config["maximum_watts"]
            decision = "charge"
        elif not opportunity:
            target, decision = config["idle_watts"], "idle"
            reasons.append("no_export_opportunity")
        else:
            error = export - config["target_export_w"]
            target = watts if abs(error) <= config["deadband_w"] else int((watts + error) // 100) * 100
            decision = "charge"
            reasons.append("export_deadband" if abs(error) <= config["deadband_w"] else "surplus_step_proposed")
        target = max(config["idle_watts"], min(config["maximum_watts"], target))
        target = max(watts - config["maximum_step_w"], min(watts + config["maximum_step_w"], target))
        desired["ac_charging_power_limit_w"] = target
        if target != watts:
            result["proposed_settings"].append({"setting": "ac_charging_power_limit_w", "value": target})
        reasons.append("saved_limit_is_not_measured_battery_power")
    else:
        price = base["signals"]["price"]["value"]
        previous = state["previous_decision"]
        cheap = price <= config["charge_stop" if previous == "charge" else "charge_start"]
        expensive = price >= config["discharge_stop" if previous == "battery" else "discharge_start"]
        override = config["manual_override"]
        decision = (override if override != "none" else "charge" if cheap else "battery" if expensive else "grid")
        # A manual battery override cannot bypass the actual/specified reserve.
        # Previous preview state does not prove that a battery-use action ran.
        # This Standard-mode baseline always requires the full restart margin.
        floor = reserve + config["soc_resume_margin"]
        if emergency:
            decision = "charge"
        elif decision == "battery" and metrics["battery_percentage"] <= floor:
            decision = "grid"
            reasons.append("reserve_or_resume_margin_blocks_discharge")
        if decision in ("charge", "battery"):
            result["proposed_plan"] = {
                "enabled": True,
                "periods": [{"tariff": "off_peak" if decision == "charge" else "peak",
                             "start_hour": 0, "end_hour": 24}],
            }
        reasons.append("standard_empty_plan_baseline_only")
        reasons.append("price_charge" if decision == "charge" else "price_discharge" if decision == "battery" else "grid_baseline")
    if reserve != current["backup_reserve_percentage"]:
        result["proposed_settings"].insert(0, {"setting": "backup_reserve_percentage", "value": reserve})
        reasons.append("reserve_raise_proposed")
    changed = bool(result["proposed_settings"] or result["proposed_plan"] or decision != state["previous_decision"])
    result.update(decision="emergency" if emergency else decision, reasons=reasons,
                  desired_settings=desired,
                  next_preview_state={"previous_decision": decision, "last_changed_at": now if changed else state["last_changed_at"]})
    return result


def _blocked(result: dict, reason: str) -> dict:
    result.update(eligible=False, decision="blocked", reasons=[reason])
    return result
