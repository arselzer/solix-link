"""Allowlisted diagnostics without configuration, identities or device requests."""

from __future__ import annotations

import math
import re
import time
from typing import TYPE_CHECKING, Any

from .api import COMMANDS, METRICS, numeric, snapshot_available
from .native_energy import validate_native_energy
from .native_energy_continuity import REASONS as CONTINUITY_REASONS

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from .coordinator import SolixConfigEntry

MODELS = frozenset({"c1000", "c1000_gen2", "c2000_gen2", "c300"})
PROTOCOLS = frozenset({"legacy", "prime", "native_mqtt"})
POWER_FLOWS = frozenset({"unknown", "battery", "grid", "transitioning"})
TEXT_METRICS = {
    "battery_status": frozenset({"unknown", "idle", "charging", "discharging"}),
    "usage_mode": frozenset({"unknown", "standard", "time_of_use", "self_consumption", "custom"}),
    "active_tariff": frozenset({"unknown", "none", "peak", "mid_peak", "off_peak"}),
    "ac_output_frequency_setting_hz": frozenset({"unknown"}),
}
VERSION = re.compile(r"[0-9]{1,3}(?:\.[0-9]{1,3}){1,5}\Z")
POLICY_REASONS = frozenset({"disarmed", "manual_hold", "unowned", "another_policy_owns_station",
    "policy_clock_regressed", "manual_reconciliation_required", "manual_intervention_detected",
    "qualified_connected_c1000_gen2_required", "fresh_saved_plan_required",
    "empty_or_owned_all_day_plan_required", "protected_charging_baseline_required",
    "plan_activation_readback_required", "guarded_gateway_commands_required",
    "empty_standard_baseline_required", "restore_empty_standard_plan_before_reset",
    "restore_original_power_before_reset", "ownership_reset", "fresh_finite_price_required",
    "fresh_finite_export_required", "export_sign_confirmation_required", "configure_reserve_before_arming",
    "current_power_outside_policy_range", "unchanged", "cooldown", "plan_change", "power_change"})


def policy_diagnostics(value: object) -> dict | None:
    if type(value) is not dict or value.get("phase") not in ("unowned", "active", "pending", "blocked", "storage_error"):
        return None
    result = {"phase": value["phase"]}
    for key, allowed in (("owner", ("price", "surplus")), ("decision", ("grid", "battery", "charge", "idle")),
                         ("override", ("none", "hold", "charge", "grid", "battery")),
                         ("reason", POLICY_REASONS)):
        field = value.get(key)
        if isinstance(field, str) and field in allowed:
            result[key] = field
    if type(value.get("armed")) is bool:
        result["armed"] = value["armed"]
    return result


def diagnostics_report(snapshots: dict, *, now: float | None = None) -> dict[str, Any]:
    """Return fresh/stale operational data, dropping arbitrary strings and keys."""
    now = time.time() if now is None else now
    stations = []
    for snapshot in snapshots.values():
        if not isinstance(snapshot, dict):
            continue
        metrics = {}
        raw_metrics = snapshot.get("metrics", {})
        for key, value in raw_metrics.items() if isinstance(raw_metrics, dict) else ():
            if key not in METRICS:
                continue
            if key == "software_version":
                if isinstance(value, str) and VERSION.fullmatch(value):
                    metrics[key] = value
            elif key in TEXT_METRICS:
                if isinstance(value, str) and value in TEXT_METRICS[key]:
                    metrics[key] = value
                elif key == "ac_output_frequency_setting_hz" and numeric(value) is not None:
                    metrics[key] = value
            elif numeric(value) is not None:
                metrics[key] = value
        seen = numeric(snapshot.get("last_seen_timestamp"))
        age = now - seen if seen is not None else None
        controls = snapshot.get("controls", [])
        if not isinstance(controls, list):
            controls = []
        model, protocol = snapshot.get("model"), snapshot.get("protocol")
        power_flow = snapshot.get("power_flow")
        accounting = snapshot.get("native_meter_accounting", {})
        continuity = ({"ready": accounting["ready"], "reason": accounting["reason"]}
                      if type(accounting) is dict and type(accounting.get("ready")) is bool
                      and type(accounting.get("reason")) is str and accounting["reason"] in CONTINUITY_REASONS else None)
        stations.append({
            "station": len(stations) + 1,
            "model": model if isinstance(model, str) and model in MODELS else "unknown",
            "protocol": protocol if isinstance(protocol, str) and protocol in PROTOCOLS else "unknown",
            "connected": snapshot.get("connected") is True,
            "available": snapshot_available(snapshot, now=now),
            "last_seen_age_seconds": round(age, 2) if age is not None and math.isfinite(age) else None,
            "power_flow": power_flow if isinstance(power_flow, str) and power_flow in POWER_FLOWS else "unknown",
            "controls": sorted({c for c in controls if isinstance(c, str) and c in COMMANDS}),
            "metrics": metrics,
            "native_energy": validate_native_energy(snapshot.get("native_energy"), model=model, now=now) if protocol == "native_mqtt" else None,
            "charging_policy": policy_diagnostics(snapshot.get("price_policy")),
            "native_meter_accounting": continuity,
        })
    return {"station_count": len(stations), "stations": stations}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SolixConfigEntry,
) -> dict[str, Any]:
    """Use the existing coordinator snapshot; never poll or send a command."""
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is None:
        return {
            "gateway_authenticated": None,
            "last_poll_success": False,
            **diagnostics_report({}),
        }
    return {
        "gateway_authenticated": bool(coordinator.client.token),
        "last_poll_success": coordinator.last_update_success,
        **diagnostics_report(coordinator.data or {}),
    }
