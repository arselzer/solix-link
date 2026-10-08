"""Presence-gated pack telemetry and original raw counters in HA entities."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_freshness_entities import NOW, coordinator, platform, snapshot
from test_original_counter_diagnostics import report


def entities(platform, metrics, *, model="c1000", raw=None):
    data = snapshot(model=model, protocol="native_mqtt", metrics=metrics)
    if raw is not None: data["original_counters"] = raw
    source = coordinator({"station": data})
    entry = SimpleNamespace(runtime_data=source, async_on_unload=lambda _callback: None)
    added = []
    asyncio.run(platform.sensor.async_setup_entry(None, entry, added.extend))
    asyncio.run(platform.binary.async_setup_entry(None, entry, added.extend))
    return source, {entity.entity_description.key: entity for entity in added}


def test_absent_pack_creates_presence_but_no_zero_soc_or_temperature(platform):
    _, found = entities(platform, dict(expansion_battery_count=0, expansion_battery_percentage=0, expansion_temperature_c=0))
    assert found["expansion_battery_present"].is_on is False
    assert "expansion_battery_percentage" not in found and "expansion_temperature_c" not in found


def test_present_pack_sensors_go_unavailable_on_removal(platform):
    source, found = entities(platform, dict(expansion_battery_count=1, expansion_battery_percentage=75, expansion_temperature_c=25))
    assert found["expansion_battery_percentage"].native_value == 75
    assert found["expansion_temperature_c"].native_value == 25
    source.data["station"]["metrics"]["expansion_battery_count"] = 0
    assert not found["expansion_battery_percentage"].available and not found["expansion_temperature_c"].available
    assert found["expansion_battery_present"].is_on is False


def test_c2000_presence_is_not_a_pack_soc_decoder(platform):
    _, found = entities(platform, dict(expansion_battery_count=1, expansion_battery_percentage=75, expansion_temperature_c=25), model="c2000_gen2")
    assert found["expansion_battery_present"].is_on is True
    assert "expansion_battery_percentage" not in found and "expansion_temperature_c" not in found


def test_original_counter_entities_are_disabled_unitless_and_not_energy_statistics(platform):
    raw = report(now=NOW)
    source, found = entities(platform, {}, raw=raw)
    for index in range(1, 9):
        entity = found[f"original_counter_{index}_raw"]
        assert entity.native_value == index*123 and entity.available
        d = entity.entity_description
        assert d.entity_category == "diagnostic" and d.entity_registry_enabled_default is False
        assert d.state_class is None and d.native_unit_of_measurement is None and d.device_class is None
        assert not entity.extra_state_attributes["units_verified"]
    source.data["station"]["original_counters"]["reported_at"] -= 7200
    assert not found["original_counter_1_raw"].available


def test_multi_event_batch_has_no_fabricated_latest_counter(platform):
    _, found = entities(platform, {}, raw=report(now=NOW, count=2))
    assert found["original_counter_1_raw"].native_value is None
    assert not found["original_counter_1_raw"].available


def test_ha_filters_new_private_report_data(platform):
    raw = report(now=NOW); raw["account_id"] = "private"
    s = snapshot(model="c1000", protocol="native_mqtt", metrics={"expansion_battery_count": 0}, original_counters=raw)
    parsed = platform.api.parse_snapshot(s)
    assert "account_id" not in parsed["original_counters"] and parsed["metrics"]["expansion_battery_count"] == 0


def test_c2000_screen_options_are_only_thirty_or_sixty_seconds(platform):
    for protocol in ("prime", "native_mqtt"):
        s = snapshot(model="c2000_gen2", protocol=protocol, controls=["set-display-timeout"], metrics={"display_timeout_seconds": 30})
        assert platform.api.display_timeout_options(s) == ["30_seconds", "1_minute"]
        platform.api.validate_command(s, {"command": "set-display-timeout", "seconds": 60})
        with pytest.raises(ValueError): platform.api.validate_command(s, {"command": "set-display-timeout", "seconds": 0})
        assert not platform.api.device_timeout_options(s) and not platform.api.temperature_unit_supported(s)


def test_standalone_numeric_validator_matches_package():
    root = Path(__file__).resolve().parents[1]
    assert (root/"python/solix_link/original_counters.py").read_bytes() == (root/"custom_components/solix_link/original_counters.py").read_bytes()
