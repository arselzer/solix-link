"""Privacy and stale-state checks for the real diagnostics platform."""

import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1] / "custom_components/solix_link"
PACKAGE = "solix_diagnostics_contract"
package = ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
spec = importlib.util.spec_from_file_location(f"{PACKAGE}.diagnostics", ROOT / "diagnostics.py")
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)


def snapshot(**changes):
    result = {
        "name": "private bedroom name", "model": "c1000_gen2", "protocol": "native_mqtt",
        "connected": True, "available": True, "last_seen_timestamp": 999,
        "power_flow": "grid", "controls": ["set-charge-power", "set-ac-output"],
        "metrics": {"battery_percentage": 95, "temperature_c": 27, "software_version": "1.1.4.9",
                    "usage_mode": "standard", "active_tariff": "none"},
    }
    result.update(changes)
    return result


def test_diagnostics_omit_configured_names_identifiers_credentials_and_raw_data():
    raw = snapshot()
    raw.update(serial_number="private serial", account_id="private account", token="private token",
               url="http://private-gateway", raw_tlvs={"a2": "private bytes"}, error="private error")
    raw["metrics"].update(serial_number="private serial", ssid="private network", raw_tlv="private bytes")
    raw["history"] = {"name": "private name", "database_path": "private path",
                      "account_id": "private account", "lifetime_totals": {"token": "private token"}}
    report = diagnostics.diagnostics_report({"private map key": raw}, now=1000)
    text = json.dumps(report)
    assert "private" not in text
    assert report["station_count"] == 1
    assert report["stations"][0]["last_seen_age_seconds"] == 1
    assert report["stations"][0]["metrics"]["software_version"] == "1.1.4.9"
    assert report["stations"][0]["controls"] == ["set-charge-power"]
    assert "set-ac-output" not in text


@pytest.mark.parametrize("key", ["software_version", "usage_mode", "active_tariff", "battery_status",
                                 "battery_percentage", "temperature_c", "ac_output_frequency_setting_hz"])
def test_known_metric_names_cannot_carry_arbitrary_private_strings(key):
    raw = snapshot()
    raw["metrics"][key] = "private leaked credential"
    report = diagnostics.diagnostics_report({"station": raw}, now=1000)
    assert "private" not in json.dumps(report)
    assert key not in report["stations"][0]["metrics"]


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -float("inf"), {"private": 1}])
def test_non_numeric_values_are_not_exported_as_measurements(value):
    raw = snapshot()
    raw["metrics"]["temperature_c"] = value
    report = diagnostics.diagnostics_report({"station": raw}, now=1000)
    assert "temperature_c" not in report["stations"][0]["metrics"]
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("seen,available", [(999, True), (800, False), (None, False),
                                         (float("nan"), False), (1010, False)])
def test_cached_metrics_do_not_make_a_stale_station_available(seen, available):
    report = diagnostics.diagnostics_report({"station": snapshot(last_seen_timestamp=seen)}, now=1000)
    assert report["stations"][0]["available"] is available
    assert report["stations"][0]["metrics"]["battery_percentage"] == 95
    json.dumps(report, allow_nan=False)


def test_unknown_model_protocol_flow_and_controls_cannot_export_identifiers():
    raw = snapshot(model="private product name", protocol="private transport", power_flow="private source",
                   controls=["private command", {"private": "nested"}])
    report = diagnostics.diagnostics_report({"station": raw}, now=1000)
    assert "private" not in json.dumps(report)
    assert report["stations"][0]["model"] == "unknown"
    assert report["stations"][0]["controls"] == []


def test_platform_uses_cached_data_and_does_not_return_entry_or_client_secrets():
    coordinator = SimpleNamespace(data={"private name": snapshot()}, last_update_success=True,
                                  client=SimpleNamespace(token="private token", url="http://private-host"))
    entry = SimpleNamespace(runtime_data=coordinator, data={"token": "private token"},
                            title="private entry title", entry_id="private id")
    report = asyncio.run(diagnostics.async_get_config_entry_diagnostics(None, entry))
    assert "private" not in json.dumps(report)
    assert report["gateway_authenticated"] is True
    assert report["last_poll_success"] is True
    coordinator.data = None
    assert asyncio.run(diagnostics.async_get_config_entry_diagnostics(None, entry))["station_count"] == 0


def test_unloaded_or_failed_setup_exports_no_configuration_or_exception():
    entry = SimpleNamespace(data={"token": "private token", "url": "http://private-host"})
    report = asyncio.run(diagnostics.async_get_config_entry_diagnostics(None, entry))
    assert report == {"gateway_authenticated": None, "last_poll_success": False,
                      "station_count": 0, "stations": []}


def test_policy_diagnostics_export_only_fixed_status_values():
    raw=snapshot(price_policy={"phase":"blocked","owner":"surplus","reason":"manual_intervention_detected",
        "armed":True,"override":"hold","account_id":"private account","protected":{"private":"secret"}})
    report=diagnostics.diagnostics_report({"private map key":raw},now=1000)
    policy=report["stations"][0]["charging_policy"]
    assert policy==dict(phase="blocked",owner="surplus",reason="manual_intervention_detected",armed=True,override="hold")
    assert "private" not in json.dumps(report)
    raw["price_policy"]["reason"]="private error"
    assert "reason" not in diagnostics.diagnostics_report({"station":raw},now=1000)["stations"][0]["charging_policy"]
