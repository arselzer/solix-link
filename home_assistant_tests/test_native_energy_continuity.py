"""Synthetic restored backups, epochs and HA persistence; no device requests."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from solix_link.energy_store import NativeEnergyStore
from solix_link.energy_report import REPORT_NAME
from test_energy_statistics_contract import MemoryStore
from test_freshness_entities import NOW, platform, snapshot
from test_native_energy_contract import entities
from test_recovery_contract import Entry, integration


def reports(model="c1000_gen2"):
    store = NativeEnergyStore(model)
    firmware = "1.1.4.9" if model == "c1000_gen2" else "2.1.6.4"
    results = []
    for at, raw in ((NOW-1200, 100), (NOW-900, 110), (NOW-600, 130)):
        results.append(store.ingest([{"protobuf_name": REPORT_NAME, "units_verified": False,
            "event_timestamp": at, "groups": {"standard": {
                "ac_input_energy_raw": raw, "ac_output_energy_raw": raw}}}],
            reported_at=at, firmware_version=firmware))
    return results


def make(integration, monkeypatch, data, store):
    class Client:
        url = "http://synthetic.invalid"

        async def async_devices(self):
            return deepcopy(data)

        async def async_history_summary(self):
            return None

    c = integration.coordinator.SolixCoordinator(None, Entry("native", Client.url), Client())
    c._native_store = store
    monkeypatch.setattr(integration.coordinator.time, "time", lambda: NOW)
    return c


@pytest.mark.parametrize("model", ["c1000_gen2", "c2000_gen2"])
def test_old_valid_backup_is_rejected_after_ha_reload_without_hiding_diagnostics(integration, monkeypatch, model):
    _, old, current = reports(model)
    data = {"station": snapshot(model=model, native_energy=current)}
    store = MemoryStore()
    c = make(integration, monkeypatch, data, store)
    result = asyncio.run(c._async_update_data())
    assert result["station"]["native_meter_accounting"] == {"ready": True, "reason": "none"}
    assert store.saves == 1
    persisted = deepcopy(store.value)
    data["station"]["native_energy"] = old
    reloaded = make(integration, monkeypatch, data, store)
    result = asyncio.run(reloaded._async_update_data())["station"]
    assert result["native_energy"]["meter"]["energy_kwh"]["ac_input"] == 0.01
    assert result["native_meter_accounting"] == {"ready": False, "reason": "clock_regressed"}
    assert store.value == persisted and store.saves == 1
    data["station"]["native_energy"] = current
    assert asyncio.run(reloaded._async_update_data())["station"]["native_meter_accounting"]["ready"]
    assert store.saves == 1


@pytest.mark.parametrize("problem,reason", [("total", "counter_regressed"), ("raw", "counter_regressed"),
    ("start", "epoch_regressed"), ("count", "clock_regressed"), ("old_generation", "epoch_regressed")])
def test_same_epoch_changes_and_old_generations_cannot_feed_statistics(integration, monkeypatch, problem, reason):
    current = reports()[-1]
    data = {"station": snapshot(model="c1000_gen2", native_energy=current)}
    store = MemoryStore(); c = make(integration, monkeypatch, data, store)
    asyncio.run(c._async_update_data())
    after = deepcopy(current)
    if problem in ("total", "raw"):
        after["meter"]["channels"]["ac_input"]["total_raw" if problem == "total" else "counter_raw"] -= 1
    if problem == "start": after["meter"]["started_at"] -= 1
    if problem == "count": after["meter"]["accepted_reports"] -= 1
    if problem == "old_generation": after["meter"]["generation"] = "b" * 32
    data["station"]["native_energy"] = after
    assert asyncio.run(c._async_update_data())["station"]["native_meter_accounting"] == {"ready": False, "reason": reason}
    assert store.saves == 1


def test_new_generation_requires_a_later_epoch_and_can_start_at_zero(integration, monkeypatch):
    current = reports()[-1]
    data = {"station": snapshot(model="c1000_gen2", native_energy=current)}
    store = MemoryStore(); c = make(integration, monkeypatch, data, store)
    asyncio.run(c._async_update_data())
    after = deepcopy(current)
    after["reported_at"] = NOW-10
    after["meter"].update(generation="b"*32, started_at=NOW-10, last_receipt_at=NOW-10,
                          last_event_timestamp=NOW-10, accepted_reports=1)
    for row in after["meter"]["channels"].values(): row.update(total_raw=0, counter_raw=1)
    data["station"]["native_energy"] = after
    assert asyncio.run(c._async_update_data())["station"]["native_meter_accounting"]["ready"]
    assert store.value["stations"]["station"]["generation"] == "b"*32
    assert store.saves == 2


@pytest.mark.parametrize("problem", ["corrupt", "save", "cancel_save"])
def test_storage_failures_latch_statistics_off_and_preserve_evidence(integration, monkeypatch, problem):
    data = {"station": snapshot(model="c1000_gen2", native_energy=reports()[-1])}
    store = MemoryStore({"invalid": True} if problem == "corrupt" else None)
    store.fail = problem == "save"
    if problem == "cancel_save":
        async def cancel(_value): raise asyncio.CancelledError
        store.async_save = cancel
    c = make(integration, monkeypatch, data, store)
    if problem == "cancel_save":
        with pytest.raises(asyncio.CancelledError): asyncio.run(c._async_update_data())
    result = asyncio.run(c._async_update_data())["station"]
    assert result["native_meter_accounting"] == {"ready": False, "reason": "storage_error"}
    assert result["native_energy"]["meter"]["energy_kwh"]["ac_input"] == 0.03
    assert not c._native_storage_valid and not c._native_highwater and store.saves == 0
    assert store.value == ({"invalid": True} if problem == "corrupt" else None)


@pytest.mark.parametrize("suffix", ["", ".corrupt.synthetic"])
def test_empty_load_with_storage_evidence_is_not_a_fresh_meter(integration, monkeypatch, tmp_path, suffix):
    data = {"station": snapshot(model="c1000_gen2", native_energy=reports()[-1])}
    store = MemoryStore(); store.path = str(tmp_path/"continuity")
    (tmp_path/("continuity"+suffix)).write_text("synthetic invalid persistence")
    c = make(integration, monkeypatch, data, store)
    async def executor(function, *args): return function(*args)
    c.hass = SimpleNamespace(async_add_executor_job=executor)
    assert not asyncio.run(c._async_update_data())["station"]["native_meter_accounting"]["ready"]
    assert store.saves == 0


def test_cancelled_load_does_not_skip_restore_on_retry(integration, monkeypatch):
    data = {"station": snapshot(model="c1000_gen2", native_energy=reports()[-1])}
    store = MemoryStore(); c = make(integration, monkeypatch, data, store)
    async def cancelled(): raise asyncio.CancelledError
    original = store.async_load; store.async_load = cancelled
    with pytest.raises(asyncio.CancelledError): asyncio.run(c._async_update_data())
    assert not c._native_loaded
    store.async_load = original
    assert asyncio.run(c._async_update_data())["station"]["native_meter_accounting"]["ready"]
    assert store.loads == 1


def test_entity_requires_the_persisted_gate_even_for_a_fresh_numeric_meter(platform):
    source, sensors = entities(platform, snapshot(model="c1000_gen2", native_energy=reports()[-1]))
    meter = next(s for s in sensors if isinstance(s, platform.sensor.SolixNativeMeterSensor))
    assert meter.available
    source.data["station"]["native_meter_accounting"] = {"ready": False, "reason": "counter_regressed"}
    assert meter.native_value == 0.03 and not meter.available
    assert meter.extra_state_attributes["continuity_reason"] == "counter_regressed"
    source.data["station"].pop("native_meter_accounting")
    assert not meter.available
