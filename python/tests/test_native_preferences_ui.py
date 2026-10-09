"""Native C1000 preference interfaces use synthetic profiles and fake RPC."""

import asyncio
from dataclasses import asdict
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from solix_link import ap_service_cli, interactive
from solix_link.ap_service import APService
from solix_link.ap_service_config import APServiceConfig, private_write
from solix_link.ap_service_monitor import APServiceMonitor
from solix_link.cli import parser
from solix_link.commands import validate_command
from solix_link.protocol import Model
from solix_link.tui import Target, TuiBackend, controls_for, create_app


PREFERENCES = (
    ("set-display-brightness", "level", 2, "display_brightness", 1),
    ("set-display-timeout", "seconds", 60, "display_timeout_seconds", 30),
    ("set-port-memory", "enabled", False, "port_memory_enabled", 1),
)


@pytest.mark.parametrize("command,flag,value,field,expected", [
    ("ap-service-set-display-brightness", "--level", "2", "level", 2),
    ("ap-service-set-display-timeout", "--seconds", "0", "seconds", 0),
    ("ap-service-set-port-memory", "--enabled", "off", "enabled", False),
])
def test_cli_native_preferences_send_exact_rpc_fields(monkeypatch, tmp_path, capsys, command, flag, value, field, expected):
    request = AsyncMock(return_value={"metrics": {}})
    monkeypatch.setattr(ap_service_cli, "ap_service_request", request)
    args = parser().parse_args([command, "--directory", str(tmp_path), "--name", "office", flag, value])
    ap_service_cli.dispatch(args)
    request.assert_awaited_once_with(tmp_path, command.removeprefix("ap-service-"), name="office", **{field: expected})
    assert json.loads(capsys.readouterr().out) == {"metrics": {}}


@pytest.mark.parametrize("level", [0, -1, 4, 255])
def test_cli_brightness_refuses_off_and_invalid_raw_values(level):
    with pytest.raises(SystemExit):
        parser().parse_args(["ap-service-set-display-brightness", "--directory", "/tmp/example", "--level", str(level)])


@pytest.mark.parametrize("command,field,value,metric,baseline", PREFERENCES)
def test_unix_worker_routes_exact_typed_preferences(command, field, value, metric, baseline):
    async def run():
        method = {"set-display-brightness": "set_display_brightness", "set-display-timeout": "set_display_timeout",
                  "set-port-memory": "set_port_memory"}[command]
        config = APServiceConfig("office", "wlan_unused", "phy9", "AT", "A1763SYNTHETIC001", "a" * 40,
                                 model=Model.C1000_GEN2)
        mqtt = SimpleNamespace(config=config, **{method: AsyncMock(return_value={"metrics": {metric: int(value)}})})
        worker = object.__new__(APService)
        worker._clients = set()
        worker.stations = {"office": mqtt}
        worker.config = SimpleNamespace(name="office")
        reader = asyncio.StreamReader()
        reader.feed_data(json.dumps({"command": command, "name": "office", field: value}).encode() + b"\n")
        class Writer:
            body = b""
            def write(self, value): self.body += value
            async def drain(self): pass
            def close(self): pass
        writer = Writer()
        await worker._control(reader, writer)
        getattr(mqtt, method).assert_awaited_once_with(value)
        assert json.loads(writer.body) == {"ok": True, "result": {"metrics": {metric: int(value)}}}
    asyncio.run(run())


@pytest.mark.parametrize("command,field,value,metric,baseline", PREFERENCES)
@pytest.mark.parametrize("extra", [{"raw": 1}, {"enabled": "on"}, {"level": True}])
def test_preference_gateway_rejects_unknown_fields_and_wrong_types(command, field, value, metric, baseline, extra):
    with pytest.raises(ValueError):
        validate_command(command, {field: value, **extra})


@pytest.mark.parametrize("model", [Model.C1000_GEN2, Model.C2000_GEN2])
@pytest.mark.parametrize("enabled", [True, False])
def test_native_capabilities_keep_c2000_preferences_limited_to_screen_timeout(tmp_path, model, enabled):
    config = APServiceConfig("office", "wlan_unused", "phy9", "AT", "A1763SYNTHETIC001", "a" * 40, model=model)
    private_write(tmp_path / "status.json", json.dumps({"control_enabled": enabled}))
    controls = APServiceMonitor(config, tmp_path).supported_commands("office")
    for command, *_ in PREFERENCES:
        supported = model == Model.C1000_GEN2 or command == "set-display-timeout"
        assert (command in controls) is (enabled and supported)


@pytest.mark.parametrize("action,text,command,fields", [
    ("display-brightness", "2", "set-display-brightness", {"level": 2}),
    ("display-timeout", "60", "set-display-timeout", {"seconds": 60}),
    ("port-memory", "off", "set-port-memory", {"enabled": False}),
])
def test_tui_native_preference_handoff_and_stale_guard(tmp_path, action, text, command, fields):
    config = APServiceConfig("office", "wlan_unused", "phy9", "AT", "A1763SYNTHETIC001", "a" * 40, model=Model.C1000_GEN2)
    private_write(tmp_path / "ap_service.json", json.dumps(asdict(config)))
    async def run():
        state = {"connected": True, "available": True, "control_enabled": True, "last_seen_timestamp": time.time(),
                 "metrics": {"display_brightness": 1, "display_timeout_seconds": 30, "port_memory_enabled": 1}}
        request = AsyncMock(side_effect=lambda *_args, **_fields: state.copy())
        backend = TuiBackend([], tmp_path, requester=request)
        await backend.connect("native")
        await backend.control(action, text)
        assert request.await_args.args == (tmp_path, command) and request.await_args.kwargs == fields
        state["last_seen_timestamp"] = time.time() - 31
        await backend.refresh()
        count = request.await_count
        with pytest.raises(ValueError, match="Fresh"):
            await backend.control(action, text)
        assert request.await_count == count
        await backend.disconnect()
    asyncio.run(run())


@pytest.mark.parametrize("model,native", [(Model.C1000_GEN2, False), (Model.C2000_GEN2, True), (Model.C1000, False), (Model.C300, False)])
def test_new_brightness_and_memory_are_not_ble_or_other_model_controls(model, native):
    assert not {control.key for control in controls_for(Target("test", "Station", model, native))} & {"display-brightness", "port-memory"}


@pytest.mark.parametrize("responses,command,fields", [
    (["2", "2", "1"], "set-display-brightness", {"level": 2}),
    (["3", "5", "1"], "set-display-timeout", {"seconds": 60}),
    (["4", "1", "1"], "set-port-memory", {"enabled": False}),
])
def test_line_preferences_confirm_before_rpc(monkeypatch, tmp_path, responses, command, fields):
    replies = iter(responses)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(replies))
    request = AsyncMock(return_value={"metrics": {}})
    monkeypatch.setattr(interactive, "ap_service_request", request)
    interactive.preference_menu(SimpleNamespace(name="office", model=Model.C1000_GEN2), tmp_path, tmp_path / "ap")
    request.assert_awaited_once_with(tmp_path / "ap", command, name="office", **fields)


def test_tui_port_memory_cancel_and_confirm(tmp_path):
    pytest.importorskip("textual")
    from textual.widgets import Input, Select
    config = APServiceConfig("office", "wlan_unused", "phy9", "AT", "A1763SYNTHETIC001", "a" * 40, model=Model.C1000_GEN2)
    private_write(tmp_path / "ap_service.json", json.dumps(asdict(config)))
    async def run():
        state = {"connected": True, "available": True, "control_enabled": True, "last_seen_timestamp": time.time(),
                 "metrics": {"display_brightness": 1, "display_timeout_seconds": 30, "port_memory_enabled": 1}}
        calls = []
        async def request(_directory, command, **fields):
            calls.append((command, fields))
            return state.copy()
        backend = TuiBackend([], tmp_path, requester=request)
        app = create_app(backend=backend)
        async with app.run_test(size=(110, 38)) as pilot:
            await pilot.click("#connect")
            await pilot.pause()
            await pilot.press("f2")
            app.query_one("#setting", Select).value = "port-memory"
            await pilot.pause()
            app.query_one("#setting-value", Input).value = "off"
            await pilot.click("#apply-setting")
            await pilot.pause()
            assert app.screen.detail == "Off clears output-recovery bookkeeping; turning On does not restore that transient state."
            await pilot.click("#saving-cancel")
            await pilot.pause()
            assert not any(command == "set-port-memory" for command, _ in calls)
            await pilot.click("#apply-setting")
            await pilot.pause()
            await pilot.click("#saving-confirm")
            await pilot.pause()
            assert [fields for command, fields in calls if command == "set-port-memory"] == [{"enabled": False}]
    asyncio.run(run())
