"""Terminal workflow using the same validated BLE and native MQTT operations."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time

from .client import SolixMonitor, discover
from .config import DeviceConfig, load_config, protocol_choices, save_config
from .ap_service_config import APServiceConfig, add_ap_service_device, initialize_ap_service, load_ap_service, load_ap_service_profiles, private_write
from .ap_service import ap_service_request
from .protocol import Model
from .c1000_capabilities import ORIGINAL_AC_SMART_WARNING, ORIGINAL_DC_SMART_WARNING, ORIGINAL_FAST_CHARGE_WARNING, original_prime_commands, original_prime_operation_supported
from .commands import native_commands_for_model


def choose(title: str, options: list[str]) -> int | None:
    """Return a zero-based selection, or None for Back/EOF."""
    print(f"\n{title}")
    for index, label in enumerate(options, 1):
        print(f"  {index}. {label}")
    print("  0. Back / exit")
    while True:
        try:
            value = input("Select: ").strip()
        except EOFError:
            return None
        if value == "0":
            return None
        if value.isdecimal() and 1 <= int(value) <= len(options):
            return int(value) - 1
        print("Enter one of the listed numbers.")


def prompt(label: str, default: str = "") -> str:
    value = input(f"{label}" + (f" [{default}]" if default else "") + ": ").strip()
    return value or default


def select_device(config_path: Path) -> DeviceConfig | None:
    """Combine saved devices with a fresh scan; a missing adapter is recoverable."""
    saved = load_config(config_path)
    print("Scanning Bluetooth for supported SOLIX stations…", flush=True)
    try:
        discovered = asyncio.run(discover(timeout=8))
    except Exception as error:
        print(f"Scan unavailable ({type(error).__name__}); saved devices are still selectable.")
        discovered = []
    by_address = {device.address.upper(): device for device in discovered}
    candidates = list(saved)
    for found in discovered:
        if not any(device.address.upper() == found.address.upper() for device in saved):
            candidates.append(DeviceConfig(f"station_{len(candidates) + 1}", found.address, Model.from_name(found.name)))
    if not candidates:
        print("No stations found. Turn on the station and release other Bluetooth connections, then rescan.")
        return None
    choice = choose("Select a station", [
        f"{device.name} — {device.model.value} ({'advertising' if device.address.upper() in by_address else 'saved; not advertising'})"
        for device in candidates
    ])
    if choice is None:
        return None
    device = candidates[choice]
    if device not in saved:
        name = prompt("Save station as", device.name)
        if any(existing.name == name for existing in saved):
            raise ValueError("That name already belongs to another station")
        protocol = device.protocol
        choices = protocol_choices(device.model)
        if len(choices) > 1:
            selected_protocol = choose("Bluetooth protocol (choose for the installed firmware)", [
                f"{choice.title()}{' (model default)' if index == 0 else ''}" for index, choice in enumerate(choices)
            ])
            if selected_protocol is None:
                return None
            protocol = choices[selected_protocol]
        timezone_name = prompt("Timezone", "Etc/UTC") if protocol == "prime" else None
        device = DeviceConfig(name, device.address, device.model, protocol=protocol, timezone_name=timezone_name)
        save_config([*saved, device], config_path)
    return device


def change_protocol(device: DeviceConfig, config_path: Path) -> DeviceConfig:
    """Change the saved Bluetooth transport without sending station commands."""
    choices = protocol_choices(device.model)
    selected = choose(f"Bluetooth protocol (current: {device.protocol})", [choice.title() for choice in choices])
    if selected is None or choices[selected] == device.protocol:
        return device
    protocol = choices[selected]
    timezone_name = device.timezone_name
    if protocol == "prime" and timezone_name is None:
        timezone_name = prompt("Timezone", "Etc/UTC")
    updated = replace(device, protocol=protocol, timezone_name=timezone_name)
    saved = load_config(config_path)
    if not any(entry.name == device.name and entry.address.upper() == device.address.upper() for entry in saved):
        raise ValueError("Saved station changed; select it again before changing protocol")
    save_config([updated if entry.name == device.name else entry for entry in saved], config_path)
    if device.model == Model.C1000 and protocol == "prime":
        print("Original C1000 Prime 1.7.1 supports the listed settings and confirmed AC socket changes with an inactive AC countdown. AC Smart requires AC output off; DC Smart requires DC output off. Fast charging requires adequate AC supply.")
    return updated


def ensure_paired(device: DeviceConfig, config_path: Path) -> DeviceConfig:
    if device.protocol != "prime" or device.client_id:
        return device
    action = choose("This station needs a local Bluetooth pairing ID", [
        "Pair locally with a main-button confirmation", "Use an existing pairing ID (hidden input)",
    ])
    if action is None:
        raise ValueError("Pairing cancelled")
    if action == 0:
        from .cli import _pair
        asyncio.run(_pair(argparse.Namespace(name=device.name, address=device.address, model=device.model.value,
                                            client_id=None, timezone=device.timezone_name, config=config_path)))
    else:
        import getpass
        updated = DeviceConfig(device.name, device.address, device.model, getpass.getpass("Pairing ID: "),
                               device.protocol, device.timezone_name)
        save_config([updated if saved.name == device.name else saved for saved in load_config(config_path)], config_path)
    return next(saved for saved in load_config(config_path) if saved.name == device.name)


async def read_serial(device: DeviceConfig) -> str:
    async with SolixMonitor(device.address, model=device.model, owner_user_id=device.client_id,
                            protocol=device.protocol, timezone_name=device.timezone_name) as monitor:
        metrics = await monitor.wait_for_update(timeout=15)
        serial = metrics.get("serial_number")
        if not isinstance(serial, str):
            raise ValueError("Station did not report a serial number")
        return serial


def wifi_adapters() -> list[tuple[str, str]]:
    """List dedicated-adapter candidates without changing NetworkManager state."""
    result = []
    for interface in sorted(Path("/sys/class/net").glob("*")):
        phy = interface / "phy80211"
        try:
            if phy.exists() and not int((interface / "flags").read_text(), 16) & 1:
                result.append((interface.name, phy.resolve().name))
        except OSError:
            continue
    return result


def setup_ap_service(device: DeviceConfig, directory: Path) -> None:
    if device.model not in (Model.C1000, Model.C1000_GEN2, Model.C2000_GEN2) or device.protocol != "prime" or not device.client_id:
        raise ValueError("AP setup requires a paired Prime original C1000, C1000 Gen 2 or C2000 Gen 2")
    print("Local MQTT uses a dedicated Wi-Fi adapter, a private API and an AP without an internet route.")
    if (directory / "ap_service.json").exists():
        from dataclasses import replace
        parent = load_ap_service(directory / "ap_service.json")
        serial = asyncio.run(read_serial(device))
        config = replace(parent, name=device.name, model=device.model, device_serial=serial,
                         account_id=device.client_id, timezone_name=device.timezone_name or "Etc/UTC")
        add_ap_service_device(directory, config)
        print(f"Added {device.name} to the shared AP; provision this station when starting the AP service.")
        return
    adapters = wifi_adapters()
    if adapters:
        selected = choose("Select an unused Wi-Fi adapter (currently DOWN)", [f"{interface} / {phy}" for interface, phy in adapters])
        if selected is None:
            return
        interface, phy = adapters[selected]
    else:
        print("No DOWN Wi-Fi adapter was found. Existing active adapters will be refused.")
        interface, phy = prompt("Dedicated Wi-Fi interface"), prompt("Wi-Fi phy")
    country = prompt("Regulatory country (two letters)").upper()
    try:
        serial = asyncio.run(read_serial(device))
    except Exception as error:
        print(f"Could not read the serial over Bluetooth ({type(error).__name__}).")
        import getpass
        length = 16 if device.model == Model.C1000 else 17
        serial = getpass.getpass(f"Device serial ({length} characters, hidden): ").strip()
    import getpass
    account = getpass.getpass("App account ID, or Enter for saved BLE pairing ID (hidden): ").strip() or device.client_id
    config = APServiceConfig(device.name, interface, phy, country, serial, account,
                       timezone_name=device.timezone_name or "Etc/UTC", model=device.model)
    initialize_ap_service(directory, config)
    print(f"Saved local AP credentials and certificates in {directory}. Native setup is experimental ({device.model.value}).")


def _show_status(status: dict) -> None:
    metrics = status.get("metrics", {})
    print(f"Connected: {status.get('connected', False)}; fresh data: {status.get('available', False)}")
    print(f"Power flow: {status.get('power_flow', 'unknown')}")
    for key in ("battery_percentage", "battery_status", "ac_output_enabled", "ac_input_power_w", "ac_output_power_w", "ac_charging_power_limit_w",
                "usage_mode", "active_tariff", "backup_reserve_percentage", "tou_schedule_slot_count",
                "ac_fast_charge_enabled", "temperature_unit_fahrenheit", "ac_power_saving_mode_enabled", "dc_power_saving_mode_enabled"):
        if key in metrics:
            print(f"  {key}: {metrics[key]}")
    if "device_timeout_minutes" in metrics:
        minutes = metrics["device_timeout_minutes"]
        print(f"  Device Timeout: {'Never' if minutes == 0 else str(minutes) + ' minutes'}")
    from .energy_values import native_energy_rows, validate_native_energy
    energy = validate_native_energy(status.get("native_energy"), model=status.get("model")) if status.get("protocol") == "native_mqtt" else None
    if energy:
        print(f"  Native energy: {'recent' if energy['available'] else 'stale'} report; epoch {energy['counter_epoch']}; units unverified")
        for group, key, raw, kwh in native_energy_rows(energy):
            print(f"  {group} / {key}: {raw} raw; {kwh} nominal kWh")


def device_timeout_menu(device: DeviceConfig | APServiceConfig, config_path: Path,
                        directory: Path | None = None) -> None:
    minutes = (0, 30, 60, 120, 240, 360, 720, 1440)
    selected = choose("Device Timeout", ["Never", "30 minutes", "1 hour", "2 hours", "4 hours", "6 hours", "12 hours", "24 hours"])
    if selected is None:
        return
    value = minutes[selected]
    if value:
        print("The station may turn off when idle, interrupting remote access.")
    print("Never disables this timeout; other sleep behavior may still interrupt remote access.")
    if choose("Apply Device Timeout?", ["Apply selected timeout"]) is None:
        return
    if directory is not None:
        _show_status(asyncio.run(ap_service_request(directory, "set-device-timeout", name=device.name, minutes=value)))
    else:
        from .cli import _set
        asyncio.run(_set(argparse.Namespace(command="set-device-timeout", name=device.name, minutes=value, config=config_path)))


def preference_menu(device: DeviceConfig | APServiceConfig, config_path: Path, directory: Path | None = None) -> None:
    clock_windows = {}
    original = device.model == Model.C1000 and getattr(device, "protocol", None) == "legacy"
    if original:
        commands = ["set-temperature-unit", "set-fast-charge", "set-ac-power-saving", "set-dc-power-saving"]
        labels = ["Temperature display", "Fast charging", "AC power saving (may turn output off)", "DC power saving (may turn output off)"]
    elif device.model == Model.C1000 and (directory is not None or getattr(device, "protocol", None) == "prime"):
        candidates = [("set-display-brightness", "Display brightness"), ("set-display-timeout", "Screen timeout"),
                      ("set-light", "Light mode"), ("set-temperature-unit", "Temperature display"),
                      ("set-dc-power-saving", "DC Smart mode (requires DC output off)"),
                      ("set-fast-charge", "Fast charging (requires adequate AC supply)"),
                      ("set-ac-power-saving", "AC Smart mode (requires AC output off and inactive timer)"),
                      ("set-ac-output", "AC sockets (changes powered loads)")]
        supported = native_commands_for_model(device.model) if directory is not None else original_prime_commands()
        if directory is None and original_prime_operation_supported("ac_output"):
            supported += ["set-ac-output"]
        commands = [command for command, _ in candidates if command in supported]
        labels = [label for command, label in candidates if command in supported]
    elif device.model == Model.C1000_GEN2 and (directory is not None or getattr(device, "protocol", None) == "prime"):
        commands, labels = ["set-fast-charge"], ["Fast charging"]
        if directory is not None:
            commands += ["set-display-brightness", "set-display-timeout", "set-port-memory",
                         "set-ac-power-saving", "set-dc-power-saving"]
            labels += ["Display brightness", "Screen timeout", "Output port memory",
                       "AC Smart mode (requires AC output off)", "DC Smart mode (requires DC output off)"]
            clock_windows = {len(commands): 1, len(commands) + 1: 2}
            commands += ["set-clock-brightness", "set-clock-brightness"]
            labels += ["First clock window brightness", "Second clock window brightness"]
    else:
        print("These preferences are unavailable for this station profile.")
        return
    if not commands:
        print("Native preferences are not yet verified for this station profile.")
        return
    selected = choose("Station preferences", labels)
    if selected is None:
        return
    command = commands[selected]
    options = {
        "set-temperature-unit": ["Celsius", "Fahrenheit"],
        "set-display-brightness": ["Low", "Medium", "High"],
        "set-display-timeout": ["20 seconds", "30 seconds", "60 seconds", "5 minutes", "30 minutes"] if device.model == Model.C1000
                               else ["Never", "10 seconds", "20 seconds", "30 seconds", "60 seconds", "5 minutes", "30 minutes"],
        "set-light": ["Off", "Low", "Medium", "High", "SOS"],
        "set-clock-brightness": ["Normal", "High"],
    }.get(command, ["Off", "On"])
    value = choose(labels[selected], options)
    if value is None:
        return
    if command in ("set-ac-power-saving", "set-dc-power-saving"):
        print("Power saving may automatically turn the output off at low load.")
        if device.model == Model.C1000 and (directory is not None or getattr(device, "protocol", None) == "prime"):
            print(ORIGINAL_AC_SMART_WARNING if command == "set-ac-power-saving" else ORIGINAL_DC_SMART_WARNING)
        elif device.model == Model.C1000_GEN2:
            print("Requires main 1.1.4.9, the corresponding output off and both AC/DC countdowns inactive.")
    if command == "set-clock-brightness":
        print("Requires main 1.1.4.9, Standard mode, disabled clock, idle asset transfer and inactive output countdowns. Does not enable the clock.")
    if command == "set-ac-output":
        print("This changes power at the AC sockets. Original Prime requires a fresh inactive AC countdown; review connected loads before applying.")
    if command == "set-fast-charge" and device.model == Model.C1000_GEN2:
        print("Enabling requires Standard mode with no active tariff. Native MQTT also requires connected mains.")
    if command == "set-fast-charge" and device.model == Model.C1000:
        print(ORIGINAL_FAST_CHARGE_WARNING)
    if command == "set-port-memory":
        print("Off clears output-recovery bookkeeping; turning On does not restore that transient state.")
    if choose("Confirm preference change", ["Apply selected preference"]) is None:
        return
    if directory is not None:
        fields = ({"level": value + 1} if command == "set-display-brightness" else
                  {"seconds": ((20, 30, 60, 300, 1800) if device.model == Model.C1000 else (0, 10, 20, 30, 60, 300, 1800))[value]} if command == "set-display-timeout" else
                  {"mode": value} if command == "set-light" else
                  {"fahrenheit": value == 1} if command == "set-temperature-unit" else
                  {"window": clock_windows[selected], "high": value == 1} if command == "set-clock-brightness" else
                  {"enabled": value == 1})
        _show_status(asyncio.run(ap_service_request(directory, command, name=device.name, **fields)))
    else:
        from .cli import _set
        arguments = ({"unit": "fahrenheit" if value else "celsius"} if command == "set-temperature-unit" else
                     {"enabled": "on" if value else "off"} if command == "set-ac-output" else
                     {"level": value + 1} if command == "set-display-brightness" else
                     {"seconds": (20, 30, 60, 300, 1800)[value]} if command == "set-display-timeout" else
                     {"mode": ("off", "low", "medium", "high", "sos")[value]} if command == "set-light" else
                     {"enabled": "on" if value else "off"})
        asyncio.run(_set(argparse.Namespace(command=command, name=device.name, config=config_path, **arguments)))


def native_session(directory: Path, config_path: Path, *, provision: bool, allow_control: bool,
                   name: str | None = None) -> None:
    profiles = load_ap_service_profiles(directory)
    if name is None and len(profiles) > 1:
        names = list(profiles)
        selected = choose("Select native station", names)
        if selected is None:
            return
        name = names[selected]
    config = profiles[name][0] if name is not None else next(iter(profiles.values()))[0]
    maximum_power = {Model.C1000: 1000, Model.C1000_GEN2: 1200, Model.C2000_GEN2: 1800}[config.model]
    minimum_power = 300 if config.model == Model.C2000_GEN2 else 100
    command = [sys.executable, "-m", "solix_link", "ap-service-run", "--directory", str(directory.resolve()),
               "--config", str(config_path.resolve())]
    if provision:
        command.extend(["--provision", "--name", config.name])
    if allow_control:
        command.append("--allow-control")
    if os.geteuid() != 0:
        print("Starting the isolated AP needs root to move the dedicated adapter into its namespace. Run:")
        print("sudo " + shlex.join(command))
        print("Then reopen interactive mode to inspect the AP-service status.")
        return
    log_path = directory / f"interactive-{time.time_ns()}.log"
    private_write(log_path, b"")
    with log_path.open("ab") as log:
        process = subprocess.Popen(command, stdout=log, stderr=log, start_new_session=True)
    try:
        print(f"Starting the AP service; logs: {log_path}. Radio reconnection can take several minutes.")
        while process.poll() is None:
            if config.model == Model.C1000:
                supported = native_commands_for_model(config.model) if allow_control else ()
                actions = ["status"]
                options = ["Show live status"]
                for action, label in (("set-charge-power", "Set charging-power limit"),
                                      ("set-device-timeout", "Set Device Timeout (Never / idle shutdown)")):
                    if action in supported:
                        actions.append(action)
                        options.append(label)
                if any(command in supported for command in ("set-display-brightness", "set-display-timeout", "set-light", "set-temperature-unit")):
                    actions.append("preferences")
                    options.append("Display, light and temperature preferences")
                options.append("Stop this AP session")
                selected = choose("Original C1000 AP-service session", options)
                if selected is None or selected == len(options) - 1:
                    break
                try:
                    action = actions[selected]
                    if action == "status":
                        _show_status(asyncio.run(ap_service_request(directory, "status", name=config.name)))
                    elif action == "set-charge-power":
                        watts = int(prompt(f"Charging-power limit ({minimum_power}–{maximum_power} W in 100 W steps)"))
                        _show_status(asyncio.run(ap_service_request(directory, action, name=config.name, watts=watts)))
                    elif action == "set-device-timeout":
                        device_timeout_menu(config, config_path, directory)
                    else:
                        preference_menu(config, config_path, directory)
                except (ValueError, OSError, RuntimeError, TimeoutError) as error:
                    print(f"{type(error).__name__}: {error}. Check fresh status before retrying a control write.")
                continue
            options = ["Show live status", "Read controller readiness",
                                                     "Set charging-power limit" if allow_control else "Charging controls disabled",
                                                     "Set upper charge limit" if allow_control else "Charge-cap controls disabled",
                                                     "Set backup reserve" if allow_control else "Reserve controls disabled",
                                                     "Store or activate hourly tariff plan" if allow_control else "Tariff controls disabled",
                                                     "Clear plan and confirm grid power" if allow_control else "Grid-return control disabled"]
            if config.model == Model.C1000_GEN2:
                options.append("Set Device Timeout (Never / idle shutdown)" if allow_control else "Device Timeout controls disabled")
                options.append("Charging, display and port-memory preferences" if allow_control else "Station preferences disabled")
            options.append("Stop this AP session")
            selected = choose("AP-service session", options)
            if selected is None or selected == len(options) - 1:
                break
            try:
                if selected == 0:
                    _show_status(asyncio.run(ap_service_request(directory, "status", name=config.name)))
                elif selected == 1:
                    print(json.dumps(asyncio.run(ap_service_request(directory, "readiness", name=config.name))))
                elif selected == 2 and allow_control:
                    watts = int(prompt(f"Charging-power limit ({minimum_power}–{maximum_power} W in 100 W steps)"))
                    _show_status(asyncio.run(ap_service_request(directory, "set-charge-power", name=config.name, watts=watts)))
                elif selected == 3 and allow_control:
                    upper = int(prompt("Upper charge limit (80–100% in 5% steps)"))
                    _show_status(asyncio.run(ap_service_request(directory, "set-charge-cap", name=config.name, upper=upper)))
                elif selected == 4 and allow_control:
                    reserve = int(prompt("Backup reserve (5–100% in 5% steps, within charge limits)"))
                    _show_status(asyncio.run(ap_service_request(directory, "set-backup-reserve", name=config.name, reserve=reserve)))
                elif selected == 5 and allow_control:
                    from .tou import TouPeriod
                    mode = choose("Plan mode", ["Store in Standard", "Activate Time-of-Use (persists until changed)"])
                    if mode is None:
                        continue
                    text = prompt("Periods separated by commas, e.g. peak:0:24; empty clears the plan")
                    periods = []
                    for value in text.split(",") if text else []:
                        parts = value.strip().split(":")
                        if len(parts) != 3:
                            raise ValueError("Period format must be TARIFF:START:END")
                        periods.append(TouPeriod(parts[0], int(parts[1]), int(parts[2])).to_dict())
                    _show_status(asyncio.run(ap_service_request(directory, "set-tou-plan", name=config.name, periods=periods, enabled=mode == 1)))
                elif selected == 6 and allow_control:
                    _show_status(asyncio.run(ap_service_request(directory, "return-grid", name=config.name)))
                elif selected == 7 and allow_control and config.model == Model.C1000_GEN2:
                    device_timeout_menu(config, config_path, directory)
                elif selected == 8 and allow_control and config.model == Model.C1000_GEN2:
                    preference_menu(config, config_path, directory)
            except (ValueError, OSError, RuntimeError, TimeoutError) as error:
                print(f"{type(error).__name__}: {error}. Check fresh status before retrying a control write.")
        if process.poll() is not None and process.returncode:
            print(f"AP session exited; inspect {log_path}.")
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=40)
            except subprocess.TimeoutExpired:
                raise RuntimeError(f"AP shutdown is still running; inspect private log {log_path}") from None


def mqtt_menu(device: DeviceConfig, config_path: Path, directory: Path) -> None:
    while True:
        choices = ["Publish BLE telemetry to my MQTT broker"]
        if device.model in (Model.C1000, Model.C1000_GEN2, Model.C2000_GEN2) and device.protocol == "prime":
            choices += ["Create AP-service configuration", "Start saved AP service",
                        "Provision/reconnect to AP service", "Inspect running AP-service status",
                        "Serve native status over HTTP", "Check saved AP setup (read-only)"]
        action = choose("MQTT connection", choices)
        if action is None:
            return
        try:
            if action == 0:
                from .manager import MonitorService
                from .mqtt_bridge import MqttBridge
                host = prompt("Your MQTT broker", "127.0.0.1")
                ca = prompt("TLS CA file (optional)")
                port = int(prompt("Port", "8883" if ca else "1883"))
                username = prompt("MQTT username (optional)")
                password = prompt("MQTT password file (optional)") if username else ""
                print("Publishing BLE telemetry; Ctrl-C stops the bridge.")
                asyncio.run(MqttBridge(MonitorService([device]), host=host, port=port,
                                       username=username or None, password_file=Path(password) if password else None,
                                       ca_file=Path(ca) if ca else None).run())
            elif action == 1:
                setup_ap_service(device, directory)
            elif action in (2, 3):
                profiles = load_ap_service_profiles(directory)
                config = profiles.get(device.name, (None, None))[0]
                if config is None or config.model != device.model:
                    raise ValueError("AP service belongs to a different selected station")
                choices = ["Monitoring only"]
                if native_commands_for_model(config.model):
                    choices.append("Enable explicit verified native commands")
                controls = choose("Native control", choices)
                if controls is not None:
                    native_session(directory, config_path, provision=action == 3, allow_control=controls == 1, name=device.name)
            elif action == 4:
                _show_status(asyncio.run(ap_service_request(directory, "status", name=device.name)))
            elif action == 5:
                from .ap_service_monitor import APServiceMonitor
                from .server import run_server
                host = prompt("HTTP listen address", "127.0.0.1")
                port = int(prompt("HTTP port", "8765"))
                run_server(APServiceMonitor(load_ap_service(directory / "ap_service.json"), directory), host, port)
            elif action == 6:
                from .ap_service_check import check_ap_service
                print(json.dumps(check_ap_service(directory, config_path), indent=2))
        except KeyboardInterrupt:
            print("Stopped.")
        except Exception as error:
            print(f"{type(error).__name__}: {error}")


def run_gateway_menu(client) -> None:
    """GET-only line fallback; choosing a gateway never scans Bluetooth."""
    from .gateway_client import GatewayReadError
    from .terminal_preview import manual_preview
    from .tui import public_snapshot
    selected = None
    while True:
        try:
            if selected is None:
                stations = client.devices()
                choice = choose("Read-only gateway stations", [item["name"] for item in stations])
                if choice is None:
                    return
                selected = stations[choice]["name"]
            action = choose(f"Gateway: {selected} · read only", [
                "Cached status (no station request)", "Saved AC/battery history",
                "Charging policy preview (commands sent: 0)", "Select another station",
                "Native energy counters / nominal kWh (cached)",
            ])
            if action is None:
                return
            if action == 3:
                selected = None
                continue
            if action == 0:
                print(json.dumps(public_snapshot(client.snapshot(selected)), indent=2))
            elif action == 1:
                value = prompt("History hours (1,6,24,168)", "24")
                if value not in ("1", "6", "24", "168"):
                    raise GatewayReadError("Choose 1, 6, 24 or 168 history hours")
                now = time.time()
                result = client.history(selected, since=max(0, now - int(value) * 3600), until=now, limit=200)
                print("AC energy is estimated, includes bypass, and is not stored battery energy. Gaps remain unknown.")
                print(json.dumps(result, indent=2))
            elif action == 2:
                raw = client.snapshot(selected)
                snapshot = {**public_snapshot(raw), "model": raw["model"], "protocol": raw["protocol"]}
                path = Path(prompt("Policy request JSON file")).expanduser()
                print("Optional manual observations: enter value AND age, or leave both blank. Age0 means observed now.")
                result = manual_preview(snapshot, path, price_value=prompt("Price value"),
                                        price_age=prompt("Price age (seconds)"), export_value=prompt("Export W (+ means grid export)"),
                                        export_age=prompt("Export age (seconds)"))
                print("Read-only preview uses this laptop's clock. No policy/HA state or settings are changed.")
                print(json.dumps(result, indent=2))
            elif action == 4:
                print("Native counters are uncalibrated, may reset, and include bypass. Mode groups are not summed.")
                print(json.dumps(client.energy(selected), indent=2))
        except KeyboardInterrupt:
            print("Stopped.")
        except Exception as error:
            from .tui import safe_error
            print(safe_error(error))
            if selected is None:
                return


def run_interactive(config_path: Path, ap_service_directory: Path | None = None, *,
                    gateway_url: str | None = None, gateway_token_file: Path | None = None) -> None:
    if gateway_token_file is not None and gateway_url is None:
        raise ValueError("A gateway token file requires --gateway-url")
    if gateway_url is not None:
        from .gateway_client import GatewayClient
        run_gateway_menu(GatewayClient(gateway_url, gateway_token_file))
        return
    print("SOLIX local monitoring — Ctrl-C stops an active monitor; 0 returns to the menu.")
    selected = select_device(config_path)
    while True:
        options = [
            "Select / rescan a station", "Monitor over Bluetooth", "Connect MQTT / isolated Wi-Fi",
            "Serve Bluetooth status over HTTP",
        ]
        if selected and (selected.model == Model.C1000
                         or selected.model == Model.C1000_GEN2 and selected.protocol == "prime"):
            options.append("Set Device Timeout (Never / idle shutdown)")
            options.append("Station preferences (temperature / fast charge / power saving)" if selected.model == Model.C1000 and selected.protocol == "legacy"
                           else "Station preferences (display / light / temperature)" if selected.model == Model.C1000
                           else "Station preferences (fast charging)")
        protocol_action = None
        if selected and len(protocol_choices(selected.model)) > 1:
            protocol_action = len(options)
            options.append("Change Bluetooth protocol (Prime / legacy)")
        action = choose(f"Station: {selected.name if selected else 'none selected'}", options)
        if action is None:
            return
        try:
            if action == 0:
                selected = select_device(config_path)
                continue
            if selected is None:
                print("Select a station first.")
                continue
            if action == protocol_action:
                selected = change_protocol(selected, config_path)
                continue
            selected = ensure_paired(selected, config_path)
            if action == 1:
                from .cli import _monitor
                print("Monitoring Bluetooth; Ctrl-C returns to the menu.")
                asyncio.run(_monitor(argparse.Namespace(name=selected.name, config=config_path)))
            elif action == 2:
                directory = ap_service_directory or config_path.parent / "ap-services" / selected.name
                mqtt_menu(selected, config_path, directory)
            elif action == 3:
                from .manager import MonitorService
                from .server import run_server
                host = prompt("HTTP listen address", "127.0.0.1")
                port = int(prompt("HTTP port", "8765"))
                run_server(MonitorService([selected]), host, port)
            elif action == 4:
                device_timeout_menu(selected, config_path)
            elif action == 5:
                preference_menu(selected, config_path)
        except KeyboardInterrupt:
            print("Stopped.")
        except Exception as error:
            print(f"{type(error).__name__}: {error}")
