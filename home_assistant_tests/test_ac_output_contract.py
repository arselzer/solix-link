"""AC controls remain opt-in; confirmation and permission have distinct roles."""

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_gateway_contract import api, status
from test_recovery_contract import integration
from test_freshness_entities import NOW, coordinator, platform, snapshot


def power_status(**changes):
    value = status()
    value["metrics"]["software_version"] = "2.1.6.4"
    value["ac_power_control"] = {"supported": True, "enabled": True, "storage_valid": True,
        "restore_pending": False, "manageable": True}
    value.update(changes)
    return value


@pytest.mark.parametrize("command", ["set-ac-output", "cycle-ac-output"])
def test_exact_native_power_contract_and_unqualified_models(command):
    payload = {"command": command, "enabled": False, "acknowledge_power_interruption": True} if command == "set-ac-output" else {
        "command": command, "off_seconds": 20, "acknowledge_power_interruption": True, "controller_independent": True}
    api.validate_command(api.parse_snapshot(power_status()), payload)
    for changes in ({"model": "c1000_gen2"}, {"protocol": "prime"}, {"available": False}, {"ac_power_control": None}):
        with pytest.raises(ValueError): api.validate_command(api.parse_snapshot(power_status(**changes)), payload)
    for flag in ("enabled", "storage_valid"):
        value = power_status()
        value["ac_power_control"][flag] = False
        with pytest.raises(ValueError): api.validate_command(api.parse_snapshot(value), payload)
    assert api.request_deadline("POST", payload) == (105 if command == "set-ac-output" else 255)


def test_options_require_acknowledgment_and_send_only_permission_metadata(integration, monkeypatch):
    monkeypatch.setattr(integration.flow.selector, "SelectSelector", lambda config: config, raising=False)
    monkeypatch.setattr(integration.flow.selector, "SelectSelectorConfig", lambda **kwargs: kwargs, raising=False)
    client = SimpleNamespace(async_ac_output_permission=AsyncMock())
    data = {"station": power_status()}
    data["station"]["ac_power_control"]["enabled"] = False
    source = SimpleNamespace(client=client, data=data, async_request_refresh=AsyncMock())
    flow = integration.flow.SolixConfigFlow.async_get_options_flow(None)
    flow.config_entry = SimpleNamespace(options={}, runtime_data=source)
    form = asyncio.run(flow.async_step_init())
    assert next(marker.default for marker in form["data_schema"] if marker.key == "ac_output_devices") == []
    result = asyncio.run(flow.async_step_init({"ac_output_devices": ["station"]}))
    assert result["errors"]["base"] == "ac_acknowledgment_required"
    client.async_ac_output_permission.assert_not_awaited()
    result = asyncio.run(flow.async_step_init({"ac_output_devices": ["station"], "acknowledge_ac_interruption": True}))
    assert result["type"] == "create_entry"
    client.async_ac_output_permission.assert_awaited_once_with("station", True, acknowledge_high_impact=True)


def test_ac_switch_disabled_default_unavailable_when_locked_and_never_writes_on_discovery(platform, monkeypatch):
    module = ModuleType("homeassistant.components.switch")
    module.SwitchEntity = type("SwitchEntity", (), {})
    from test_freshness_entities import Description
    module.SwitchEntityDescription = Description
    monkeypatch.setitem(sys.modules, module.__name__, module)
    sys.modules["homeassistant.const"].EntityCategory.CONFIG = "config"
    exceptions = ModuleType("homeassistant.exceptions")
    exceptions.HomeAssistantError = type("HAError", (Exception,), {})
    monkeypatch.setitem(sys.modules, exceptions.__name__, exceptions)
    spec = importlib.util.spec_from_file_location("solix_freshness_contract.switch",
        Path(__file__).resolve().parents[1] / "custom_components/solix_link/switch.py")
    switches = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(switches)
    data = snapshot(metrics={"ac_output_enabled": 1, "software_version": "2.1.6.4"},
        controls=["set-ac-output"], ac_power_control={"supported": True, "enabled": False,
            "storage_valid": True, "restore_pending": False, "manageable": True})
    source = coordinator({"station": data})
    source.client.token = "synthetic-owner"
    source.async_command = AsyncMock()
    added = []
    entry = SimpleNamespace(runtime_data=source, async_on_unload=lambda callback: None)
    asyncio.run(switches.async_setup_entry(None, entry, added.extend))
    entity = next(item for item in added if isinstance(item, switches.SolixACOutputSwitch))
    assert entity._attr_entity_registry_enabled_default is False and not entity.available
    source.async_command.assert_not_awaited()
    with pytest.raises(exceptions.HomeAssistantError): asyncio.run(entity.async_turn_off())
    data["ac_power_control"]["enabled"] = True
    assert entity.available and entity.is_on
    asyncio.run(entity.async_turn_off())
    source.async_command.assert_awaited_once_with("station", {"command": "set-ac-output", "enabled": False,
        "acknowledge_power_interruption": True})
