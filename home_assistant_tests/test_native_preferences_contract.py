"""Native preference guards; standalone contracts, not Home Assistant runtime."""

import importlib.util
import json
from pathlib import Path
import time

import pytest

BASE = Path(__file__).resolve().parents[1] / "custom_components/solix_link"
SPEC = importlib.util.spec_from_file_location("solix_native_preferences_api", BASE / "api.py")
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)

PREFERENCES = (("set-display-brightness", "level", 2, "display_brightness", 1),
               ("set-display-timeout", "seconds", 60, "display_timeout_seconds", 30),
               ("set-port-memory", "enabled", False, "port_memory_enabled", 1))


def test_original_prime_verified_setting_parity():
    snapshot = station(model="c1000", protocol="prime", controls=["set-charge-power", "set-display-brightness", "set-device-timeout",
                                                                 "set-display-timeout", "set-light", "set-temperature-unit"],
                       metrics={"display_brightness": 2, "device_timeout_minutes": 720, "ac_charging_power_limit_w": 1000,
                                "display_timeout_seconds": 30, "light_mode": 0, "temperature_unit_fahrenheit": 0})
    assert api.display_brightness_options(snapshot) == ["low", "medium", "high"]
    assert api.device_timeout_options(snapshot)[0] == "never"
    for payload in ({"command": "set-charge-power", "watts": 900},
                    {"command": "set-display-brightness", "level": 1},
                    {"command": "set-device-timeout", "minutes": 0},
                    {"command": "set-display-timeout", "seconds": 60},
                    {"command": "set-light", "mode": 1},
                    {"command": "set-temperature-unit", "fahrenheit": True}):
        api.validate_command(snapshot, payload)
    snapshot["controls"] = list(api.COMMANDS)  # Extra advertisements must not open unsupported switches.
    for payload in ({"command": "set-fast-charge", "enabled": True},
                    {"command": "set-ac-power-saving", "enabled": True},
                    {"command": "set-dc-power-saving", "enabled": True},
                    {"command": "set-port-memory", "enabled": True}):
        with pytest.raises(ValueError):
            api.validate_command(snapshot, payload)


@pytest.mark.parametrize("bad", [True, 0, 4, None, "2", 2.0])
def test_original_prime_brightness_needs_exact_valid_readback(bad):
    snapshot = station(model="c1000", protocol="prime", metrics={"display_brightness": bad})
    assert api.display_brightness_options(snapshot) == []
    with pytest.raises(ValueError):
        api.validate_command(snapshot, {"command": "set-display-brightness", "level": 1})


def station(**changes):
    data = {"name": "Station", "model": "c1000_gen2", "protocol": "native_mqtt", "connected": True,
            "available": True, "last_seen_timestamp": time.time(), "controls": list(api.COMMANDS),
            "metrics": {"display_brightness": 1, "display_timeout_seconds": 30, "port_memory_enabled": 1}}
    data.update(changes)
    return data


@pytest.mark.parametrize("command,field,value,metric,baseline", PREFERENCES)
def test_exact_native_c1000_preferences_pass_and_metrics_survive_filtering(command, field, value, metric, baseline):
    snapshot = api.parse_snapshot(station())
    assert snapshot["metrics"][metric] == baseline and command in snapshot["controls"]
    api.validate_command(snapshot, {"command": command, field: value})


@pytest.mark.parametrize("command,field,value,metric,baseline", PREFERENCES)
@pytest.mark.parametrize("model,protocol", [("c1000_gen2", "prime"), ("c2000_gen2", "native_mqtt"),
                                           ("c2000_gen2", "prime"), ("c1000", "legacy"), ("c300", "legacy")])
def test_native_preferences_reject_wrong_model_or_transport_even_if_advertised(command, field, value, metric, baseline, model, protocol):
    if model == "c2000_gen2" and command == "set-display-timeout":
        api.validate_command(station(model=model, protocol=protocol), {"command": command, field: value})
        return
    with pytest.raises(ValueError):
        api.validate_command(station(model=model, protocol=protocol), {"command": command, field: value})


@pytest.mark.parametrize("command,field,value,metric,baseline", PREFERENCES)
@pytest.mark.parametrize("bad", [None, True, "1", 1.0, 255])
def test_native_preferences_need_explicit_valid_integer_readback(command, field, value, metric, baseline, bad):
    data = station()
    data["metrics"][metric] = bad
    with pytest.raises(ValueError):
        api.validate_command(data, {"command": command, field: value})


@pytest.mark.parametrize("command,field,value,metric,baseline", PREFERENCES)
@pytest.mark.parametrize("changes", [{"controls": []}, {"available": False}, {"last_seen_timestamp": 0}])
def test_native_preferences_require_capability_and_freshness(command, field, value, metric, baseline, changes):
    with pytest.raises(ValueError):
        api.validate_command(station(**changes), {"command": command, field: value})


@pytest.mark.parametrize("level", [0, -1, 4, True, "2", 2.0])
def test_brightness_never_sends_display_off_or_raw_invalid_value(level):
    with pytest.raises(ValueError):
        api.validate_command(station(), {"command": "set-display-brightness", "level": level})


@pytest.mark.parametrize("seconds", [0, 10, 20, 30, 60, 300, 1800])
def test_screen_timeout_documented_choices(seconds):
    api.validate_command(station(), {"command": "set-display-timeout", "seconds": seconds})


def test_discrete_entity_translation_and_icon_keys_match():
    strings = json.loads((BASE / "strings.json").read_text())
    assert strings == json.loads((BASE / "translations/en.json").read_text())
    icons = json.loads((BASE / "icons.json").read_text())
    assert set(strings["entity"]["select"]["display_brightness"]["state"]) == set(api.DISPLAY_BRIGHTNESS_OPTIONS)
    assert set(strings["entity"]["select"]["display_timeout"]["state"]) == set(api.DISPLAY_TIMEOUT_OPTIONS)
    assert "port_memory" in strings["entity"]["switch"] and "port_memory" in icons["entity"]["switch"]
    assert set(api.display_brightness_options(station())) == {"low", "medium", "high"}
    assert api.display_timeout_options(station())[0] == "never"
