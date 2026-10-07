"""Exercise real recovery logic with scoped HA doubles; no HA runtime/network.

These tests verify gateway configuration and coordinated I/O behavior. They do
not certify Home Assistant's own flow helpers or registry APIs for a release.
"""

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1] / "custom_components/solix_link"


class FlowAbort(Exception):
    def __init__(self, reason):
        self.reason = reason


class Entry:
    def __init__(self, entry_id, url, *, token="old-token", unique_id="stable-endpoint"):
        self.entry_id = entry_id
        self.data = {"url": url, "token": token}
        self.unique_id = unique_id

    @classmethod
    def __class_getitem__(cls, _item):
        return cls


class Flow:
    def __init_subclass__(cls, **_kwargs):
        pass

    def __init__(self):
        self.entry = None
        self.entries = []
        self.hass = SimpleNamespace()
        self.unique_id = None
        self.saved = []

    def _get_reauth_entry(self):
        return self.entry

    def _get_reconfigure_entry(self):
        return self.entry

    def _async_current_entries(self):
        return self.entries

    async def async_set_unique_id(self, unique_id):
        self.unique_id = unique_id

    def _abort_if_unique_id_configured(self):
        if any(entry.unique_id == self.unique_id for entry in self.entries):
            raise FlowAbort("already_configured")

    def _async_abort_entries_match(self, data):
        if any(all(entry.data.get(key) == value for key, value in data.items()) for entry in self.entries):
            raise FlowAbort("already_configured")

    def async_create_entry(self, *, title, data):
        self.saved.append(data)
        return {"type": "create_entry", "title": title, "data": data, "unique_id": self.unique_id}

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_abort(self, *, reason):
        return {"type": "abort", "reason": reason}

    def async_update_reload_and_abort(self, entry, *, data_updates, reason="reauth_successful"):
        entry.data = {**entry.data, **data_updates}
        self.saved.append(data_updates)
        return {"type": "abort", "reason": reason}


class Coordinator:
    @classmethod
    def __class_getitem__(cls, _item):
        return cls

    def __init__(self, *_args, **kwargs):
        self.config_entry = kwargs["config_entry"]
        self.data = None
        self.updates = []

    def async_set_updated_data(self, data):
        self.data = data
        self.updates.append(data)


class Marker:
    def __init__(self, key, *, default=None):
        self.key, self.default = key, default


@pytest.fixture
def integration(monkeypatch):
    """Keep fake HA modules out of other tests and the real package namespace."""
    def install(name, **values):
        module = ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
        return module

    install("voluptuous", Schema=lambda fields: fields, Required=Marker, Optional=Marker)
    install("homeassistant")
    install("homeassistant.config_entries", ConfigFlow=Flow, ConfigFlowResult=dict, ConfigEntry=Entry,
            OptionsFlowWithReload=Flow)
    install("homeassistant.core", HomeAssistant=object, callback=lambda function: function)
    install("homeassistant.exceptions", ConfigEntryAuthFailed=type("AuthFailed", (Exception,), {}),
            HomeAssistantError=type("HAError", (Exception,), {}))
    install("homeassistant.helpers")
    install("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda hass: None)
    install("homeassistant.helpers.selector", TextSelector=lambda config: config,
            TextSelectorConfig=lambda **kwargs: SimpleNamespace(**kwargs),
            TextSelectorType=SimpleNamespace(PASSWORD="password"))
    install("homeassistant.helpers.update_coordinator", DataUpdateCoordinator=Coordinator,
            UpdateFailed=type("UpdateFailed", (Exception,), {}))
    package_name = "solix_recovery_contract"
    package = install(package_name)
    package.__path__ = [str(ROOT)]

    def load(name):
        qualified = f"{package_name}.{name}"
        spec = importlib.util.spec_from_file_location(qualified, ROOT / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, qualified, module)
        spec.loader.exec_module(module)
        return module

    api = load("api")
    return SimpleNamespace(api=api, flow=load("config_flow"), coordinator=load("coordinator"))


def validated_flow(integration, monkeypatch, *, exception=None):
    calls = []

    class Client:
        def __init__(self, _session, url, token):
            calls.append((url, token))

        async def async_devices(self):
            if exception is not None:
                raise exception
            return {}

    monkeypatch.setattr(integration.flow, "GatewayClient", Client)
    flow = integration.flow.SolixConfigFlow()
    flow.entry = Entry("existing", "http://old.test")
    flow.entries = [flow.entry]
    return flow, calls


@pytest.mark.parametrize("previous, expected", [({}, False), ({"native_energy_enabled": True}, True),
    ({"native_energy_enabled": False}, False), ({"native_energy_enabled": "true"}, False)])
def test_energy_options_default_is_opt_in_and_uses_ha_reload_helper(integration, previous, expected):
    flow = integration.flow.SolixConfigFlow.async_get_options_flow(None)
    assert isinstance(flow, integration.flow.OptionsFlowWithReload)
    flow.config_entry = SimpleNamespace(options=previous)
    result = asyncio.run(flow.async_step_init())
    assert result["type"] == "form" and result["step_id"] == "init"
    marker, validator = next(iter(result["data_schema"].items()))
    assert marker.key == "native_energy_enabled" and marker.default is expected
    assert validator is bool


@pytest.mark.parametrize("enabled", [True, False])
def test_energy_options_preserve_unrelated_options_without_mutation_or_gateway_calls(integration, monkeypatch, enabled):
    def forbidden(*_args, **_kwargs):
        pytest.fail("An energy diagnostic option must not contact the gateway")

    monkeypatch.setattr(integration.flow, "GatewayClient", forbidden)
    previous = {"other_preference": "retained", "native_energy_enabled": not enabled}
    flow = integration.flow.SolixConfigFlow.async_get_options_flow(None)
    flow.config_entry = SimpleNamespace(options=previous)
    result = asyncio.run(flow.async_step_init({"native_energy_enabled": enabled}))
    assert result["data"] == {"other_preference": "retained", "native_energy_enabled": enabled}
    assert previous == {"other_preference": "retained", "native_energy_enabled": not enabled}


def test_reconfigure_keeps_gateway_identity_and_untouched_token(integration, monkeypatch):
    flow, calls = validated_flow(integration, monkeypatch)
    result = asyncio.run(flow.async_step_reconfigure({"url": "HTTP://NEW.test:80/"}))
    assert result == {"type": "abort", "reason": "reconfigure_successful"}
    assert calls == [("http://new.test", "old-token")]
    assert flow.entry.data == {"url": "http://new.test", "token": "old-token"}
    assert flow.entry.unique_id == "stable-endpoint"


def test_reconfigure_explicit_empty_token_removes_it(integration, monkeypatch):
    flow, calls = validated_flow(integration, monkeypatch)
    asyncio.run(flow.async_step_reconfigure({"url": "http://new.test", "token": ""}))
    assert calls == [("http://new.test", "")]
    assert flow.entry.data["token"] == ""


def test_reconfigure_refuses_another_configured_endpoint_without_mutation(integration, monkeypatch):
    flow, _calls = validated_flow(integration, monkeypatch)
    flow.entries.append(Entry("other", "http://other.test", unique_id="other-id"))
    before = dict(flow.entry.data)
    result = asyncio.run(flow.async_step_reconfigure({"url": "HTTP://OTHER.test:80/"}))
    assert result == {"type": "abort", "reason": "already_configured"}
    assert flow.entry.data == before and not flow.saved


@pytest.mark.parametrize("step", ["user", "reconfigure", "reauth_confirm"])
@pytest.mark.parametrize("kind,expected", [("auth", "invalid_auth"), ("read", "cannot_connect")])
def test_recovery_errors_do_not_update_configuration(integration, monkeypatch, step, kind, expected):
    exception = (integration.api.GatewayAuthError("private rejected token") if kind == "auth"
                 else integration.api.GatewayError("private endpoint failure"))
    flow, _calls = validated_flow(integration, monkeypatch, exception=exception)
    before = dict(flow.entry.data)
    result = asyncio.run(getattr(flow, f"async_step_{step}")({"url": "http://new.test", "token": "replacement"}))
    assert result["type"] == "form" and result["errors"] == {"base": expected}
    assert flow.entry.data == before and not flow.saved
    assert "private" not in str(result["errors"])


def test_reauth_changes_only_token_and_keeps_entity_identity(integration, monkeypatch):
    flow, calls = validated_flow(integration, monkeypatch)
    result = asyncio.run(flow.async_step_reauth_confirm({"token": " replacement "}))
    assert result == {"type": "abort", "reason": "reauth_successful"}
    assert calls == [("http://old.test", "replacement")]
    assert flow.entry.data == {"url": "http://old.test", "token": "replacement"}
    assert flow.entry.unique_id == "stable-endpoint"


@pytest.mark.parametrize("url", ["http://private:secret@gateway.test", "http://gateway.test:99999", "ftp://gateway.test"])
def test_invalid_endpoint_never_reads_or_updates_gateway(integration, monkeypatch, url):
    flow, calls = validated_flow(integration, monkeypatch)
    result = asyncio.run(flow.async_step_reconfigure({"url": url}))
    assert result["errors"] == {"base": "invalid_url"}
    assert not calls and not flow.saved


def test_new_flow_refuses_original_identity_after_address_reconfiguration(integration, monkeypatch):
    flow, _calls = validated_flow(integration, monkeypatch)
    flow.entry.unique_id = integration.api.gateway_id("http://old.test")
    flow.entry.data["url"] = "http://new.test"
    with pytest.raises(FlowAbort) as caught:
        asyncio.run(flow.async_step_user({"url": "http://old.test"}))
    assert caught.value.reason == "already_configured" and not flow.saved


def test_recovery_form_does_not_prefill_saved_token(integration, monkeypatch):
    flow, _calls = validated_flow(integration, monkeypatch)
    result = asyncio.run(flow.async_step_reconfigure())
    token = next(key for key in result["data_schema"] if key.key == "token")
    assert token.default is None


def test_coordinator_preserves_original_endpoint_identity(integration):
    entry = Entry("entry", "http://new.test")
    coordinator = integration.coordinator.SolixCoordinator(None, entry, SimpleNamespace(url="http://new.test"))
    assert coordinator.endpoint_id == entry.unique_id == "stable-endpoint"
    assert integration.api.device_id(coordinator.endpoint_id, "ups") == integration.api.device_id("stable-endpoint", "ups")


@pytest.mark.parametrize("kind", ["auth", "read"])
def test_poll_failure_maps_to_home_assistant_recovery(integration, kind):
    error = (integration.api.GatewayAuthError("rejected") if kind == "auth"
             else integration.api.GatewayError("unreachable"))

    async def devices():
        raise error

    coordinator = integration.coordinator.SolixCoordinator(None, Entry("entry", "http://gateway.test"),
        SimpleNamespace(url="http://gateway.test", async_devices=devices))
    expected = (sys.modules["homeassistant.exceptions"].ConfigEntryAuthFailed if kind == "auth"
                else sys.modules["homeassistant.helpers.update_coordinator"].UpdateFailed)
    with pytest.raises(expected):
        asyncio.run(coordinator._async_update_data())


@pytest.mark.parametrize("refresh_succeeds", [True, False])
def test_command_failure_refreshes_once_without_retry_and_preserves_peers(integration, refresh_succeeds):
    calls = []
    baseline = {"target": {"connected": True, "available": True, "metrics": {"value": 1}},
                "peer": {"connected": True, "available": True, "metrics": {"value": 3}}}

    async def command(name, payload):
        calls.append(("post", name))
        raise integration.api.GatewayCommandError("Settings may have changed")

    async def device(name):
        calls.append(("get", name))
        if not refresh_succeeds:
            raise integration.api.GatewayError("unreachable")
        return {"connected": True, "available": True, "metrics": {"value": 2}}

    coordinator = integration.coordinator.SolixCoordinator(None, Entry("entry", "http://gateway.test"),
        SimpleNamespace(url="http://gateway.test", async_command=command, async_device=device))
    coordinator.data = baseline
    with pytest.raises(sys.modules["homeassistant.exceptions"].HomeAssistantError):
        asyncio.run(coordinator.async_command("target", {"command": "set-display-timeout", "seconds": 30}))
    assert calls == [("post", "target"), ("get", "target")]
    assert coordinator.data["peer"] == baseline["peer"]
    assert coordinator.data["target"]["available"] is refresh_succeeds
    assert baseline["target"]["available"] is True
    assert len(coordinator.updates) == 1


def test_command_and_poll_io_are_serialized(integration):
    async def scenario():
        calls = []
        started, release = asyncio.Event(), asyncio.Event()

        async def command(name, payload):
            calls.append("post")
            started.set()
            await release.wait()
            return {"connected": True, "available": True}

        async def devices():
            calls.append("poll")
            return {"target": {"connected": True, "available": True}}

        async def history():
            calls.append("history")
            return None

        coordinator = integration.coordinator.SolixCoordinator(None, Entry("entry", "http://gateway.test"),
            SimpleNamespace(url="http://gateway.test", async_command=command, async_devices=devices,
                            async_history_summary=history))
        write = asyncio.create_task(coordinator.async_command("target", {}))
        await started.wait()
        poll = asyncio.create_task(coordinator._async_update_data())
        await asyncio.sleep(0)
        assert calls == ["post"]
        release.set()
        await asyncio.gather(write, poll)
        assert calls == ["post", "poll", "history"]

    asyncio.run(scenario())
