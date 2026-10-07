"""Native energy SDK/HA schema and entity contracts, with synthetic counters."""

import asyncio
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from solix_link.energy_store import NativeEnergyStore
from solix_link.energy_values import validate_native_energy
from solix_link.energy_report import REPORT_NAME
from test_freshness_entities import NOW, coordinator, platform, snapshot

ROOT = Path(__file__).resolve().parents[1] / "custom_components/solix_link"
SPEC = importlib.util.spec_from_file_location("solix_native_energy_contract", ROOT / "native_energy.py")
native = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native)


def energy(**changes):
    store = NativeEnergyStore("c2000_gen2")
    report = {"protobuf_name": REPORT_NAME, "units_verified": False, "groups": {
        "standard": {"ac_input_energy_raw": 1250, "ac_output_energy_raw": 1200},
        "time_of_use": {"ac_output_energy_raw": 300}}}
    return store.ingest([report], reported_at=NOW-600, firmware_version="2.1.6.4") | changes


@pytest.mark.parametrize("changes", [{}, {"units_verified": True}, {"counter_epoch": True},
    {"groups": {}}, {"reported_at": float("nan")}, {"model": "c1000"}, {"reported_at": NOW+6},
    {"reported_at": NOW-1800}, {"firmware_version": "PRIVATE"}, {"available": False}, {"conversion_basis": "verified"}])
def test_sdk_and_component_independent_validation_have_same_contract(changes):
    value = energy(**changes)
    assert native.validate_native_energy(value, model="c2000_gen2", now=NOW) == validate_native_energy(value, model="c2000_gen2", now=NOW)


def test_parser_drops_private_fields_and_ignores_incoming_derived_values(platform):
    value = energy()
    value["groups"]["standard"].update(secret="PRIVATE", energy_kwh={"ac_input": 99})
    parsed = platform.api.parse_snapshot(snapshot(native_energy=value))
    assert "PRIVATE" not in json.dumps(parsed)
    assert parsed["native_energy"]["groups"]["standard"]["energy_kwh"]["ac_input"] == 1.25
    for changes in ({"protocol": "prime"}, {"model": "c1000"}):
        assert platform.api.parse_snapshot(snapshot(native_energy=value, **changes))["native_energy"] is None


def entities(platform, data):
    source = coordinator({"station": data})
    result = []
    entry = SimpleNamespace(runtime_data=source, async_on_unload=lambda _callback: None)
    asyncio.run(platform.sensor.async_setup_entry(None, entry, result.extend))
    return source, [entity for entity in result if isinstance(entity, platform.sensor.SolixNativeEnergySensor)]


def test_discovery_exposes_only_reported_counters_no_statistics_or_fake_zero(platform):
    source, sensors = entities(platform, snapshot(native_energy=energy()))
    assert len(sensors) == 3
    assert {entity.native_value for entity in sensors} == {1.25, 1.2, 0.3}
    for sensor in sensors:
        assert sensor.entity_description.state_class is None
        assert sensor.entity_description.entity_registry_enabled_default is False
        assert sensor.entity_registry_enabled_default is False
        assert sensor.entity_description.native_unit_of_measurement == "kWh"
        assert sensor.extra_state_attributes["units_verified"] is False
        assert sensor.extra_state_attributes["source"] == "device_energy_report"
        assert sensor.extra_state_attributes["counter_epoch"] == 1
    assert not any(sensor.channel == "dc_input" for sensor in sensors)


@pytest.mark.parametrize("option, expected", [(True, True), (False, False), (None, False), ("true", False)])
def test_native_energy_option_applies_to_later_discovery_only_and_keeps_statistics_unset(platform, option, expected):
    source = coordinator({"station": snapshot()})
    added = []
    entry = SimpleNamespace(runtime_data=source, options={"native_energy_enabled": option},
                            async_on_unload=lambda _callback: None)
    asyncio.run(platform.sensor.async_setup_entry(None, entry, added.extend))
    assert not any(isinstance(entity, platform.sensor.SolixNativeEnergySensor) for entity in added)
    source.data["station"]["native_energy"] = energy()
    for callback in source.listeners:
        callback()
    added = [entity for entity in added if isinstance(entity, platform.sensor.SolixNativeEnergySensor)]
    assert len(added) == 3
    assert all(entity.entity_registry_enabled_default is expected for entity in added)
    assert all(entity.entity_description.state_class is None for entity in added)
    assert all(entity.extra_state_attributes["units_verified"] is False for entity in added)
    # Only this entity's default is changed; shared descriptions remain disabled.
    assert all(entity.entity_description.entity_registry_enabled_default is False for entity in added)


@pytest.mark.parametrize("age,connected,success,expected", [(600, False, True, True),
    (1799, False, True, True), (1800, True, True, False), (-6, True, True, False), (1, True, False, False)])
def test_report_freshness_is_independent_of_live_power(platform, age, connected, success, expected):
    _, sensors = entities(platform, snapshot(connected=connected, available=connected, native_energy=energy()))
    sensor = next(entity for entity in sensors if entity.group == "standard" and entity.channel == "ac_input")
    source = sensor.coordinator
    source.data["station"]["native_energy"] = energy(reported_at=NOW-age, counter_epoch_started_at=NOW-age)
    source.last_update_success = success
    assert sensor.available is expected
    assert sensor.native_value == 1.25


def test_missing_report_does_not_create_entities_or_reset_existing_value_to_zero(platform):
    source, sensors = entities(platform, snapshot(native_energy=energy()))
    source.data["station"].pop("native_energy")
    assert all(entity.native_value is None and not entity.available for entity in sensors)
    assert entities(platform, snapshot(model="c1000"))[1] == []


def test_later_reports_discover_channels_once_and_isolate_multiple_devices(platform):
    source = coordinator({"first": snapshot(native_energy=energy()), "second": snapshot(model="c1000")})
    added = []
    entry = SimpleNamespace(runtime_data=source, async_on_unload=lambda _callback: None)
    asyncio.run(platform.sensor.async_setup_entry(None, entry, added.extend))
    initial = [entity for entity in added if isinstance(entity, platform.sensor.SolixNativeEnergySensor)]
    assert len(initial) == 3 and all(entity.station_name == "first" for entity in initial)
    report = energy()
    report["groups"]["standard"]["raw"]["dc_input_energy_raw"] = 0
    source.data["first"]["native_energy"] = report
    source.data["second"] = snapshot(native_energy=energy())
    for _ in range(2):
        for callback in source.listeners:
            callback()
    native_entities = [entity for entity in added if isinstance(entity, platform.sensor.SolixNativeEnergySensor)]
    assert len(native_entities) == 7
    assert len({entity._attr_unique_id for entity in native_entities}) == 7
    assert next(entity for entity in native_entities if entity.station_name == "first" and entity.channel == "dc_input").native_value == 0
