"""Reported ports, duration estimates and versions across supported models."""
import asyncio
from types import SimpleNamespace

import pytest

from test_freshness_entities import NOW, coordinator, platform, snapshot
from test_wifi_signal_presentation import report


def entity(platform, key, model, value=None, **changes):
    data = snapshot(model=model, metrics={key: value}, **changes)
    source = coordinator({"station": data})
    description = next(row for row in platform.sensor.DESCRIPTIONS if row.key == key)
    return platform.sensor.SolixSensor(source, "station", description)


@pytest.mark.parametrize("key,model,allowed", [
    ("usb_c1_power_w", "c1000", True), ("usb_c2_power_w", "c300", True),
    ("usb_c3_power_w", "c1000", False), ("usb_c3_power_w", "c300", True),
    ("usb_a2_power_w", "c1000", True), ("usb_a2_power_w", "c300", False),
    ("usb_a1_power_w", "c2000_gen2", False), ("dc_input_power_w", "c1000", True),
    ("dc_input_power_w", "c1000_gen2", False), ("solar_input_power_w", "c300", True),
    ("input_power_w", "c1000_gen2", False)])
def test_ports_have_model_scope_and_zero_is_valid(platform, key, model, allowed):
    sensor = entity(platform, key, model, 0)
    assert sensor.available is allowed
    assert sensor.native_value == (0 if allowed else None)
    assert sensor.entity_description.state_class == "measurement"
    assert sensor.entity_description.native_unit_of_measurement == "W"
    sensor.snapshot["metrics"][key] = -1
    assert not sensor.available


@pytest.mark.parametrize("model,activity,value,expected", [
    ("c1000", None, 5994, 5994), ("c1000", None, "unknown", None), ("c1000", None, 6000, None),
    ("c1000_gen2", "charging", 60, 60), ("c2000_gen2", "discharging", 120, 120),
    ("c300", "idle", 0, None), ("c1000_gen2", "unknown", 60, None),
    ("c1000_gen2", "idle", 60, None), ("c2000_gen2", "charging", 65535*6, None),
    ("c1000", None, True, None), ("c1000", None, 6.0, None), ("c300", "charging", -6, None)])
def test_runtime_estimate_requires_valid_direction_and_bounds(platform, model, activity, value, expected):
    sensor = entity(platform, "time_remaining_minutes", model, value)
    sensor.snapshot["metrics"]["battery_status"] = activity
    assert sensor.native_value == expected
    assert sensor.available is (expected is not None)
    assert sensor.entity_description.state_class is None
    assert sensor.extra_state_attributes["estimated"] is True
    expected_kind = {"charging": "time_to_full", "discharging": "time_to_empty"}.get(activity, "remaining_time")
    assert sensor.extra_state_attributes["estimate_kind"] == expected_kind


@pytest.mark.parametrize("model,value,expected", [("c1000", 0, 0), ("c2000_gen2", 300, 300),
    ("c300", 1, 1), ("c1000_gen2", 300, None), ("c1000", 0xffffffff, None), ("c1000", True, None)])
def test_countdown_is_not_saved_timeout(platform, model, value, expected):
    for key in ("ac_output_timer_remaining_seconds", "dc_output_timer_remaining_seconds"):
        sensor = entity(platform, key, model, value)
        sensor.snapshot["metrics"]["ac_output_timeout_seconds"] = 3600
        assert sensor.native_value == expected
        assert sensor.entity_description.state_class is None
        assert sensor.extra_state_attributes["zero_means"] == "no_active_countdown"


@pytest.mark.parametrize("model,key,value,expected", [
    ("c2000_gen2", "software_version_bms", "1.2.3.4", "1.2.3.4"),
    ("c1000_gen2", "software_version_module", "0.3.3.0", "0.3.3.0"),
    ("c1000_gen2", "software_version_inverter", "1.2.3.4", None),
    ("c1000", "software_version_module", "1.2.3.4", None),
    ("c2000_gen2", "software_version_bms", "private-identifier", None),
    ("c2000_gen2", "software_version_bms", 1234, None)])
def test_component_firmware_is_validated_text_diagnostic(platform, model, key, value, expected):
    sensor = entity(platform, key, model, value)
    assert sensor.native_value == expected
    assert sensor.entity_description.entity_category == "diagnostic"
    assert sensor.entity_description.state_class is None


@pytest.mark.parametrize("age,expected", [(1, -42), (600, None), (-6, None)])
def test_signal_has_independent_freshness(platform, age, expected):
    sensor = entity(platform, "wifi_rssi_dbm", "c1000_gen2", wifi_signal=report(observed_at=NOW-age))
    assert sensor.native_value == expected
    assert sensor.available is (expected is not None)
    assert sensor.extra_state_attributes["observed_at"] == NOW-age
    assert sensor.entity_description.device_class == "signal_strength"


def test_discovery_filters_impossible_ports_and_saved_timer_alias(platform):
    values = {"usb_a1_power_w": 0, "usb_c3_power_w": 15, "time_remaining_minutes": 60,
              "software_version_module": "0.3.3.0", "dc_output_timer_remaining_seconds": 10}
    snapshots = {model: snapshot(model=model, metrics=values) for model in ("c1000", "c300", "c1000_gen2", "c2000_gen2")}
    snapshots["c1000_gen2"]["wifi_signal"] = report()
    source = coordinator(snapshots)
    added = []
    entry = SimpleNamespace(runtime_data=source, async_on_unload=lambda _callback: None)
    asyncio.run(platform.sensor.async_setup_entry(None, entry, added.extend))
    roles = {(sensor.station_name, sensor.entity_description.key) for sensor in added}
    assert ("c300", "usb_c3_power_w") in roles and ("c1000", "usb_c3_power_w") not in roles
    assert ("c1000_gen2", "dc_output_timer_remaining_seconds") not in roles
    assert ("c1000_gen2", "wifi_rssi_dbm") in roles
    assert ("c2000_gen2", "wifi_rssi_dbm") not in roles


def test_api_keeps_new_values_and_discards_private_fields(platform):
    data = snapshot(model="c1000", metrics={"usb_a2_power_w": 0, "dc_input_power_w": 3,
        "time_remaining_minutes": "unknown", "serial_number": "private"})
    parsed = platform.api.parse_snapshot(data)
    assert parsed["metrics"] == {"usb_a2_power_w": 0, "dc_input_power_w": 3, "time_remaining_minutes": "unknown"}
