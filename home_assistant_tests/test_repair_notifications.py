"""Repairs are actionable, scoped, deduplicated and contain no identities."""

import importlib
import json
import sys
from types import ModuleType, SimpleNamespace

from test_recovery_contract import integration
from test_charging_controller import NOW, surplus_owned
from test_native_energy_meter import ingest
from solix_link.energy_store import NativeEnergyStore


def module(integration):
    return importlib.import_module(integration.coordinator.__package__+".repair_notifications")


def test_storage_pending_and_stale_signal_conditions_have_fixed_safe_text(integration):
    repairs=module(integration)
    state={**surplus_owned(),"phase":"pending"}
    runtime={"PRIVATE-ID":dict(armed=True,mode="apply",override="none",signal_timestamp=NOW-30,signal_max_age=30)}
    active,resolved=repairs.repair_conditions({}, {"PRIVATE-ID":state}, runtime,storage_valid=False,now=NOW)
    assert {entry[0] for entry in active.values()}=={"charging_storage","charging_reconciliation","charging_signal"}
    assert "PRIVATE-ID" not in json.dumps(active) and not resolved
    active,resolved=repairs.repair_conditions({}, {}, {"PRIVATE-ID":{**runtime["PRIVATE-ID"],"override":"hold"}},storage_valid=True,now=NOW)
    assert not active and "charging_storage" in resolved


def test_quarantine_is_not_cleared_by_missing_or_invalid_energy(integration):
    repairs=module(integration)
    store=NativeEnergyStore("c1000_gen2")
    ingest(store,at=NOW,value=100)
    energy=ingest(store,at=NOW+600,value=5)
    data={"station":{"model":"c1000_gen2","native_energy":energy}}
    active,resolved=repairs.repair_conditions(data,{}, {},storage_valid=True,now=NOW+600)
    key=next(key for key in active if key.startswith("energy_quarantine"))
    assert active[key][1]["reason"]=="counter_decreased"
    _,resolved=repairs.repair_conditions({"station":{}},{}, {},storage_valid=True,now=NOW+600)
    assert key not in resolved
    restored=ingest(NativeEnergyStore("c1000_gen2"),at=NOW+600,value=5)
    _,resolved=repairs.repair_conditions({"station":{"model":"c1000_gen2","native_energy":restored}}, {},{},storage_valid=True,now=NOW+600)
    assert key in resolved


def test_registry_updates_are_scoped_and_deduplicated_without_device_requests(integration,monkeypatch):
    repairs=module(integration)
    registry={}
    ir=ModuleType("homeassistant.helpers.issue_registry")
    ir.IssueSeverity=SimpleNamespace(ERROR="error")
    ir.async_create_issue=lambda hass,domain,key,**fields:registry.update({(domain,key):fields})
    ir.async_delete_issue=lambda hass,domain,key:registry.pop((domain,key),None)
    monkeypatch.setitem(sys.modules,ir.__name__,ir)
    monkeypatch.setattr(repairs.time,"time",lambda:NOW)
    coordinator=SimpleNamespace(hass=object(),config_entry=SimpleNamespace(entry_id="test"),
        _price_states={"PRIVATE-ID":{**surplus_owned(),"phase":"blocked"}},_policy_runtime={},_price_storage_valid=True)
    repairs.sync_repairs(coordinator,{})
    repairs.sync_repairs(coordinator,{})
    assert len(registry)==1 and next(iter(registry))[1].startswith("test_")
    assert next(iter(registry.values()))["is_fixable"] is False
    coordinator._price_states={}
    # Include the monitored station so its resolved ownership can be reconciled.
    repairs.sync_repairs(coordinator,{"PRIVATE-ID":{}})
    assert not registry


def test_native_continuity_repairs_survive_missing_reports_and_clear_only_on_acceptance(integration):
    repairs = module(integration)
    data = {"PRIVATE-ID": {"native_meter_accounting": {"ready": False, "reason": "counter_regressed"}}}
    active, _ = repairs.repair_conditions(data, {}, {}, storage_valid=True, now=NOW)
    key = next(key for key in active if key.startswith("native_energy_continuity"))
    assert "PRIVATE-ID" not in json.dumps(active)
    assert key not in repairs.repair_conditions({"PRIVATE-ID": {}}, {}, {}, storage_valid=True, now=NOW)[1]
    data["PRIVATE-ID"]["native_meter_accounting"] = {"ready": True, "reason": "none"}
    assert key in repairs.repair_conditions(data, {}, {}, storage_valid=True, now=NOW)[1]
