"""Read-only terminal gateway/history/preview checks with synthetic clients."""

import asyncio
from copy import deepcopy
import json
from threading import Event
import time

import pytest

from solix_link import cli, interactive
from solix_link.tui import TuiBackend, controls_for, create_app, public_snapshot
from test_charging_policy import request, station
from test_gateway_client import saved_history


class FakeGateway:
    name = "Demo · C1000 Gen 2"

    def __init__(self):
        self.calls = []
        self.timestamp = time.time()

    def devices(self):
        self.calls.append(("devices",))
        return [self.snapshot(self.name)]

    def snapshot(self, name):
        self.calls.append(("snapshot", name))
        assert name == self.name
        value = station()
        value.update(name=name, last_seen_timestamp=self.timestamp, power_flow="grid", control_enabled=True,
                     controls=["set-ac-output", "set-charge-power"], client_id="PRIVATE-PAIRING-ID")
        value["metrics"].update(ac_input_power_w=340, ac_output_power_w=120, serial_number="PRIVATE-SERIAL")
        return value

    def history(self, name, **fields):
        self.calls.append(("history", name, fields))
        return saved_history(name)


def test_energy_panel_reads_only_cached_snapshot_and_shows_epoch():
    pytest.importorskip("textual")
    from textual.widgets import DataTable, Static, TabbedContent
    from solix_link.energy_store import NativeEnergyStore
    from solix_link.energy_report import REPORT_NAME

    class EnergyGateway(FakeGateway):
        def snapshot(self, name):
            result = super().snapshot(name)
            report = {"protobuf_name": REPORT_NAME, "units_verified": False,
                      "groups": {"standard": {"ac_input_energy_raw": 1250, "ac_output_duration_raw": 4}}}
            result["native_energy"] = NativeEnergyStore("c1000_gen2").ingest([report],
                reported_at=self.timestamp, firmware_version="1.1.4.9")
            return result

    async def run():
        client = EnergyGateway()
        backend = backend_for(client)
        app = create_app(backend=backend)
        async with app.run_test(size=(110, 44)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+o")
            await pilot.pause()
            before = len(client.calls)
            await pilot.press("f7")
            await pilot.pause()
            assert app.query_one("#tabs", TabbedContent).active == "energy"
            table = app.query_one("#energy-table", DataTable)
            assert table.row_count == 2
            assert any("1.250000" in table.get_row_at(index) for index in range(table.row_count))
            assert "epoch 1" in str(app.query_one("#energy-summary", Static).render())
            assert len(client.calls) == before
    asyncio.run(run())


def backend_for(client):
    def prohibited(*_args, **_kwargs):
        pytest.fail("Gateway target must not activate BLE, AP worker or writes")
    return TuiBackend([], gateway_client=client, monitor_factory=prohibited, requester=prohibited, scanner=prohibited)


def policy_file(tmp_path):
    body = request()
    now = time.time()
    body["config"]["latch_changed_at"] = now - 600
    body["signals"]["price"]["timestamp"] = now
    path = tmp_path / "synthetic-policy.json"
    path.write_text(json.dumps(body))
    return path


def log_text(widget):
    return "\n".join(line.text for line in widget.lines)


def test_gateway_backend_has_no_transport_fallback_or_control_capability():
    async def run():
        client = FakeGateway()
        backend = backend_for(client)
        assert await backend.load_gateway() == 1
        target = backend.targets[0]
        assert target.gateway and not target.native and target.device is None
        assert controls_for(target) == ()
        snapshot = await backend.connect(target.key)
        assert "PRIVATE" not in repr(snapshot)
        assert backend.preview_snapshot(snapshot)["protocol"] == "native_mqtt"
        await backend.refresh(force=True)
        for action in ("charge-power", "ac-output", "plan", "return-grid"):
            with pytest.raises(PermissionError, match="read only"):
                await backend.control(action, "on")
        await backend.saved_history(target.key, 24)
        await backend.disconnect()
        assert backend.target is None and backend.gateway_snapshot == {}
        assert {call[0] for call in client.calls} == {"devices", "snapshot", "history"}
    asyncio.run(run())


def test_saved_plan_load_is_local_and_expires_independently():
    pytest.importorskip("textual")
    from textual.widgets import Button, Input, TabbedContent

    class PlannedGateway(FakeGateway):
        def snapshot(self, name):
            result = super().snapshot(name)
            result["tou_plan_readback"] = dict(schema_version=1, enabled=False, reported_at=self.timestamp,
                source="status_d9", periods=[dict(tariff="peak", start_hour=6, end_hour=24)])
            return result

    async def run():
        client = PlannedGateway()
        backend = backend_for(client)
        app = create_app(backend=backend)
        async with app.run_test(size=(110, 44)) as pilot:
            await pilot.pause()
            app.action_connect()
            await pilot.pause()
            await app.workers.wait_for_complete()
            app.query_one(TabbedContent).active = "plan"
            await pilot.pause()
            load = app.query_one("#load-saved-plan", Button)
            assert not load.disabled
            await pilot.click("#load-saved-plan")
            await pilot.pause()
            assert app.query_one("#plan-text", Input).value == "peak:6:24"
            assert app.query_one("#apply-plan", Button).disabled
            assert {call[0] for call in client.calls} <= {"devices", "snapshot"}
            app.snapshot["tou_plan_readback"]["reported_at"] = time.time()-31
            app.snapshot["last_seen_timestamp"] = time.time()
            app.render_snapshot(app.snapshot)
            assert app.query_one("#load-saved-plan", Button).disabled
    asyncio.run(run())


def test_headless_history_and_manual_preview_are_fixed_and_read_only(tmp_path):
    pytest.importorskip("textual")
    from textual.widgets import Button, DataTable, Input, RichLog, TabbedContent
    async def run():
        client = FakeGateway()
        backend = backend_for(client)
        app = create_app(backend=backend)
        path = policy_file(tmp_path)
        before = path.read_bytes()
        async with app.run_test(size=(110, 42)) as pilot:
            await pilot.pause()
            assert app.selected == "gateway:" + client.name
            fixed_y = app.query_one("#connection").region.y
            await pilot.press("ctrl+o")
            await pilot.pause()
            assert app.query_one("#apply-setting", Button).disabled
            assert app.query_one("#apply-plan", Button).disabled
            await pilot.press("f5")
            await pilot.pause()
            assert app.query_one("#tabs", TabbedContent).active == "history"
            app.action_history_load()
            await pilot.pause()
            assert app.query_one("#history-table", DataTable).row_count == 1
            await pilot.press("f6")
            await pilot.pause()
            app.query_one("#preview-file", Input).value = str(path)
            await pilot.pause()
            app.action_preview()
            await pilot.pause()
            result = log_text(app.query_one("#preview-result", RichLog))
            assert "OPPORTUNITY" in result and "Commands sent: 0" in result
            assert "PRIVATE" not in result and path.read_bytes() == before
            app.query_one("#preview-price", Input).value = "0.5"
            app.query_one("#preview-price-age", Input).value = "0"
            await pilot.pause()
            assert "OPPORTUNITY" not in log_text(app.query_one("#preview-result", RichLog))
            app.action_preview()
            await pilot.pause()
            assert "IDLE" in log_text(app.query_one("#preview-result", RichLog))
            app.query_one("#preview-scroll").scroll_end(animate=False)
            await pilot.pause()
            assert app.query_one("#connection").region.y == fixed_y
            assert all(call[0] in ("devices", "snapshot", "history") for call in client.calls)
            await app.action_quit()
    asyncio.run(run())


def test_terminal_adaptive_selector_renders_candidate_plan_without_transport_writes(tmp_path):
    pytest.importorskip("textual")
    from textual.widgets import Input, RichLog, Select
    from test_adaptive_policy import request as adaptive_request
    class AdaptiveGateway(FakeGateway):
        def snapshot(self, name):
            result = super().snapshot(name)
            result["metrics"].update(tou_schedule_slot_count=0, backup_reserve_percentage=20)
            return result
    async def run():
        client = AdaptiveGateway()
        backend = backend_for(client)
        app = create_app(backend=backend)
        body = adaptive_request("price_tou")
        now = time.time()
        body["state"]["last_changed_at"] = now - 300
        body["signals"]["price"]["timestamp"] = now
        path = tmp_path / "adaptive-policy.json"
        path.write_text(json.dumps(body))
        raw = path.read_bytes()
        async with app.run_test(size=(110, 44)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+o")
            await pilot.pause()
            await pilot.press("f6")
            await pilot.pause()
            app.query_one("#preview-kind", Select).value = "adaptive"
            app.query_one("#preview-file", Input).value = str(path)
            await pilot.pause()
            app.action_preview()
            await pilot.pause()
            rendered = log_text(app.query_one("#preview-result", RichLog))
            assert "BATTERY" in rendered and "Candidate TOU plan" in rendered
            assert "peak" in rendered and "Commands sent: 0" in rendered
            assert "PRIVATE" not in rendered and path.read_bytes() == raw
            app.query_one("#preview-kind", Select).value = "fixed"
            await pilot.pause()
            assert "BATTERY" not in log_text(app.query_one("#preview-result", RichLog))
            assert all(call[0] in ("devices", "snapshot", "history") for call in client.calls)
            await app.action_quit()
    asyncio.run(run())


def test_late_history_is_discarded_after_disconnect():
    pytest.importorskip("textual")
    from textual.widgets import DataTable
    started, release = Event(), Event()
    class SlowGateway(FakeGateway):
        def history(self, name, **fields):
            started.set()
            release.wait(3)
            return saved_history(name)
    async def run():
        backend = backend_for(SlowGateway())
        app = create_app(backend=backend)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause()
            app.action_history_load()
            assert await asyncio.to_thread(started.wait, 1)
            app.action_disconnect()
            await pilot.pause()
            release.set()
            await pilot.pause()
            assert app.query_one("#history-table", DataTable).row_count == 0
            assert backend.target is None
            await app.action_quit()
    try:
        asyncio.run(run())
    finally:
        release.set()


def test_late_preview_is_discarded_when_cached_snapshot_changes(monkeypatch, tmp_path):
    pytest.importorskip("textual")
    from textual.widgets import Input, RichLog
    from solix_link import terminal_preview
    started, release = Event(), Event()
    def slow_preview(*_args, **_kwargs):
        started.set()
        release.wait(3)
        return {"decision": "opportunity", "reasons": [], "current_settings": {},
                "proposed_settings": [{"command": "set-charge-power", "watts": 1000}],
                "telemetry_age_seconds": 0}
    monkeypatch.setattr(terminal_preview, "manual_preview", slow_preview)
    async def run():
        backend = backend_for(FakeGateway())
        app = create_app(backend=backend)
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+o")
            await pilot.pause()
            await pilot.press("f6")
            await pilot.pause()
            app.query_one("#preview-file", Input).value = str(tmp_path / "synthetic.json")
            await pilot.pause()
            app.action_preview()
            assert await asyncio.to_thread(started.wait, 1)
            changed = deepcopy(app.snapshot)
            changed["available"] = False
            app.render_snapshot(changed)
            release.set()
            await pilot.pause()
            assert "OPPORTUNITY" not in log_text(app.query_one("#preview-result", RichLog))
            assert "Snapshot changed" in log_text(app.query_one("#preview-result", RichLog))
            assert not app.preview_loading
            await app.action_quit()
    try:
        asyncio.run(run())
    finally:
        release.set()


def test_cli_flags_and_scripted_history_keep_exact_names(monkeypatch, tmp_path, capsys):
    from solix_link import gateway_client, tui
    calls = []
    token = tmp_path / "token"
    monkeypatch.setattr(tui, "run_tui", lambda config, directory, **kwargs: calls.append(kwargs))
    assert cli.main(["tui", "--gateway-url", "http://127.0.0.1:8765", "--gateway-token-file", str(token)]) == 0
    assert calls == [{"gateway_url": "http://127.0.0.1:8765", "gateway_token_file": token}]
    fake = FakeGateway()
    monkeypatch.setattr(gateway_client, "GatewayClient", lambda *_args: fake)
    assert cli.main(["gateway-history", "--gateway-url", "http://127.0.0.1:8765", "--name", fake.name,
                     "--since", "900", "--until", "1000", "--limit", "2000"]) == 0
    assert json.loads(capsys.readouterr().out)["name"] == fake.name
    assert fake.calls[-1] == ("history", fake.name, {"since": 900.0, "until": 1000.0, "limit": 2000})


def test_narrow_history_preview_navigation_and_drafts_survive_refresh():
    pytest.importorskip("textual")
    from textual.widgets import Footer, Input, TabbedContent
    async def run():
        backend = backend_for(FakeGateway())
        app = create_app(backend=backend)
        async with app.run_test(size=(45, 24)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+o")
            await pilot.pause()
            await pilot.press("f5")
            await pilot.pause()
            tabs = app.query_one("#tabs", TabbedContent)
            assert tabs.active == "history"
            assert tabs.region.bottom <= app.query_one(Footer).region.y
            await pilot.press("f6")
            await pilot.pause()
            assert tabs.active == "preview"
            app.query_one("#preview-price", Input).value = "0.25"
            await pilot.pause()
            app.render_snapshot(await backend.refresh())
            assert app.query_one("#preview-price", Input).value == "0.25"
            assert app.query_one("#body").scroll_y == 0
            await app.action_quit()
    asyncio.run(run())


def test_gateway_line_fallback_never_scans_ble(monkeypatch, tmp_path, capsys):
    from solix_link import gateway_client
    fake = FakeGateway()
    monkeypatch.setattr(gateway_client, "GatewayClient", lambda *_args: fake)
    monkeypatch.setattr(interactive, "select_device", lambda *_args: pytest.fail("No BLE fallback"))
    choices = iter(["1", "1", "0"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(choices))
    interactive.run_interactive(tmp_path / "config.json", gateway_url="http://127.0.0.1:8765")
    output = capsys.readouterr().out
    assert "PRIVATE" not in output and "read only" in output
    assert all(call[0] in ("devices", "snapshot") for call in fake.calls)


@pytest.mark.parametrize("view", ["history", "preview"])
def test_gateway_line_history_and_preview_send_no_commands(monkeypatch, tmp_path, capsys, view):
    fake = FakeGateway()
    path = policy_file(tmp_path)
    values = (["1", "2", "24", "0"] if view == "history" else
              ["1", "3", str(path), "", "", "", "", "0"])
    choices = iter(values)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(choices))
    interactive.run_gateway_menu(fake)
    output = capsys.readouterr().out
    assert "PRIVATE" not in output
    assert all(call[0] in ("devices", "snapshot", "history") for call in fake.calls)
    if view == "preview":
        assert '"commands_sent": 0' in output and '"decision": "opportunity"' in output
    else:
        assert "not stored battery energy" in output and fake.calls[-1][0] == "history"


def test_token_file_without_explicit_url_is_rejected_before_selection(tmp_path):
    with pytest.raises(ValueError, match="requires"):
        interactive.run_interactive(tmp_path / "config.json", gateway_token_file=tmp_path / "token")
    with pytest.raises(ValueError, match="requires"):
        TuiBackend([], gateway_token_file=tmp_path / "token")
