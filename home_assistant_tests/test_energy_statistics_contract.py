"""All-model energy discovery and persistent HA continuity, without devices."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from test_freshness_entities import NOW, coordinator, platform, snapshot
from test_history_contract import attached, make_coordinator, station, summary, history
from test_recovery_contract import integration

GENERATION = "a" * 32


def metered_history(**changes):
    return attached(**({"generation": GENERATION} | changes))


@pytest.mark.parametrize("model,protocol", [("c300", "legacy"), ("c1000", "prime"),
    ("c1000_gen2", "native_mqtt"), ("c2000_gen2", "native_mqtt")])
def test_all_models_have_separate_energy_entities_with_correct_statistics(platform, model, protocol):
    data = snapshot(model=model, protocol=protocol, history=metered_history(model=model, protocol=protocol))
    source = coordinator({"station": data})
    source.history_accounting_ready = True
    added = []
    entry = SimpleNamespace(runtime_data=source, options={"history_energy_enabled": True},
                            async_on_unload=lambda _callback: None)
    asyncio.run(platform.sensor.async_setup_entry(None, entry, added.extend))
    sensors = [s for s in added if isinstance(s, platform.sensor.SolixHistoryEnergySensor)]
    assert len(sensors) == 2
    assert [s.native_value for s in sensors] == [1.25, 0.8]
    for sensor in sensors:
        assert sensor.available and sensor.entity_registry_enabled_default
        assert sensor.entity_description.device_class == "energy"
        assert sensor.entity_description.state_class == "total"
        assert sensor.entity_description.native_unit_of_measurement == "kWh"
        assert sensor.entity_description.entity_category is None
        assert sensor.last_reset.timestamp() == NOW-7200
        assert sensor.extra_state_attributes["estimated"] is True
        assert sensor.extra_state_attributes["includes_bypass"] is True
        assert sensor.extra_state_attributes["mode_group"] == "all_observed_modes"
        assert sensor.extra_state_attributes["generation"] == GENERATION
    source.history_accounting_ready = False
    assert all(not s.available for s in sensors)


def test_missing_channel_or_legacy_epoch_cannot_create_fake_zero_statistics(platform):
    data = metered_history()
    data["lifetime_totals"].update(ac_input_coverage_seconds=0, ac_input_energy_kwh_estimate=0)
    source = coordinator({"station": snapshot(history=data)})
    added = []
    entry = SimpleNamespace(runtime_data=source, options={}, async_on_unload=lambda _callback: None)
    asyncio.run(platform.sensor.async_setup_entry(None, entry, added.extend))
    sensors = [s for s in added if isinstance(s, platform.sensor.SolixHistoryEnergySensor)]
    assert len(sensors) == 1 and sensors[0].channel == "output"
    assert not sensors[0].entity_registry_enabled_default
    source.data["station"]["history"].pop("generation")
    assert not sensors[0].available
    source.data["station"].pop("history")
    assert sensors[0].native_value is None and sensors[0].last_reset is None


class MemoryStore:
    def __init__(self, value=None):
        self.value, self.loads, self.saves, self.fail = value, 0, 0, False

    async def async_load(self):
        self.loads += 1
        return deepcopy(self.value)

    async def async_save(self, value):
        if self.fail:
            raise OSError("synthetic storage error")
        self.saves += 1
        self.value = deepcopy(value)


def test_reload_persists_highwater_and_rejects_database_rollback(integration, monkeypatch):
    state = {"history": summary(station(generation=GENERATION))}
    store = MemoryStore()
    source, _ = make_coordinator(integration, monkeypatch, state)
    source._history_store = store
    first = asyncio.run(source._async_update_data())
    assert source.history_accounting_ready and "history" in first["station"]
    assert store.loads == 1 and store.saves == 1
    state["clock"] = 5
    asyncio.run(source._async_update_data())
    assert store.saves == 1
    rolled_back = station(generation=GENERATION)
    rolled_back["lifetime_totals"]["ac_output_energy_kwh_estimate"] -= 0.1
    state.update(history=summary(rolled_back), clock=30)
    monkeypatch.setattr(integration.coordinator, "parse_history_summary", history.parse_history_summary)
    restored, _ = make_coordinator(integration, monkeypatch, state)
    restored._history_store = store
    assert "history" not in asyncio.run(restored._async_update_data())["station"]
    assert store.saves == 1
    state.update(clock=60, history=summary(station(generation=GENERATION)))
    assert "history" in asyncio.run(restored._async_update_data())["station"]


def test_new_history_requires_a_later_nonoverlapping_epoch():
    before = metered_history()
    after = metered_history(generation="b"*32)
    assert not history.history_nonregressing(before, after)
    after["collection_start"] = before["last_seen_timestamp"] + 0.5
    assert history.history_nonregressing(before, after)
    after["generation"] = GENERATION
    assert not history.history_nonregressing(before, after)
    after.pop("generation")
    assert not history.history_nonregressing(before, after)


def test_pending_generation_can_accept_its_first_real_sample():
    pending = metered_history(collection_start=None, last_seen_timestamp=None,
        lifetime_totals=dict.fromkeys(history.TOTAL_KEYS, 0), gap_open=True)
    assert history.history_nonregressing(pending, metered_history())


def test_corrupt_persistence_is_preserved_and_statistics_fail_closed(integration, monkeypatch):
    state = {"history": summary(station(generation=GENERATION))}
    store = MemoryStore({"secret": "synthetic invalid state"})
    source, _ = make_coordinator(integration, monkeypatch, state)
    source._history_store = store
    result = asyncio.run(source._async_update_data())
    assert "history" in result["station"]  # Diagnostics and live telemetry survive.
    assert not source.history_accounting_ready and store.saves == 0
    assert store.value == {"secret": "synthetic invalid state"}


def test_write_failure_and_recovery_gate_energy_statistics(integration, monkeypatch):
    state = {"history": summary(station(generation=GENERATION))}
    store = MemoryStore()
    store.fail = True
    source, _ = make_coordinator(integration, monkeypatch, state)
    source._history_store = store
    assert "history" in asyncio.run(source._async_update_data())["station"]
    assert not source.history_accounting_ready
    store.fail = False
    state["clock"] = 5
    asyncio.run(source._async_update_data())
    assert source.history_accounting_ready and store.saves == 1


@pytest.mark.parametrize("suffix", ["", ".corrupt.synthetic"])
def test_ha_empty_load_with_existing_or_renamed_corruption_is_not_a_fresh_baseline(integration, monkeypatch, tmp_path, suffix):
    state = {"history": summary(station(generation=GENERATION))}
    store = MemoryStore()
    store.path = str(tmp_path / "energy-continuity")
    (tmp_path / ("energy-continuity"+suffix)).write_text("synthetic broken storage")
    source, _ = make_coordinator(integration, monkeypatch, state)
    source._history_store = store
    async def executor(function, *args):
        return function(*args)
    source.hass = SimpleNamespace(async_add_executor_job=executor)
    assert "history" in asyncio.run(source._async_update_data())["station"]
    assert not source.history_accounting_ready and store.saves == 0


def test_genuinely_absent_storage_can_establish_first_baseline(integration, monkeypatch, tmp_path):
    state = {"history": summary(station(generation=GENERATION))}
    store = MemoryStore()
    store.path = str(tmp_path / "new-continuity")
    source, _ = make_coordinator(integration, monkeypatch, state)
    source._history_store = store
    async def executor(function, *args):
        return function(*args)
    source.hass = SimpleNamespace(async_add_executor_job=executor)
    asyncio.run(source._async_update_data())
    assert source.history_accounting_ready and store.saves == 1


def test_options_enable_new_history_entities_without_controls(integration):
    flow = integration.flow.SolixConfigFlow.async_get_options_flow(None)
    flow.config_entry = SimpleNamespace(options={"native_energy_enabled": True})
    result = asyncio.run(flow.async_step_init({"native_energy_enabled": True, "history_energy_enabled": True}))
    assert result["data"] == {"native_energy_enabled": True, "history_energy_enabled": True}
