from types import SimpleNamespace
import pytest

from solix_gen2 import Model
from solix_gen2 import cli, interactive
from solix_gen2.config import DeviceConfig, load_config, save_config


def inputs(monkeypatch, values):
    values = iter(values)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(values))


@pytest.mark.parametrize("selection,command,fields", [
    ("5", "set-ac-power-saving", {"enabled": True}),
    ("6", "set-dc-power-saving", {"enabled": True}),
    ("7", "set-clock-brightness", {"window": 1, "high": True}),
    ("8", "set-clock-brightness", {"window": 2, "high": True}),
])
def test_gen2_native_preference_fallback_dispatches_selected_window(monkeypatch, tmp_path, selection, command, fields, capsys):
    requests = []
    async def request(directory, action, **values):
        requests.append((directory, action, values))
        return {}
    monkeypatch.setattr(interactive, "ap_service_request", request)
    monkeypatch.setattr(interactive, "_show_status", lambda value: None)
    inputs(monkeypatch, [selection, "2", "1"])
    device = SimpleNamespace(name="office", model=Model.C1000_GEN2)
    interactive.preference_menu(device, tmp_path / "config.json", tmp_path)
    assert requests == [(tmp_path, command, {"name": "office", **fields})]
    assert "Requires main 1.1.4.9" in capsys.readouterr().out


def test_gen2_native_preference_cancel_sends_nothing(monkeypatch, tmp_path):
    async def request(*args, **values):
        raise AssertionError("Cancelled command must not be sent")
    monkeypatch.setattr(interactive, "ap_service_request", request)
    inputs(monkeypatch, ["8", "2", "0"])
    interactive.preference_menu(SimpleNamespace(name="office", model=Model.C1000_GEN2), tmp_path / "config.json", tmp_path)


@pytest.mark.parametrize("selection,seconds", [("1", 30), ("2", 60)])
def test_c2000_native_screen_menu_has_only_qualified_values(monkeypatch, tmp_path, capsys, selection, seconds):
    requests = []
    async def request(directory, action, **values):
        requests.append((action, values))
        return {}
    monkeypatch.setattr(interactive, "ap_service_request", request)
    monkeypatch.setattr(interactive, "_show_status", lambda value: None)
    inputs(monkeypatch, ["1", selection, "1"])
    interactive.preference_menu(SimpleNamespace(name="ups", model=Model.C2000_GEN2), tmp_path / "config.json", tmp_path)
    assert requests == [("set-display-timeout", {"name": "ups", "seconds": seconds})]
    output = capsys.readouterr().out
    assert "1. 30 seconds" in output and "2. 60 seconds" in output
    assert "Hardware validation is pending" in output
    assert "Never" not in output and "AC sockets" not in output


def test_c2000_native_screen_cancel_sends_nothing(monkeypatch, tmp_path):
    async def request(*args, **values):
        raise AssertionError("Cancelled command must not be sent")
    monkeypatch.setattr(interactive, "ap_service_request", request)
    inputs(monkeypatch, ["1", "2", "0"])
    interactive.preference_menu(SimpleNamespace(name="ups", model=Model.C2000_GEN2), tmp_path / "config.json", tmp_path)


def test_c2000_prime_preferences_are_reachable_without_idle_timeout(monkeypatch, tmp_path, capsys):
    device = SimpleNamespace(name="ups", model=Model.C2000_GEN2, protocol="prime")
    called = []
    monkeypatch.setattr(interactive, "select_device", lambda path: device)
    monkeypatch.setattr(interactive, "ensure_paired", lambda selected, path: selected)
    monkeypatch.setattr(interactive, "preference_menu", lambda *args: called.append(args))
    inputs(monkeypatch, ["5", "0"])
    interactive.run_interactive(tmp_path / "config.json")
    assert called == [(device, tmp_path / "config.json")]
    assert "Set Device Timeout" not in capsys.readouterr().out


def test_no_arguments_launch_guided_mode_only_in_a_terminal(monkeypatch, tmp_path, capsys):
    called = []
    monkeypatch.setattr(cli, "tui_available", lambda: False)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(interactive, "run_interactive", lambda config, directory: called.append((config, directory)))
    assert cli.main([]) == 0
    assert called == [(cli.DEFAULT_CONFIG, None)]
    assert "Using the line menu" in capsys.readouterr().err
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    assert cli.main([]) == 2


def test_no_arguments_use_terminal_dashboard_when_installed(monkeypatch):
    from solix_link import tui
    called = []
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "tui_available", lambda: True)
    monkeypatch.setattr(tui, "run_tui", lambda config, directory: called.append((config, directory)))
    assert cli.main([]) == 0
    assert called == [(cli.DEFAULT_CONFIG, None)]


def test_explicit_interactive_config_and_private_directory(monkeypatch, tmp_path, capsys):
    called = []
    monkeypatch.setattr(interactive, "run_interactive", lambda config, directory: called.append((config, directory)))
    config, directory = tmp_path / "config.json", tmp_path / "private"
    assert cli.main(["interactive", "--config", str(config), "--ap-service-directory", str(directory)]) == 0
    assert called == [(config, directory)]
    assert "tui extra" not in capsys.readouterr().err


def test_menu_invalid_selection_and_back(monkeypatch):
    inputs(monkeypatch, ["", "-1", "3", "2"])
    assert interactive.choose("Menu", ["one", "two"]) == 1
    inputs(monkeypatch, ["0"])
    assert interactive.choose("Menu", ["one"]) is None


def test_scan_merges_saved_devices_and_saves_new_station(monkeypatch, tmp_path):
    config = tmp_path / "config.json"
    saved = DeviceConfig("ups", "AA:BB:CC:DD:EE:01", Model.C2000_GEN2, "a" * 40)
    save_config([saved], config)
    async def scan(timeout):
        return [SimpleNamespace(address=saved.address, name="Anker A1783"),
                SimpleNamespace(address="AA:BB:CC:DD:EE:02", name="Anker A1723")]
    monkeypatch.setattr(interactive, "discover", scan)
    inputs(monkeypatch, ["2", "c300"])
    selected = interactive.select_device(config)
    assert selected.model == Model.C300 and selected.protocol == "legacy"
    assert selected.client_id is None
    assert len(load_config(config)) == 2
    assert config.stat().st_mode & 0o777 == 0o600


def test_saved_selection_survives_missing_bluetooth(monkeypatch, tmp_path):
    config = tmp_path / "config.json"
    saved = DeviceConfig("original", "AA:BB:CC:DD:EE:03", Model.C1000)
    save_config([saved], config)
    async def scan(timeout):
        raise OSError("No adapter")
    monkeypatch.setattr(interactive, "discover", scan)
    inputs(monkeypatch, ["1"])
    assert interactive.select_device(config) == saved
    assert interactive.ensure_paired(saved, config) == saved


def test_nonroot_native_setup_prints_explicit_command_without_spawning(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(interactive, "load_ap_service_profiles", lambda path: {"ups": (SimpleNamespace(name="ups", model=Model.C2000_GEN2), path)})
    monkeypatch.setattr(interactive.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(interactive.subprocess, "Popen", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not spawn")))
    interactive.native_session(tmp_path / "private directory", tmp_path / "config.json", provision=True, allow_control=False)
    output = capsys.readouterr().out
    assert "sudo " in output and "--provision" in output
    assert "ap-service-run" in output
    assert "--allow-control" not in output
    assert "private directory'" in output  # The displayed path is shell-quoted.


@pytest.mark.parametrize("model", [Model.C1000_GEN2, Model.C2000_GEN2])
def test_interactive_native_session_stops_owned_child(monkeypatch, tmp_path, model, capsys):
    import signal
    directory = tmp_path / "ap_service"
    directory.mkdir(mode=0o700)
    spawned = []
    monkeypatch.setattr(interactive, "load_ap_service_profiles", lambda path: {"ups": (SimpleNamespace(name="ups", model=model), path)})
    class Child:
        returncode = None
        def __init__(self, command, **kwargs):
            self.command = command
            self.signals = []
            spawned.append(self)
        def poll(self):
            return self.returncode
        def send_signal(self, value):
            self.signals.append(value)
        def wait(self, timeout):
            self.returncode = 0
    monkeypatch.setattr(interactive.os, "geteuid", lambda: 0)
    monkeypatch.setattr(interactive.subprocess, "Popen", Child)
    inputs(monkeypatch, ["0"])
    interactive.native_session(directory, tmp_path / "config.json", provision=False, allow_control=False)
    assert spawned[0].signals == [signal.SIGTERM]
    assert "ap-service-run" in spawned[0].command
    assert "--allow-control" not in spawned[0].command
    assert "--provision" not in spawned[0].command
    assert next(directory.glob("interactive-*.log")).stat().st_mode & 0o777 == 0o600
    if model == Model.C2000_GEN2:
        assert "Screen timeout controls disabled" in capsys.readouterr().out
