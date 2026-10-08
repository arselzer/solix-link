"""Dashboard tests use synthetic status and fake transports only."""

import asyncio
import builtins
from dataclasses import asdict
import json
import time
from types import SimpleNamespace

import pytest

from solix_link.config import DeviceConfig, load_config, save_config
from solix_link.ap_service_config import APServiceConfig, private_write
from solix_link.protocol import Model
from solix_link.tui import (
    METRIC_LABELS, Target, TuiBackend, controls_for, create_app, parse_plan, public_snapshot, safe_error,
)


def station(model=Model.C300, client_id=None):
    return DeviceConfig("test_station", "AA:BB:CC:DD:EE:01", model, client_id)


class FakeMonitor:
    instances = []

    def __init__(self, *_args, **kwargs):
        self.connected = False
        self.callback = kwargs["on_update"]
        self.metrics = {"battery_percentage": 64, "ac_output_enabled": 0,
                        "display_timeout_seconds": 30, "serial_number": "PRIVATE_SYNTHETIC_SERIAL"}
        self.calls = []
        self.instances.append(self)

    async def connect(self, **_kwargs):
        self.connected = True
        self.callback(self.metrics)

    async def disconnect(self):
        self.connected = False

    async def request_status(self):
        self.calls.append(("status",))
        self.callback(self.metrics)

    async def wait_for_update(self, **_kwargs):
        return self.metrics.copy()

    async def set_ac_output_enabled(self, enabled):
        self.calls.append(("ac-output", enabled))
        self.metrics["ac_output_enabled"] = int(enabled)
        self.callback(self.metrics)

    async def set_display_timeout(self, value):
        self.calls.append(("display-timeout", value))
        self.metrics["display_timeout_seconds"] = value
        self.callback(self.metrics)

    async def set_ac_charging_power(self, value):
        self.calls.append(("charge-power", value))

    async def set_charge_cap(self, value):
        self.calls.append(("charge-cap", value))

    async def set_light_mode(self, value):
        self.calls.append(("light", value))

    async def set_charge_limits(self, upper, lower):
        self.calls.append(("charge-limits", upper, lower))


def test_c2000_has_no_ac_control_in_ui_or_backend():
    for native in (False, True):
        target = Target("test", "Test station", Model.C2000_GEN2, native)
        assert "ac-output" not in {item.key for item in controls_for(target)}
    async def run():
        backend = TuiBackend([station(Model.C2000_GEN2, "a" * 40)], monitor_factory=FakeMonitor)
        await backend.connect("ble:test_station")
        with pytest.raises(ValueError, match="unavailable"):
            await backend.control("ac-output", "off")
        assert backend.monitor.calls == []
        await backend.disconnect()
    asyncio.run(run())


@pytest.mark.parametrize("model,maximum", [(Model.C1000_GEN2, 1200), (Model.C2000_GEN2, 1800)])
def test_native_target_uses_initialized_profile_model_and_limits(tmp_path, model, maximum):
    config = APServiceConfig("ups", "wlan_unused", "phy9", "AT", "A1763SYNTHETIC001", "a" * 40,
                             model=model)
    private_write(tmp_path / "ap_service.json", json.dumps(asdict(config)))
    target = TuiBackend([], tmp_path).targets[0]
    assert target.native and target.model == model
    assert model.value in target.label
    controls = controls_for(target)
    minimum = 100 if model == Model.C1000_GEN2 else 300
    assert next(control for control in controls if control.key == "charge-power").hint == f"{minimum}–{maximum} W, in 100 W steps"
    expected = {"charge-power", "charge-cap", "reserve", "display-timeout"}
    if model == Model.C1000_GEN2:
        expected |= {"temperature-unit", "off-grid-alert", "discharge-floor", "device-timeout", "fast-charge",
                     "display-brightness", "display-timeout", "port-memory", "dc-power-saving", "ac-power-saving",
                     "clock-first-brightness", "clock-second-brightness"}
    assert {control.key for control in controls} == expected
    assert config.account_id not in target.label and config.device_serial not in target.label


def test_missing_mock_native_profile_retains_default_model(tmp_path):
    assert TuiBackend([], tmp_path).targets[0].model == Model.C2000_GEN2


@pytest.mark.parametrize("failure", ["malformed", "model", "permissions", "broken_symlink"])
def test_existing_invalid_native_profile_fails_without_leaking_secrets(tmp_path, failure):
    path = tmp_path / "ap_service.json"
    config = APServiceConfig("ups", "wlan_unused", "phy9", "AT", "A1763SYNTHETIC001", "a" * 40)
    fields = asdict(config)
    fields["passphrase"] = "SYNTHETIC_PRIVATE_PASSWORD"
    if failure == "broken_symlink":
        path.symlink_to(tmp_path / "missing_private_profile")
    else:
        if failure == "model":
            fields["model"] = "SYNTHETIC_PRIVATE_MODEL"
        private_write(path, "SYNTHETIC_PRIVATE_PASSWORD{" if failure == "malformed" else json.dumps(fields))
        if failure == "permissions":
            path.chmod(0o644)
    with pytest.raises(RuntimeError, match="profile is invalid or unreadable") as error:
        TuiBackend([], tmp_path)
    assert "SYNTHETIC_PRIVATE" not in str(error.value)
    assert config.account_id not in str(error.value) and config.device_serial not in str(error.value)


def test_unreadable_native_profile_does_not_choose_a_default_model(tmp_path, monkeypatch):
    from solix_link import ap_service_config
    private_write(tmp_path / "ap_service.json", "{}")
    def unreadable(_path):
        raise PermissionError("SYNTHETIC_PRIVATE_PASSWORD")
    monkeypatch.setattr(ap_service_config, "load_ap_service", unreadable)
    with pytest.raises(RuntimeError, match="profile is invalid or unreadable") as error:
        TuiBackend([], tmp_path)
    assert "SYNTHETIC_PRIVATE" not in str(error.value)


def test_ble_control_and_disconnect_preserve_secrets():
    async def run():
        backend = TuiBackend([station()], monitor_factory=FakeMonitor)
        snapshot = await backend.connect("ble:test_station")
        assert snapshot["available"]
        assert "serial_number" not in snapshot["metrics"]
        await backend.control("ac-output", "on")
        await backend.control("display-timeout", "30")
        assert backend.monitor.calls == [("ac-output", True), ("display-timeout", 30)]
        monitor = backend.monitor
        await backend.disconnect()
        assert not monitor.connected and backend.target is None
    asyncio.run(run())


def test_unpaired_gen2_is_rejected_before_opening_bluetooth():
    async def run():
        backend = TuiBackend([station(Model.C1000_GEN2)], monitor_factory=lambda *_a, **_k: pytest.fail("No Bluetooth"))
        with pytest.raises(ValueError, match="Pair this station first"):
            await backend.connect("ble:test_station")
        assert backend.target is None
    asyncio.run(run())


def test_native_controls_use_only_local_socket_actions(tmp_path):
    async def run():
        calls = []
        async def request(directory, command, **fields):
            assert directory == tmp_path
            calls.append((command, fields))
            return {"connected": True, "available": True, "control_enabled": True, "power_flow": "grid", "metrics": {}}
        backend = TuiBackend([], tmp_path, requester=request)
        await backend.connect("native")
        await backend.control("reserve", "25")
        await backend.control("charge-power", "800")
        await backend.control("charge-cap", "90")
        await backend.control("plan", "off_peak:0:6,peak:6:24", enabled=True)
        await backend.control("return-grid")
        assert calls == [
            ("status", {}), ("set-backup-reserve", {"reserve": 25}),
            ("set-charge-power", {"watts": 800}), ("set-charge-cap", {"upper": 90}),
            ("set-tou-plan", {"periods": [
                {"tariff": "off_peak", "start_hour": 0, "end_hour": 6},
                {"tariff": "peak", "start_hour": 6, "end_hour": 24},
            ], "enabled": True}), ("return-grid", {"timeout": 30}),
        ]
        with pytest.raises(ValueError):
            await backend.control("plan", "peak:0:18,off_peak:6:24")
        assert len(calls) == 6
        await backend.disconnect()
        assert len(calls) == 6  # Closing UI never changes a plan or stops the ap_service.
    asyncio.run(run())


def test_c1000_native_boolean_controls_parse_explicit_choices(tmp_path):
    config = APServiceConfig("ups", "wlan_unused", "phy9", "AT", "A1763SYNTHETIC001", "a" * 40,
                             model=Model.C1000_GEN2)
    private_write(tmp_path / "ap_service.json", json.dumps(asdict(config)))
    async def run():
        calls = []
        async def request(_directory, command, **fields):
            calls.append((command, fields))
            return {"control_enabled": True, "metrics": {}}
        backend = TuiBackend([], tmp_path, requester=request)
        await backend.connect("native")
        await backend.control("temperature-unit", "fahrenheit")
        await backend.control("temperature-unit", "celsius")
        await backend.control("off-grid-alert", "on")
        await backend.control("off-grid-alert", "off")
        assert calls == [("status", {}), ("set-temperature-unit", {"fahrenheit": True}),
                         ("set-temperature-unit", {"fahrenheit": False}),
                         ("set-off-grid-alert", {"enabled": True}),
                         ("set-off-grid-alert", {"enabled": False})]
        for action, bad in (("temperature-unit", "1"), ("off-grid-alert", "true")):
            with pytest.raises(ValueError):
                await backend.control(action, bad)
        assert len(calls) == 5
        assert "temperature-unit" not in {c.key for c in controls_for(Target("ble", "test", Model.C1000_GEN2))}
    asyncio.run(run())


@pytest.mark.parametrize("action,expected", [
    ("ac-power-saving", {"command": "set-ac-power-saving"}),
    ("clock-first-brightness", {"command": "set-clock-brightness", "window": 1}),
    ("clock-second-brightness", {"command": "set-clock-brightness", "window": 2}),
])
@pytest.mark.parametrize("choice", ["on", "off"])
def test_gen2_native_clock_and_ac_smart_tui_dispatch(tmp_path, action, expected, choice):
    config = APServiceConfig("ups", "wlan_unused", "phy9", "AT", "A1763SYNTHETIC001", "a" * 40,
                             model=Model.C1000_GEN2)
    private_write(tmp_path / "ap_service.json", json.dumps(asdict(config)))
    async def run():
        calls = []
        async def request(_directory, command, **fields):
            calls.append((command, fields))
            return {"control_enabled": True, "connected": True, "available": True,
                    "last_seen_timestamp": time.time(), "metrics": {
                        "software_version": "1.1.4.9", "ac_output_enabled": 0,
                        "ac_power_saving_mode_enabled": 0, "usage_mode": "standard", "active_tariff": "none",
                        "clock_screen_enabled": 0, "clock_screen_transfer_status_raw": 0,
                        "clock_screen_first_brightness_flag_raw": 0, "clock_screen_second_brightness_flag_raw": 0,
                        "ac_output_timeout_seconds": 0, "dc_output_timeout_seconds": 0}}
        backend = TuiBackend([], tmp_path, requester=request)
        await backend.connect("native")
        await backend.control(action, choice)
        fields = {k: v for k, v in expected.items() if k != "command"}
        fields["enabled" if action == "ac-power-saving" else "high"] = choice == "on"
        assert calls == [("status", {}), (expected["command"], fields)]
        unsafe = "ac_output_enabled" if action == "ac-power-saving" else "clock_screen_transfer_status_raw"
        backend.native_snapshot["metrics"][unsafe] = 1
        with pytest.raises(ValueError):
            await backend.control(action, choice)
        assert len(calls) == 2
        await backend.disconnect()
    asyncio.run(run())


@pytest.mark.parametrize("text", ["peak:23:2", "cheap:0:24", "peak:0:12,off_peak:6:24", "peak:0.5:12", "peak:0", "peak:0:6,"])
def test_invalid_hourly_plans_are_rejected(text):
    with pytest.raises(ValueError):
        parse_plan(text)


def test_empty_plan_clears_and_secret_fields_are_not_rendered():
    assert parse_plan(" ") == []
    result = public_snapshot({"address": "AA:BB:CC:DD:EE:01", "account_id": "a" * 40,
                              "metrics": {"battery_percentage": 40, "serial_number": "EXAMPLESECRET12345"}})
    assert result["metrics"] == {"battery_percentage": 40}
    assert "address" not in result and "account_id" not in result
    error = safe_error(RuntimeError("Connection to AA:BB:CC:DD:EE:01 failed for EXAMPLESECRET12345"))
    assert "AA:BB" not in error and "EXAMPLESECRET" not in error
    assert "Connection" in error


def test_optional_dependency_error_is_actionable(monkeypatch):
    original = builtins.__import__
    def import_without_textual(name, *args, **kwargs):
        if name == "textual" or name.startswith("textual."):
            raise ImportError("No Textual")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", import_without_textual)
    with pytest.raises(RuntimeError, match=r"solix-link\[tui\]"):
        create_app(backend=TuiBackend([]))


def test_headless_dashboard_connect_control_and_narrow_layout():
    pytest.importorskip("textual")
    from textual.widgets import Input, Select
    async def run():
        backend = TuiBackend([station()], monitor_factory=FakeMonitor)
        app = create_app(backend=backend)
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.click("#connect")
            await pilot.pause()
            assert backend.monitor.connected
            from textual.widgets import TabbedContent
            app.query_one("#tabs", TabbedContent).active = "controls"
            await pilot.pause()
            app.query_one("#setting", Select).value = "display-timeout"
            await pilot.pause()
            app.query_one("#setting-value", Input).value = "30"
            await pilot.click("#apply-setting")
            await pilot.pause()
            assert backend.monitor.calls == [("display-timeout", 30)]
            await pilot.resize_terminal(70, 28)
            await pilot.pause()
            assert app.has_class("narrow")
            assert app.query_one("#station").size.width <= 70
            assert app.has_class("compact")
            assert app.query_one("#compact-summary").size.height >= 1
            assert app.query_one("#connect").region.y == app.query_one("#disconnect").region.y
            app.query_one("#apply-setting").scroll_visible(animate=False)
            await pilot.pause()
            await pilot.click("#apply-setting")
            await pilot.pause()
            assert backend.monitor.calls == [("display-timeout", 30), ("display-timeout", 30)]
            await app.action_quit()
        assert backend.monitor is None
    asyncio.run(run())


def test_headless_native_plan_is_explicit_and_no_ac_button(tmp_path):
    pytest.importorskip("textual")
    from textual.widgets import Input, Select, TabbedContent
    async def run():
        calls = []
        async def request(_directory, command, **fields):
            calls.append((command, fields))
            return {"connected": True, "available": True, "control_enabled": True, "last_seen_timestamp": time.time(),
                    "power_flow": "grid", "metrics": {"battery_percentage": 80, "ac_output_enabled": 1}}
        backend = TuiBackend([], tmp_path, requester=request)
        app = create_app(backend=backend)
        async with app.run_test(size=(100, 42)) as pilot:
            assert calls == []
            await pilot.click("#connect")
            await pilot.pause()
            assert "ac-output" not in {item.key for item in controls_for(backend.target)}
            app.query_one("#tabs", TabbedContent).active = "plan"
            await pilot.pause()
            app.query_one("#plan-text", Input).value = "peak:0:24"
            app.query_one("#plan-mode", Select).value = "activate"
            assert all(command == "status" for command, _fields in calls)
            await pilot.click("#apply-plan")
            await pilot.pause()
            assert ("set-tou-plan", {"periods": [{"tariff": "peak", "start_hour": 0, "end_hour": 24}], "enabled": True}) in calls
            await app.action_quit()
        assert not any(command == "return-grid" for command, _fields in calls)
    asyncio.run(run())


def test_headless_quit_does_not_cancel_an_active_control():
    pytest.importorskip("textual")
    async def run():
        started, finish = asyncio.Event(), asyncio.Event()
        class SlowMonitor(FakeMonitor):
            async def set_display_timeout(self, value):
                started.set()
                await finish.wait()
                await super().set_display_timeout(value)
        backend = TuiBackend([station()], monitor_factory=SlowMonitor)
        app = create_app(backend=backend)
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.click("#connect")
            await pilot.pause()
            app.launch(backend.control("display-timeout", "30"), control=True)
            await started.wait()
            await app.action_quit()
            assert backend.monitor.connected and app.busy
            finish.set()
            await pilot.pause()
            assert not app.busy
            await app.action_quit()
    asyncio.run(run())


def test_native_read_only_worker_disables_controls(tmp_path):
    pytest.importorskip("textual")
    from textual.widgets import Button
    async def run():
        calls = []
        async def request(_directory, command, **_fields):
            calls.append(command)
            return {"connected": True, "available": True, "control_enabled": False, "metrics": {}}
        backend = TuiBackend([], tmp_path, requester=request)
        app = create_app(backend=backend)
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.click("#connect")
            await pilot.pause()
            for name in ("apply-setting", "apply-plan", "return-grid"):
                assert app.query_one(f"#{name}", Button).disabled
            with pytest.raises(RuntimeError, match="controls are disabled"):
                await backend.control("reserve", "25")
            assert calls == ["status"]
            await app.action_quit()
    asyncio.run(run())


def test_scan_merges_without_replacing_saved_pairing_ids(tmp_path):
    async def run():
        saved = station(Model.C2000_GEN2, "a" * 40)
        config = tmp_path / "config.json"
        save_config([saved], config)
        original = config.read_bytes()
        async def scan(**_kwargs):
            return [SimpleNamespace(name="Anker A1783", address=saved.address.lower()),
                    SimpleNamespace(name="Anker SOLIX C300X", address="AA:BB:CC:DD:EE:02"),
                    SimpleNamespace(name="Anker A1761", address="AA:BB:CC:DD:EE:03")]
        backend = TuiBackend([saved], config_path=config, scanner=scan, monitor_factory=FakeMonitor)
        assert await backend.scan() == 2
        assert await backend.scan() == 0
        assert config.read_bytes() == original  # Discovery never saves implicitly.
        assert backend.targets[0].device.client_id == "a" * 40
        target = next(t for t in backend.targets if t.model == Model.C300)
        assert not target.saved and target.device.name == "c300"
        assert saved.address not in " ".join(t.label for t in backend.targets)
        await backend.connect(target.key)
        assert backend.monitor.connected
        result = await backend.save_target(target.key)
        assert result.saved and backend.target == result
        assert load_config(config) == [saved, result.device]
        assert config.stat().st_mode & 0o777 == 0o600
        await backend.disconnect()
    asyncio.run(run())


def test_scan_names_are_stable_and_saving_retains_newer_config_entries(tmp_path):
    async def run():
        first = DeviceConfig("c300", "AA:BB:CC:DD:EE:01", Model.C300)
        config = tmp_path / "config.json"
        save_config([first], config)
        async def scan(**_kwargs):
            return [SimpleNamespace(name="Anker A1723", address="AA:BB:CC:DD:EE:02")]
        backend = TuiBackend([first], config_path=config, scanner=scan)
        await backend.scan()
        new = backend.targets[-1]
        assert new.device.name == "c300_2"
        later = DeviceConfig("other_station", "AA:BB:CC:DD:EE:03", Model.C1000)
        save_config([first, later], config)
        result = await backend.save_target(new.key)
        assert result.device.name == "c300_2"
        assert load_config(config) == [first, later, result.device]
    asyncio.run(run())


def test_headless_scan_from_empty_dashboard_connects_without_pairing(tmp_path):
    pytest.importorskip("textual")
    from textual.widgets import Button, Select
    async def run():
        entered, finish = asyncio.Event(), asyncio.Event()
        async def scan(**_kwargs):
            entered.set()
            await finish.wait()
            return [SimpleNamespace(name="Anker A1723", address="AA:BB:CC:DD:EE:04")]
        config = tmp_path / "config.json"
        backend = TuiBackend([], config_path=config, scanner=scan, monitor_factory=FakeMonitor)
        app = create_app(backend=backend)
        async with app.run_test(size=(100, 44)) as pilot:
            assert app.query_one("#connect", Button).disabled
            await pilot.click("#scan")
            await entered.wait()
            assert app.busy and not config.exists()
            # The event loop remains responsive while discovery is in progress.
            await pilot.pause()
            finish.set()
            await pilot.pause()
            assert app.query_one("#station", Select).value == "ble:c300"
            await pilot.click("#save-station")
            await pilot.pause()
            assert load_config(config)[0].name == "c300"
            await pilot.click("#connect")
            await pilot.pause()
            assert backend.monitor.connected
            assert backend.monitor.calls == []  # Connecting never changes settings.
            await app.action_quit()
    asyncio.run(run())


def test_scanned_gen2_requires_existing_pairing_workflow(tmp_path):
    async def run():
        async def scan(**_kwargs):
            return [SimpleNamespace(name="Anker A1783", address="AA:BB:CC:DD:EE:05")]
        backend = TuiBackend([], scanner=scan, monitor_factory=lambda *_a, **_k: pytest.fail("No pairing bypass"))
        await backend.scan()
        with pytest.raises(ValueError, match="confirm its main button"):
            await backend.connect(backend.targets[0].key)
    asyncio.run(run())


def test_headless_keyboard_navigation_preserves_fixed_rows_and_explicit_writes():
    pytest.importorskip("textual")
    from textual.widgets import Input, Select, TabbedContent
    async def run():
        backend = TuiBackend([station()], monitor_factory=FakeMonitor)
        app = create_app(backend=backend)
        async with app.run_test(size=(100, 40)) as pilot:
            connection_y = app.query_one("#connection").region.y
            status_y = app.query_one("#connection-status").region.y
            await pilot.press("ctrl+o")
            await pilot.pause()
            assert backend.monitor.connected and backend.monitor.calls == []
            await pilot.press("f2")
            await pilot.pause()
            assert app.query_one("#tabs", TabbedContent).active == "controls"
            assert app.focused == app.query_one("#setting", Select)
            app.query_one("#setting", Select).value = "display-timeout"
            await pilot.pause()
            app.query_one("#setting-value", Input).focus()
            await pilot.press("6", "0")
            assert backend.monitor.calls == []  # Editing never sends commands.
            await pilot.press("f3")
            await pilot.pause()
            app.query_one("#plan-scroll").scroll_end(animate=False)
            await pilot.pause()
            assert app.query_one("#connection").region.y == connection_y
            assert app.query_one("#connection-status").region.y == status_y
            await pilot.press("f4")
            await pilot.pause()
            assert app.query_one("#tabs", TabbedContent).active == "events"
            await pilot.press("f10")
            await pilot.pause()
            assert app.screen.__class__.__name__ == "HelpScreen"
            await pilot.press("escape")
            await pilot.pause()
            assert app.screen.__class__.__name__ != "HelpScreen"
            await pilot.press("ctrl+d")
            await pilot.pause()
            assert backend.monitor is None
            await app.action_quit()
    asyncio.run(run())


def test_live_refresh_keeps_table_selection_and_scroll_position():
    pytest.importorskip("textual")
    from textual.widgets import DataTable
    async def run():
        app = create_app(backend=TuiBackend([]))
        async with app.run_test(size=(100, 34)) as pilot:
            snapshot = {"available": True, "connected": True,
                        "metrics": {key: 10 for key in METRIC_LABELS}}
            app.render_snapshot(snapshot)
            table = app.query_one("#readings", DataTable)
            table.move_cursor(row=20, column=0)
            await pilot.pause()
            scroll = table.scroll_y
            assert scroll > 0
            snapshot["metrics"]["battery_percentage"] = 11
            app.render_snapshot(snapshot)
            await pilot.pause()
            assert table.cursor_row == 20 and table.scroll_y == scroll
            assert table.get_cell("battery_percentage", "value") == "11"
            assert app.query_one("#body").scroll_y == 0
            await app.action_quit()
    asyncio.run(run())


def test_rapid_panel_switch_does_not_restore_deferred_focus_to_old_panel():
    pytest.importorskip("textual")
    from textual.widgets import RichLog, TabbedContent, TabPane
    async def run():
        app = create_app(backend=TuiBackend([]))
        async with app.run_test(size=(100, 40)) as pilot:
            app.action_panel("controls")
            app.action_panel("plan")
            app.action_panel("events")
            await pilot.pause()
            tabs = app.query_one("#tabs", TabbedContent)
            assert tabs.active == "events"
            assert app.focused == app.query_one("#event-log", RichLog)
            # A queued focus message can outlive the pane that generated it.
            tabs.post_message(TabPane.Focused(app.query_one("#plan", TabPane)))
            await pilot.pause()
            assert tabs.active == "events"
            await app.action_quit()
    asyncio.run(run())


def test_small_terminal_keeps_workspace_and_navigation_inside_screen():
    pytest.importorskip("textual")
    from textual.widgets import Footer, TabbedContent
    async def run():
        app = create_app(backend=TuiBackend([station()], monitor_factory=FakeMonitor))
        async with app.run_test(size=(45, 24)) as pilot:
            assert app.has_class("narrow") and app.has_class("compact")
            tabs = app.query_one("#tabs", TabbedContent)
            footer = app.query_one(Footer)
            assert tabs.region.bottom <= footer.region.y
            assert tabs.size.height >= 6
            await pilot.press("f2")
            await pilot.pause()
            app.query_one("#controls-scroll").scroll_end(animate=False)
            await pilot.pause()
            assert app.query_one("#body").scroll_y == 0
            assert app.query_one("#connection-status").region.bottom <= tabs.region.y
            await app.action_quit()
    asyncio.run(run())
