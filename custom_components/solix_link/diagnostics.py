"""Allowlisted diagnostics without configuration, identities or device requests."""

from __future__ import annotations

import math
import re
import time
from typing import TYPE_CHECKING, Any

from .api import COMMANDS, METRICS, numeric, snapshot_available
from .native_energy import validate_native_energy

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
