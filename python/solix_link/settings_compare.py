"""Compare sanitized partial exports without providing a restore operation."""

from .settings_export import export_settings
from .plan_readback import validate_plan_readback


def _export(value: object) -> dict:
    if (type(value) is not dict or type(value.get("schema_version")) is not int
            or value["schema_version"] != 1 or type(value.get("settings")) is not dict):
        raise ValueError("Invalid partial settings export")
    result = export_settings({"model": value.get("model"), "protocol": value.get("protocol"),
        "metrics": value["settings"], "tou_plan_readback": value.get("tou_plan_readback")})
    result["snapshot_fresh"] = value.get("snapshot_fresh") is True
    result["tou_plan_fresh"] = value.get("tou_plan_fresh") is True
    return result


def compare_settings(before: object, after: object) -> dict:
    """Freshness is metadata, not proof of independent reads of each preference."""
    left, right = _export(before), _export(after)
    compatible = left["model"] is not None and left["model"] == right["model"]
    changes = []
    added, missing = [], []
    if compatible:
        a, b = left["settings"], right["settings"]
        changes = [{"field": key, "before": a[key], "after": b[key]}
                   for key in sorted(a.keys() & b.keys()) if a[key] != b[key]]
        added, missing = sorted(b.keys() - a.keys()), sorted(a.keys() - b.keys())
        plans = [validate_plan_readback(item["tou_plan_readback"]) for item in (left, right)]
        if all(plans) and left["tou_plan_fresh"] and right["tou_plan_fresh"]:
            a_plan, b_plan = ({"enabled": plan["enabled"], "periods": plan["periods"]} for plan in plans)
            if a_plan != b_plan:
                changes.append({"field": "tou_plan", "before": a_plan, "after": b_plan})
    return {"schema_version": 1, "model": right["model"], "compatible": compatible,
        "complete": False, "restore_supported": False, "field_freshness_verified": False,
        "before_fresh": left["snapshot_fresh"], "after_fresh": right["snapshot_fresh"],
        "changes": changes, "newly_reported_fields": added, "no_longer_reported_fields": missing}
