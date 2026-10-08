"""Actionable HA Repairs from existing validated state, never station requests."""

from __future__ import annotations

import hashlib
import time

from .const import DOMAIN
from .native_energy import validate_native_energy


def repair_conditions(snapshots: dict, ownership: dict, runtime: dict, *,
                      storage_valid: bool, now: float) -> tuple[dict, set]:
    active, resolved = {}, set()
    if storage_valid:
        resolved.add("charging_storage")
    else:
        active["charging_storage"] = ("charging_storage", {})
    names = list(snapshots) + [name for name in ownership if name not in snapshots]
    for number, name in enumerate(names, 1):
        # Issue IDs and descriptions do not contain names, URLs or identities.
        suffix = hashlib.sha256(name.encode()).hexdigest()[:16]
        label = f"Station {number}"
        state = ownership.get(name, {})
        key = "charging_reconciliation_" + suffix
        if state.get("phase") in ("pending", "blocked"):
            active[key] = ("charging_reconciliation", {"station": label})
        elif storage_valid:
            resolved.add(key)
        last = runtime.get(name, {})
        key = "charging_signal_" + suffix
        stamp, age = last.get("signal_timestamp"), last.get("signal_max_age")
        stale = (last.get("armed") is True and last.get("mode") == "apply"
                 and last.get("override") != "hold"
                 and (last.get("reason") in ("fresh_finite_price_required", "fresh_finite_export_required",
                                            "export_sign_confirmation_required")
                      or stamp is None or not -5 <= now - stamp < age))
        if stale:
            active[key] = ("charging_signal", {"station": label})
        elif last:
            resolved.add(key)
        snapshot = snapshots.get(name, {})
        energy = validate_native_energy(snapshot.get("native_energy"), model=snapshot.get("model"), now=now)
        meter = energy.get("meter") if energy else None
        key = "energy_quarantine_" + suffix
        if meter and meter["status"] == "quarantined":
            active[key] = ("energy_quarantine", {"station": label, "reason": meter["reason"]})
        elif meter and meter["status"] == "tracking":
            resolved.add(key)
        # Missing/invalid energy data is not evidence quarantine was repaired.
    return active, resolved


def sync_repairs(coordinator, snapshots: dict) -> None:
    if getattr(coordinator, "hass", None) is None:
        return
    from homeassistant.helpers import issue_registry as ir
    active, resolved = repair_conditions(snapshots, coordinator._price_states, coordinator._policy_runtime,
        storage_valid=coordinator._price_storage_valid, now=time.time())
    prefix = coordinator.config_entry.entry_id + "_"
    for key, (translation, placeholders) in active.items():
        ir.async_create_issue(coordinator.hass, DOMAIN, prefix + key, is_fixable=False,
            is_persistent=False, severity=ir.IssueSeverity.ERROR, translation_key=translation,
            translation_placeholders=placeholders)
    for key in resolved:
        ir.async_delete_issue(coordinator.hass, DOMAIN, prefix + key)
