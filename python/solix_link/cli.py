"""Command-line discovery, pairing, monitoring, and HTTP serving."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import math
from pathlib import Path
import sys
from importlib.util import find_spec

from .client import SolixMonitor, discover
from .config import DEFAULT_CONFIG, DeviceConfig, load_config, save_config
from .manager import MonitorService
from .protocol import C1000_PRIME_SETTINGS, Model
from .c1000_capabilities import ORIGINAL_AC_SMART_WARNING, ORIGINAL_DC_SMART_WARNING, ORIGINAL_FAST_CHARGE_WARNING, original_prime_commands, original_prime_operation_supported


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(prog="solix-link", description="Local SOLIX monitoring and controls")
    subcommands = command.add_subparsers(dest="command", required=True)
    guided = subcommands.add_parser("interactive", help="Scan, select and monitor stations with guided BLE/MQTT setup")
    guided.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    guided.add_argument("--ap-service-directory", type=Path, help="Existing or new private AP-service directory")
    tui = subcommands.add_parser("tui", help="Open the terminal dashboard (requires the tui extra)")
    tui.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    tui.add_argument("--ap-service-directory", type=Path, help="Inspect/control an already running AP service")
    for target in (guided, tui):
        target.add_argument("--gateway-url", help="Explicit HTTP/HTTPS gateway for read-only cached monitoring/history")
        target.add_argument("--gateway-token-file", type=Path, help="Owner-only Bearer token file; never displayed or saved")

    gateway_history = subcommands.add_parser("gateway-history", help="Read bounded saved AC/battery history as JSON; no station requests")
    gateway_history.add_argument("--gateway-url", required=True)
    gateway_history.add_argument("--gateway-token-file", type=Path)
    gateway_history.add_argument("--name", required=True, help="Exact public station name")
    gateway_history.add_argument("--since", type=float, help="UNIX seconds; default last 24h on gateway")
    gateway_history.add_argument("--until", type=float, help="UNIX seconds; default gateway clock")
    gateway_history.add_argument("--limit", type=int, default=200, help="1–2000 points (default 200)")

    scan = subcommands.add_parser("scan", help="Find C300 AC, C1000, and C1000/C2000 Gen 2 devices")
    scan.add_argument("--timeout", type=float, default=8)

    inspect_ble = subcommands.add_parser("ble-inspect", help="Inspect C1000 advertisements/GATT without login, pairing or settings changes")
    inspect_ble.add_argument("--model", choices=[Model.C1000.value, Model.C1000_GEN2.value], required=True)
    inspect_ble.add_argument("--timeout", type=float, default=10, help="Discovery window, 1–30 seconds")
    inspect_ble.add_argument("--connect", action="store_true", help="Enumerate GATT services only when exactly one matching station advertises")
    inspect_ble.add_argument("--connect-timeout", type=float, default=15, help="Connection timeout, 1–30 seconds")

    add = subcommands.add_parser("add", help="Save a known device in the local config")
    add.add_argument("--name", required=True)
    add.add_argument("--address", required=True)
    add.add_argument("--model", choices=[model.value for model in Model], required=True)
    add.add_argument("--client-id", help="Previously paired 40-character Prime client ID")
    add.add_argument("--protocol", choices=["prime", "legacy"], help="Default: legacy for C300/original C1000, Prime for Gen 2; original C1000 1.7.1 supports explicit Prime monitoring")
    add.add_argument("--timezone", help="Station timezone, for example Europe/Vienna")
    add.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    pair = subcommands.add_parser("pair", help="Pair a Prime station, including original C1000 1.7.1")
    pair.add_argument("--name", required=True)
    pair.add_argument("--address", required=True)
    pair.add_argument("--model", choices=[Model.C1000.value, Model.C1000_GEN2.value, Model.C2000_GEN2.value], default=Model.C2000_GEN2.value)
    pair.add_argument("--client-id", help="Use an existing 40-character ID")
    pair.add_argument("--timezone", help="Station timezone, for example Europe/Vienna")
    pair.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    monitor = subcommands.add_parser("monitor", help="Print newline-delimited JSON status updates")
    monitor.add_argument("--name", help="Monitor only this configured device")
    monitor.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    diagnostics = subcommands.add_parser("network-diagnostics", help="Read radio HTTP, MQTT, Wi-Fi and reset codes")
    diagnostics.add_argument("--name", required=True)
    diagnostics.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    rssi = subcommands.add_parser("wifi-rssi", help="Read C1000 Gen 2 Prime RSSI; unavailable is null")
    rssi.add_argument("--name", required=True)
    rssi.add_argument("--timeout", type=float, default=20)
    rssi.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    serve = subcommands.add_parser("serve", help="Run HTTP monitoring with optional authenticated setting controls")
    serve.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--allow-control", action="store_true", help="Enable allowlisted HTTP commands; requires SOLIX_HTTP_TOKEN")
    serve.add_argument("--web-ui", action="store_true", help="Serve the optional local dashboard at /")
    serve.add_argument("--history-file", type=Path, help="Opt in to a private SQLite history file; no station requests")
    serve.add_argument("--history-retention-days", type=int, default=7, help="History retention, 1–365 days (default 7)")

    preview = subcommands.add_parser("charging-preview", help="Explain a charging policy from saved JSON; sends no commands")
    preview.add_argument("--snapshot-file", type=Path, required=True)
    preview.add_argument("--request-file", type=Path, required=True)
    preview.add_argument("--adaptive", action="store_true", help="Opt in to offline surplus/TOU proposals; no executor")

    replay = subcommands.add_parser("policy-replay", help="Replay saved adaptive-preview frames; no commands or physical prediction")
    replay.add_argument("--timeline-file", type=Path, required=True)
    replay.add_argument("--format", choices=("json", "svg"), default="json", help="JSON decisions or a standalone SVG visualization")

    mqtt = subcommands.add_parser("mqtt-bridge", help="Publish BLE status and supported settings through a local MQTT broker")
    mqtt.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    mqtt.add_argument("--broker", default="127.0.0.1")
    mqtt.add_argument("--port", type=int, default=1883)
    mqtt.add_argument("--topic-prefix", default="solix_gen2")
    mqtt.add_argument("--username")
    mqtt.add_argument("--password-file", type=Path)
    mqtt.add_argument("--ca-file", type=Path, help="Enable TLS with this trusted CA file")

    limits = subcommands.add_parser("set-limits", help="Set C1000 Prime charge/discharge limits")
    limits.add_argument("--name", required=True)
    limits.add_argument("--upper", type=int, required=True, help="Charging upper limit, 80–100 percent in 5 percent steps")
    limits.add_argument("--lower", type=int, required=True, help="Discharging lower limit: 1, 5, 10, 15, or 20 percent")
    limits.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    cap = subcommands.add_parser("set-charge-cap", help="Set the C2000 Gen 2 upper charge limit")
    cap.add_argument("--name", required=True)
    cap.add_argument("--upper", type=int, required=True, help="80–100 percent in 5 percent steps")
    cap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    power = subcommands.add_parser("set-charge-power", help="Set a supported station's AC charging-power limit")
    power.add_argument("--name", required=True)
    power.add_argument("--watts", type=int, required=True, help="C300:100/200/300/330; C1000:100–1000; C1000 Gen 2:100–1200; C2000 Gen 2:300–1800 W (100 W steps)")
    power.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    display = subcommands.add_parser("set-display-timeout", help="Set C300 AC, original C1000, or Gen 2 display timeout")
    display.add_argument("--name", required=True)
    display.add_argument("--seconds", type=int, required=True)
    display.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    brightness = subcommands.add_parser("set-display-brightness", help="Set original C1000 Prime display brightness")
    brightness.add_argument("--name", required=True)
    brightness.add_argument("--level", type=int, choices=[1, 2, 3], required=True, help="1 low, 2 medium, 3 high")
    brightness.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    timeout = subcommands.add_parser("set-device-timeout", help="Set supported C1000 idle shutdown; 0 means Never")
    timeout.add_argument("--name", required=True)
    timeout.add_argument("--minutes", type=int, choices=[0, 30, 60, 120, 240, 360, 720, 1440], required=True,
                         help="0 = Never; finite settings may turn the station off when idle and interrupt remote access")
    timeout.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    ac = subcommands.add_parser("set-ac-output", help="Set C300 AC or original C1000 AC output")
    ac.add_argument("--name", required=True)
    ac.add_argument("--enabled", choices=["on", "off"], required=True)
    ac.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    light = subcommands.add_parser("set-light", help="Set C300 AC or original C1000 light mode")
    light.add_argument("--name", required=True)
    light.add_argument("--mode", choices=["off", "low", "medium", "high", "sos"], required=True)
    light.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    from .c1000 import C1000_SETTINGS
    original = subcommands.add_parser("c1000-setting", help="Set an original C1000/A1761 control with fresh readback")
    original.add_argument("--name", required=True)
    original.add_argument("--setting", choices=C1000_SETTINGS, required=True)
    original.add_argument("--value", type=int, required=True, help="Integer value; enabled switches use 0 or 1")
    original.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    fast = subcommands.add_parser("set-fast-charge", help="Set original C1000 or C1000 Gen 2 fast charging")
    fast.add_argument("--name", required=True)
    fast.add_argument("--enabled", choices=["on", "off"], required=True)
    fast.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    temperature = subcommands.add_parser("set-temperature-unit", help="Set original C1000 temperature display")
    temperature.add_argument("--name", required=True)
    temperature.add_argument("--unit", choices=["celsius", "fahrenheit"], required=True)
    temperature.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    for name, label in (("ac", "AC"), ("dc", "DC")):
        saving = subcommands.add_parser(f"set-{name}-power-saving", help=f"Set original C1000 {label} power saving; may turn output off at low load")
        saving.add_argument("--name", required=True)
        saving.add_argument("--enabled", choices=["on", "off"], required=True)
        saving.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    wifi = subcommands.add_parser("wifi-setup", help="Provision original C1000 or Gen 2 Wi-Fi/API settings over Bluetooth")
    wifi.add_argument("--name", required=True)
    wifi.add_argument("--ssid", required=True)
    wifi.add_argument("--password-file", type=Path, help="Read Wi-Fi passphrase from a local file; otherwise prompt")
    wifi.add_argument("--api-url", required=True, help="API base URL supplied to the station")
    wifi.add_argument("--allow-http", action="store_true", help="Allow a local HTTP API URL for isolated AP-service use")
    wifi.add_argument("--account-id", help="40-character provisioning ID; Gen 2 uses its paired ID, original C1000 saves a generated local ID when needed")
    wifi.add_argument("--posix-timezone", default="UTC0")
    wifi.add_argument("--iana-timezone", default="Etc/UTC")
    wifi.add_argument("--country-code", default="US", help="Country code supplied during setup (default: US)")
    wifi.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    join = subcommands.add_parser("wifi-join", help="Join a WPA2 AP from C1000/C2000 Bluetooth without cloud setup")
    join.add_argument("--name", required=True)
    join.add_argument("--ssid", required=True)
    join.add_argument("--password-file", type=Path, help="Read Wi-Fi passphrase from a local file; otherwise prompt")
    join.add_argument("--account-id", help="40-character account ID; defaults to the paired local client ID")
    join.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    from .ap_service_cli import add_commands
    add_commands(subcommands)
    return command


def _upsert(device: DeviceConfig, path: Path) -> None:
    current = load_config(path)
    current = [saved for saved in current if saved.name != device.name]
    current.append(device)
    save_config(current, path)


async def _scan(timeout: float) -> None:
    for device in await discover(timeout=timeout):
        print(json.dumps({"name": device.name, "address": device.address}))


async def _pair(args: argparse.Namespace) -> None:
    existing = next((device for device in load_config(args.config) if device.name == args.name), None)
    client_id = args.client_id or (existing.client_id if existing else None)
    model = Model(args.model)
    timezone_name = args.timezone or (existing.timezone_name if existing else None)
    monitor = SolixMonitor(args.address, model=model, owner_user_id=client_id,
                           protocol="prime", timezone_name=timezone_name)
    connecting = asyncio.create_task(monitor.connect(timeout=120))
    pairing = asyncio.create_task(monitor.pairing_required.wait())
    try:
        done, _pending = await asyncio.wait({connecting, pairing}, return_when=asyncio.FIRST_COMPLETED)
        if pairing in done:
            print("Station requests physical pairing confirmation.", flush=True)
            await asyncio.to_thread(input, "Press its MAIN power button once, then press Enter here: ")
            await monitor.confirm_pairing()
        await connecting
        try:
            update = await monitor.wait_for_update(timeout=15)
            print(f"Connected; battery {update.get('battery_percentage', '?')}%, AC output {update.get('ac_output_enabled', '?')}")
        except TimeoutError:
            print("Registration succeeded; telemetry has not arrived yet")
        _upsert(DeviceConfig(args.name, args.address, model, monitor.owner_user_id,
                             "prime", timezone_name), args.config)
        print(f"Saved client ID to {args.config} (owner-only file permissions)")
    finally:
        pairing.cancel()
        if not connecting.done():
            connecting.cancel()
        await asyncio.gather(connecting, pairing, return_exceptions=True)
        await monitor.disconnect()


async def _monitor(args: argparse.Namespace) -> None:
    devices = load_config(args.config)
    if args.name:
        devices = [device for device in devices if device.name == args.name]
    service = MonitorService(devices)
    queue = service.subscribe()
    await service.start()
    try:
        for status in service.snapshots():
            print(json.dumps(status), flush=True)
        while True:
            print(json.dumps(await queue.get()), flush=True)
    finally:
        service.unsubscribe(queue)
        await service.stop()


async def _network_diagnostics(args: argparse.Namespace) -> None:
    device = next((saved for saved in load_config(args.config) if saved.name == args.name), None)
    if device is None:
        raise ValueError(f"Unknown configured device: {args.name}")
    if device.protocol != "prime":
        raise ValueError("Network diagnostics require a Prime station")
    async with SolixMonitor(
        device.address, model=device.model, owner_user_id=device.client_id,
        protocol=device.protocol, timezone_name=device.timezone_name,
    ) as monitor:
        print(json.dumps(await monitor.network_diagnostics()))


async def _wifi_rssi(args: argparse.Namespace) -> None:
    device = next((saved for saved in load_config(args.config) if saved.name == args.name), None)
    if device is None:
        raise ValueError(f"Unknown configured device: {args.name}")
    if device.model != Model.C1000_GEN2 or device.protocol != "prime":
        raise ValueError("Wi-Fi RSSI requires C1000 Gen 2 Prime")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise ValueError("RSSI timeout must be positive")
    async with SolixMonitor(
        device.address, model=device.model, owner_user_id=device.client_id,
        protocol=device.protocol, timezone_name=device.timezone_name,
    ) as monitor:
        print(json.dumps({"wifi_rssi_dbm": await monitor.wifi_rssi(timeout=args.timeout)}))


async def _set(args: argparse.Namespace) -> None:
    device = next((saved for saved in load_config(args.config) if saved.name == args.name), None)
    if device is None:
        raise ValueError(f"Unknown configured device: {args.name}")
    original_prime_output = (device.model == Model.C1000 and device.protocol == "prime"
                             and args.command == "set-ac-output" and original_prime_operation_supported("ac_output"))
    if device.model == Model.C1000 and device.protocol == "prime" and args.command not in original_prime_commands() and not original_prime_output:
        raise ValueError("This control is not verified for original C1000 Prime firmware")
    legacy_setting = (device.protocol == "legacy" and device.model in (Model.C300, Model.C1000) and args.command in (
        "set-display-timeout", "set-charge-power", "set-ac-output", "set-light",
    ))
    legacy_setting |= (device.model == Model.C1000 and device.protocol == "legacy"
                       and args.command in ("set-device-timeout", "set-temperature-unit", "set-fast-charge",
                                            "set-ac-power-saving", "set-dc-power-saving"))
    prime_setting = device.protocol == "prime" and (
        (device.model == Model.C1000 and (args.command in original_prime_commands() or original_prime_output))
        or (device.model == Model.C1000_GEN2 and args.command in ("set-limits", "set-display-timeout", "set-charge-power", "set-fast-charge", "set-charge-cap", "set-device-timeout"))
        or (device.model == Model.C2000_GEN2 and args.command in ("set-display-timeout", "set-charge-power", "set-charge-cap"))
    )
    if not legacy_setting and not prime_setting:
        raise ValueError("This setting is not verified for the selected device")
    if args.command == "set-charge-cap" and device.model != Model.C2000_GEN2:
        raise ValueError("set-charge-cap is verified only on C2000 Gen 2 Prime")
    if args.command in ("set-fast-charge", "set-ac-power-saving", "set-dc-power-saving", "set-ac-output") and args.enabled not in ("on", "off"):
        raise ValueError("Enabled must be on or off")
    if args.command == "set-temperature-unit" and args.unit not in ("celsius", "fahrenheit"):
        raise ValueError("Unit must be celsius or fahrenheit")
    if args.command == "set-fast-charge" and device.model == Model.C1000:
        print(ORIGINAL_FAST_CHARGE_WARNING, file=sys.stderr)
    if args.command in ("set-ac-power-saving", "set-dc-power-saving"):
        print("Power saving may automatically turn the output off at low load.", file=sys.stderr)
        if device.protocol == "prime":
            print(ORIGINAL_AC_SMART_WARNING if args.command == "set-ac-power-saving" else ORIGINAL_DC_SMART_WARNING, file=sys.stderr)
    if args.command == "set-device-timeout":
        if type(args.minutes) is not int or args.minutes not in (0, 30, 60, 120, 240, 360, 720, 1440):
            raise ValueError("Device Timeout must be 0 (Never), 30, 60, 120, 240, 360, 720 or 1440 minutes")
        if args.minutes:
            print("The station may turn off when idle, interrupting remote access.", file=sys.stderr)
        print("Never disables this timeout; other sleep behavior may still interrupt remote access.", file=sys.stderr)
    monitor = SolixMonitor(
        device.address, model=device.model, owner_user_id=device.client_id, protocol=device.protocol,
        timezone_name=device.timezone_name,
    )
    try:
        await monitor.connect(timeout=30)
        if args.command == "set-limits":
            metrics = await monitor.set_charge_limits(args.upper, args.lower)
            result = {"max_charge_percentage": metrics["max_charge_percentage"],
                      "min_charge_percentage": metrics["min_charge_percentage"]}
        elif args.command == "set-charge-cap":
            metrics = await monitor.set_charge_cap(args.upper)
            result = {"max_charge_percentage": metrics["max_charge_percentage"],
                      "min_charge_percentage": metrics["min_charge_percentage"]}
        elif args.command == "set-charge-power":
            metrics = await monitor.set_ac_charging_power(args.watts)
            result = {"ac_charging_power_limit_w": metrics["ac_charging_power_limit_w"]}
        elif args.command == "set-display-timeout":
            metrics = await monitor.set_display_timeout(args.seconds)
            result = {"display_timeout_seconds": metrics["display_timeout_seconds"]}
        elif args.command == "set-display-brightness":
            metrics = await monitor.set_c1000_setting("display_brightness", args.level)
            result = {"display_brightness": metrics["display_brightness"]}
        elif args.command == "set-device-timeout":
            metrics = await monitor.set_device_timeout(args.minutes)
            result = {"device_timeout_minutes": metrics["device_timeout_minutes"]}
        elif args.command == "set-temperature-unit":
            metrics = await monitor.set_temperature_unit(args.unit == "fahrenheit")
            result = {"temperature_unit_fahrenheit": metrics["temperature_unit_fahrenheit"]}
        elif args.command in ("set-ac-power-saving", "set-dc-power-saving"):
            method, field = ((monitor.set_ac_power_saving_enabled, "ac_power_saving_mode_enabled")
                             if args.command == "set-ac-power-saving"
                             else (monitor.set_dc_power_saving_enabled, "dc_power_saving_mode_enabled"))
            metrics = await method(args.enabled == "on")
            result = {field: metrics[field]}
        elif args.command == "set-ac-output":
            metrics = await monitor.set_ac_output_enabled(args.enabled == "on")
            result = {"ac_output_enabled": metrics["ac_output_enabled"]}
        elif args.command == "set-light":
            mode = ("off", "low", "medium", "high", "sos").index(args.mode)
            metrics = await monitor.set_light_mode(mode)
            result = {"light_mode": metrics["light_mode"]}
        else:
            metrics = await monitor.set_fast_charge_enabled(args.enabled == 'on')
            result = {"ac_fast_charge_enabled": metrics["ac_fast_charge_enabled"]}
        print(json.dumps({"name": device.name, "confirmed": result}))
    finally:
        await monitor.disconnect()


async def _c1000_setting(args: argparse.Namespace) -> None:
    device = next((saved for saved in load_config(args.config) if saved.name == args.name), None)
    if device is None or device.model != Model.C1000:
        raise ValueError("This command requires an original C1000/A1761 config")
    if device.protocol == "prime" and args.setting not in C1000_PRIME_SETTINGS:
        raise ValueError("This control is not verified for original C1000 Prime firmware")
    value = args.value
    if args.setting.endswith("_enabled") or args.setting == "temperature_unit_fahrenheit":
        if value not in (0, 1):
            raise ValueError("Enabled switches accept only 0 or 1")
        value = bool(value)
    from .c1000 import c1000_setting
    _command, _payload, expected = c1000_setting(args.setting, value)
    async with SolixMonitor(device.address, model=device.model, protocol=device.protocol,
                            owner_user_id=device.client_id, timezone_name=device.timezone_name) as monitor:
        await monitor.wait_for_update(timeout=15)
        baseline = {field: monitor.metrics.get(field) for field in expected}
        if any(item is None for item in baseline.values()):
            raise RuntimeError("Baseline telemetry is missing the setting; no write sent")
        print(json.dumps({"baseline": baseline}), flush=True)
        metrics = await monitor.set_c1000_setting(args.setting, value)
        print(json.dumps({"confirmed": {field: metrics[field] for field in expected}}))


async def _wifi_setup(args: argparse.Namespace) -> None:
    device = next((saved for saved in load_config(args.config) if saved.name == args.name), None)
    if device is None:
        raise ValueError(f"Unknown configured device: {args.name}")
    original = device.model == Model.C1000 and device.protocol in ("legacy", "prime")
    if not original and not (device.protocol == "prime" and device.model in (Model.C1000_GEN2, Model.C2000_GEN2)):
        raise ValueError("Wi-Fi setup requires an original C1000 legacy/Prime or Gen 2 Prime station")
    account_id = args.account_id if args.account_id is not None else device.client_id
    if original and device.protocol == "legacy" and account_id is None:
        from dataclasses import replace
        import secrets
        account_id = secrets.token_hex(20)
        device = replace(device, client_id=account_id)
        _upsert(device, args.config)
        print("Generated and saved a local provisioning ID", file=sys.stderr)
    if account_id is None:
        raise ValueError("A paired client ID or --account-id is required")
    passphrase = (args.password_file.read_text().rstrip('\r\n') if args.password_file
                  else getpass.getpass('Wi-Fi passphrase: '))
    monitor = SolixMonitor(
        device.address, model=device.model, owner_user_id=device.client_id, protocol=device.protocol,
        timezone_name=device.timezone_name,
    )
    try:
        await monitor.connect(timeout=35)
        if args.command == 'wifi-join':
            replies = {'4824': await monitor.join_wifi(
                ssid=args.ssid, passphrase=passphrase, account_id=account_id,
            )}
        else:
            replies = await monitor.send_wifi_provisioning(
                ssid=args.ssid, passphrase=passphrase, account_id=account_id,
                api_url=args.api_url, posix_timezone=args.posix_timezone,
                iana_timezone=args.iana_timezone, allow_http=args.allow_http,
                country_code=args.country_code,
            )
        print(json.dumps({"name": device.name, "ble_replies": replies}))
    finally:
        await monitor.disconnect()


def tui_available() -> bool:
    return find_spec("textual") is not None


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        if not sys.stdin.isatty():
            print("Interactive mode needs a terminal; use --help for scripted commands.", file=sys.stderr)
            return 2
        if tui_available():
            argv = ["tui"]
        else:
            print("Using the line menu. For the full-screen dashboard, install the tui extra: "
                  "python -m pip install 'solix-link[tui]'", file=sys.stderr)
            argv = ["interactive"]
    args = parser().parse_args(argv)
    try:
        if args.command == "interactive":
            from .interactive import run_interactive
            options = {"gateway_url": args.gateway_url, "gateway_token_file": args.gateway_token_file} if args.gateway_url or args.gateway_token_file else {}
            run_interactive(args.config, args.ap_service_directory, **options)
        elif args.command == "tui":
            from .tui import run_tui
            options = {"gateway_url": args.gateway_url, "gateway_token_file": args.gateway_token_file} if args.gateway_url or args.gateway_token_file else {}
            run_tui(args.config, args.ap_service_directory, **options)
        elif args.command == "gateway-history":
            from .gateway_client import GatewayClient
            client = GatewayClient(args.gateway_url, args.gateway_token_file)
            print(json.dumps(client.history(args.name, since=args.since, until=args.until, limit=args.limit), indent=2))
        elif args.command == "scan":
            asyncio.run(_scan(args.timeout))
        elif args.command == "ble-inspect":
            from .ble_inspection import inspect_ble_features
            report = asyncio.run(inspect_ble_features(
                Model(args.model), scan_timeout=args.timeout,
                connect=args.connect, connect_timeout=args.connect_timeout))
            print(json.dumps(report, indent=2))
            return 0 if report["result"] in ("discovered", "inspected") else 1
        elif args.command == "add":
            device = DeviceConfig(args.name, args.address, Model(args.model), args.client_id,
                                  args.protocol, args.timezone)
            _upsert(device, args.config)
            print(f"Saved {device.name} to {args.config}")
        elif args.command == "pair":
            asyncio.run(_pair(args))
        elif args.command == "monitor":
            asyncio.run(_monitor(args))
        elif args.command == "network-diagnostics":
            asyncio.run(_network_diagnostics(args))
        elif args.command == "wifi-rssi":
            asyncio.run(_wifi_rssi(args))
        elif args.command == "serve":
            from .server import run_server
            run_server(MonitorService(load_config(args.config)), host=args.host, port=args.port,
                       allow_control=args.allow_control, web_ui=args.web_ui,
                       history_file=args.history_file, history_retention_days=args.history_retention_days)
        elif args.command == "charging-preview":
            from .charging_preview_cli import offline_preview
            print(json.dumps(offline_preview(args.snapshot_file, args.request_file, adaptive=args.adaptive), indent=2))
        elif args.command == "policy-replay":
            from .charging_preview_cli import read_document
            from .policy_replay import MAX_REPLAY_BYTES, adaptive_timeline_svg, replay_adaptive_timeline
            document = read_document(args.timeline_file, MAX_REPLAY_BYTES)
            if args.format == "svg":
                print(adaptive_timeline_svg(document), end="")
            else:
                print(json.dumps(replay_adaptive_timeline(document), indent=2))
        elif args.command == "mqtt-bridge":
            from .mqtt_bridge import MqttBridge
            asyncio.run(MqttBridge(
                MonitorService(load_config(args.config)), host=args.broker, port=args.port,
                topic_prefix=args.topic_prefix, username=args.username,
                password_file=args.password_file, ca_file=args.ca_file,
            ).run())
        elif args.command in ("set-limits", "set-charge-cap", "set-charge-power", "set-display-timeout", "set-display-brightness", "set-device-timeout", "set-fast-charge", "set-temperature-unit", "set-ac-power-saving", "set-dc-power-saving", "set-ac-output", "set-light"):
            asyncio.run(_set(args))
        elif args.command == "c1000-setting":
            asyncio.run(_c1000_setting(args))
        elif args.command in ("wifi-setup", "wifi-join"):
            asyncio.run(_wifi_setup(args))
        elif args.command.startswith("ap-service-"):
            from .ap_service_cli import dispatch
            dispatch(args)
        return 0
    except (KeyboardInterrupt, asyncio.CancelledError):
        return 130
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
