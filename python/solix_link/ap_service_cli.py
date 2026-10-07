"""CLI orchestration for the isolated local Wi-Fi/MQTT endpoint."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import signal
import sys
from dataclasses import replace

from .client import SolixMonitor
from .config import DEFAULT_CONFIG, load_config
from .isolated_ap import IsolatedAP
from .ap_service_config import APServiceConfig, add_ap_service_device, initialize_ap_service, load_ap_service, load_ap_service_profiles, private_write
from .ap_service import ap_service_request
from .protocol import DEVICE_TIMEOUT_MINUTES, Model, timezone_confer
from .tou import TouPeriod
from .c1000_capabilities import ORIGINAL_AC_SMART_WARNING, ORIGINAL_DC_SMART_WARNING, ORIGINAL_FAST_CHARGE_WARNING


def add_commands(subcommands) -> None:
    check = subcommands.add_parser("ap-service-check", help="Check saved AP profiles and credentials offline with redacted results; no services or station requests")
    check.add_argument("--directory", type=Path, required=True)
    check.add_argument("--config", type=Path, help="Optional owner-only saved BLE config for provisioning-readiness checks")

    init = subcommands.add_parser("ap-service-init", help="Generate private isolated-AP configuration and local MQTT certificates")
    init.add_argument("--directory", type=Path, required=True, help="New private directory; existing directories are refused")
    init.add_argument("--name", required=True, help="Paired Prime original C1000, C1000 Gen 2 or C2000 Gen 2 config name")
    init.add_argument("--serial-file", type=Path, required=True, help="Owner-only serial file: 16 characters for original C1000, 17 for Gen 2")
    init.add_argument("--account-id-file", type=Path, help="Otherwise use the paired BLE client ID")
    init.add_argument("--interface", required=True, help="Dedicated, unused Linux Wi-Fi interface")
    init.add_argument("--phy", required=True)
    init.add_argument("--country", required=True, help="Wi-Fi regulatory country, for example AT")
    init.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    add = subcommands.add_parser("ap-service-add", help="Register another paired Prime station on the same stopped AP")
    add.add_argument("--directory", type=Path, required=True)
    add.add_argument("--name", required=True, help="Existing paired Prime config name")
    add.add_argument("--serial-file", type=Path, required=True)
    add.add_argument("--account-id-file", type=Path)
    add.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    run = subcommands.add_parser("ap-service-run", help="Run an isolated WPA2 AP and local API/NTP/native MQTT endpoint (root required)")
    run.add_argument("--directory", type=Path, required=True)
    run.add_argument("--provision", action="store_true", help="Send local Wi-Fi/API settings through the saved BLE pairing")
    run.add_argument("--name", help="Select which registered station to provision; required for multiple stations")
    run.add_argument("--allow-control", action="store_true", help="Enable native charging, tariff and supported C1000 settings via the private Unix socket")
    run.add_argument("--energy-reports", action="store_true", help="Enable local energy reporting; counter units remain unverified")
    run.add_argument("--duration", type=int, help="Stop after this many seconds; default: run until Ctrl-C")
    run.add_argument("--hostapd", default="hostapd", help="Executable name or absolute path")
    run.add_argument("--dnsmasq", default="dnsmasq", help="Executable name or absolute path")
    run.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    for command, help_text in (("ap-service-status", "Query live native MQTT status"),
                               ("ap-service-readiness", "Read native controller readiness without writing settings"),
                               ("ap-service-wireless-state", "Read C1000 Gen 2 radio application flags without changing Bluetooth or Wi-Fi"),
                               ("ap-service-wifi-rssi", "Query C1000 Gen 2 native radio RSSI; unavailable is null"),
                               ("ap-service-set-charge-power", "Set and confirm supported native MQTT charging power"),
                               ("ap-service-set-charge-cap", "Set and confirm the Gen 2 native MQTT upper charge limit"),
                               ("ap-service-set-discharge-floor", "Set C1000 Gen 2 lower discharge limit without adjusting reserve"),
                               ("ap-service-set-temperature-unit", "Set and confirm original/Gen 2 C1000 temperature units"),
                               ("ap-service-set-off-grid-alert", "Set and confirm C1000 Gen 2 off-grid notification"),
                               ("ap-service-set-device-timeout", "Set original/Gen 2 C1000 device timeout; 0 = Never"),
                               ("ap-service-set-fast-charge", "Set original/Gen 2 C1000 fast charge with fresh retained readback"),
                               ("ap-service-set-display-brightness", "Set original/Gen 2 C1000 native MQTT display brightness"),
                               ("ap-service-set-clock-brightness", "Set C1000 Gen 2 inactive clock-window brightness selector"),
                               ("ap-service-set-display-timeout", "Set original/Gen 2 C1000 native MQTT screen timeout"),
                               ("ap-service-set-light", "Set and confirm original C1000 native MQTT light mode"),
                               ("ap-service-set-dc-power-saving", "Set original/Gen 2 C1000 native DC Smart; requires DC output OFF"),
                               ("ap-service-set-ac-power-saving", "Set original/Gen 2 C1000 native AC Smart; requires AC OFF and inactive countdowns"),
                               ("ap-service-set-ac-output", "Local C1000 Gen 2 AC socket control; never exposed by HTTP or HA"),
                               ("ap-service-set-ac-countdown", "Local C1000 Gen 2 AC expiry timer; zero cancels remaining time"),
                               ("ap-service-set-port-memory", "Set C1000 Gen 2 native MQTT output-port memory"),
                               ("ap-service-set-reserve", "Set and confirm backup reserve without changing outputs"),
                               ("ap-service-set-tou", "Replace the native hourly schedule; explicit activation persists until changed"),
                               ("ap-service-grid", "Clear the plan and confirm return to grid power without toggling AC output")):
        parser = subcommands.add_parser(command, help=help_text)
        parser.add_argument("--directory", type=Path, required=True)
        parser.add_argument("--name", help="Target station; required for writes when multiple stations share the AP")
        if command == "ap-service-set-charge-power":
            parser.add_argument("--watts", type=int, required=True,
                                help="100 W steps: 100–1000 W (original C1000), 100–1200 W (C1000 Gen 2), 300–1800 W (C2000 Gen 2)")
        elif command == "ap-service-set-charge-cap":
            parser.add_argument("--upper", type=int, required=True)
        elif command == "ap-service-set-discharge-floor":
            parser.add_argument("--lower", type=int, choices=[1, 5, 10, 15, 20], required=True)
        elif command == "ap-service-set-reserve":
            parser.add_argument("--reserve", type=int, required=True)
        elif command == "ap-service-set-temperature-unit":
            parser.add_argument("--unit", choices=["celsius", "fahrenheit"], required=True)
        elif command == "ap-service-set-off-grid-alert":
            parser.add_argument("--state", choices=["on", "off"], required=True)
        elif command == "ap-service-set-fast-charge":
            parser.add_argument("--enabled", choices=["on", "off"], required=True)
        elif command == "ap-service-set-ac-output":
            parser.add_argument("--enabled", choices=["on", "off"], required=True,
                                help="Changes power at AC sockets; use only on noncritical C1000 Gen 2 loads")
        elif command == "ap-service-set-ac-countdown":
            parser.add_argument("--seconds", type=int, required=True,
                                help="0 or 600–86400; expiry stops AC. Cancel early: zero cannot revoke an already queued stop")
        elif command in ("ap-service-set-dc-power-saving", "ap-service-set-ac-power-saving"):
            parser.add_argument("--enabled", choices=["on", "off"], required=True,
                                help="On selects Smart; Off selects Normal. " + (
                                    ORIGINAL_AC_SMART_WARNING if command == "ap-service-set-ac-power-saving" else ORIGINAL_DC_SMART_WARNING))
        elif command == "ap-service-set-display-brightness":
            parser.add_argument("--level", type=int, choices=[1, 2, 3], required=True,
                                help="1 low, 2 medium, 3 high; zero is not a brightness level")
        elif command == "ap-service-set-clock-brightness":
            parser.add_argument("--window", type=int, choices=[1, 2], required=True)
            parser.add_argument("--high", choices=["on", "off"], required=True,
                                help="Stored window brightness only; requires disabled clock and no asset transfer")
        elif command == "ap-service-set-display-timeout":
            parser.add_argument("--seconds", type=int, choices=[0, 10, 20, 30, 60, 300, 1800], required=True,
                                help="Screen timeout in seconds; original C1000 excludes 0/10; Gen 2 0 means Never")
        elif command == "ap-service-set-port-memory":
            parser.add_argument("--enabled", choices=["on", "off"], required=True,
                                help="Off clears output-recovery bookkeeping; turning On does not restore it")
        elif command == "ap-service-set-light":
            parser.add_argument("--mode", choices=["off", "low", "medium", "high", "sos"], required=True)
        elif command == "ap-service-set-device-timeout":
            parser.add_argument("--minutes", type=int, choices=DEVICE_TIMEOUT_MINUTES, required=True,
                                help="0 disables this timeout; independent sleep behavior may remain")
        elif command == "ap-service-set-tou":
            parser.add_argument("--mode", choices=["standard", "time_of_use"], required=True)
            parser.add_argument("--period", action="append", default=[], metavar="TARIFF:START:END",
                                help="Repeat up to six times; peak, mid_peak or off_peak with whole local hours, e.g. peak:0:24")
        elif command == "ap-service-grid":
            parser.add_argument("--timeout", type=int, default=30, help="5–120 seconds per power-flow confirmation phase")
    serve = subcommands.add_parser("ap-service-serve", help="Expose AP-service HTTP/SSE/metrics with optional authenticated commands")
    serve.add_argument("--directory", type=Path, required=True)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--allow-control", action="store_true", help="Enable HTTP commands; requires SOLIX_HTTP_TOKEN and a control-enabled worker")
    serve.add_argument("--web-ui", action="store_true", help="Serve the optional local dashboard at /")
    serve.add_argument("--wifi-rssi", action="store_true", help="Opt in to read-only C1000 Gen 2 Wi-Fi signal queries every 5 minutes; main 1.1.4.9 / radio 0.3.3.0 only")
    serve.add_argument("--history-file", type=Path, help="Opt in to a private SQLite history file; no station requests")
    serve.add_argument("--history-retention-days", type=int, default=7, help="History retention, 1–365 days (default 7)")
    serve.add_argument("--permissions-file", type=Path, help="Owner-only per-device token scopes; replaces SOLIX_HTTP_TOKEN")
    serve.add_argument("--activity-file", type=Path, help="Persist sanitized UPS/settings/command events in private SQLite")
    serve.add_argument("--activity-retention-days", type=int, default=7)
    preview = subcommands.add_parser("ap-service-charging-preview", help="Explain a charging policy using cached AP status; no worker or station requests")
    preview.add_argument("--directory", type=Path, required=True)
    preview.add_argument("--name", help="Configured station; defaults to the primary profile")
    preview.add_argument("--request-file", type=Path, required=True)
    preview.add_argument("--adaptive", action="store_true", help="Opt in to read-only surplus/TOU proposals")


def _device(args, name: str):
    device = next((device for device in load_config(args.config) if device.name == name), None)
    if device is None or device.model not in (Model.C1000, Model.C1000_GEN2, Model.C2000_GEN2) or device.protocol != "prime" or not device.client_id:
        raise ValueError("Local MQTT setup requires a paired Prime original C1000, C1000 Gen 2 or C2000 Gen 2")
    return device


def _secret(path: Path) -> str:
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError("Identifier file must have owner-only permissions")
    return path.read_text().strip()


async def run_ap_service(args) -> None:
    if args.duration is not None and args.duration <= 0:
        raise ValueError("Duration must be positive")
    directory = args.directory.resolve()
    config = load_ap_service(directory / "ap_service.json")
    profiles = load_ap_service_profiles(directory, config)
    provision_config = config
    if args.provision:
        name = getattr(args, "name", None)
        if name is None and len(profiles) > 1:
            raise ValueError("Select the station to provision with --name")
        if name is not None:
            if name not in profiles:
                raise ValueError("Unknown station name")
            provision_config = profiles[name][0]
    ap = IsolatedAP(config, directory, hostapd=args.hostapd, dnsmasq=args.dnsmasq)
    monitor = None
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    loop.add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        if args.provision:
            device = _device(args, provision_config.name)
            if device.model != provision_config.model:
                raise ValueError("Paired device model does not match AP-service configuration")
            monitor = SolixMonitor(device.address, model=device.model, owner_user_id=device.client_id,
                                   protocol=device.protocol, timezone_name=provision_config.timezone_name)
            await monitor.connect(timeout=35)
            await monitor.wait_for_update(timeout=15)
            await monitor.request_status()
            baseline = await monitor.wait_for_update(timeout=15)
            if baseline.get("serial_number") != provision_config.device_serial:
                raise ValueError("Bluetooth device serial does not match AP-service configuration")
            private_write(directory / "provisioning-baseline.json", json.dumps(baseline))
        # Keep startup synchronous so cancellation cannot outlive cleanup in a thread.
        ap.start()
        worker = [sys.executable, "-m", "solix_link.ap_service_worker", "--directory", str(directory)]
        if args.allow_control:
            worker.append("--allow-control")
        if args.energy_reports:
            worker.append("--energy-reports")
        (directory / "ready").unlink(missing_ok=True)
        ap.spawn(worker, "service.log")
        async with asyncio.timeout(15):
            while not (directory / "ready").exists():
                ap.check()
                await asyncio.sleep(0.25)
        if monitor:
            replies = await monitor.send_wifi_provisioning(
                ssid=config.ssid, passphrase=config.passphrase, account_id=provision_config.account_id,
                api_url=config.api_url, posix_timezone=timezone_confer(provision_config.timezone_name)[1].decode(),
                iana_timezone=provision_config.timezone_name, allow_http=True,
                country_code=provision_config.country,
            )
            private_write(directory / "provisioning-replies.json", json.dumps(replies))
            if replies["4824"] not in ("00", "timeout"):
                raise RuntimeError("Station rejected Wi-Fi credentials")
            await monitor.disconnect()
            monitor = None
        print(json.dumps({"event": "ap_service_started", "names": list(profiles), "control_enabled": args.allow_control}), flush=True)
        loop = asyncio.get_running_loop()
        end = loop.time() + args.duration if args.duration else None
        previous = None
        while end is None or loop.time() < end:
            ap.check()
            status = await ap_service_request(directory, "status")
            if status != previous:
                print(json.dumps(status), flush=True)
                previous = status
            await asyncio.sleep(1)
    finally:
        if monitor:
            await monitor.disconnect()
        # Service shutdown precedes returning the adapter; no power commands are sent.
        ap.stop()
        loop.remove_signal_handler(signal.SIGTERM)


def dispatch(args) -> None:
    if args.command == "ap-service-check":
        from .ap_service_check import check_ap_service
        result = check_ap_service(args.directory, paired_config=args.config)
        print(json.dumps(result, indent=2))
        if not result["ok"]:
            raise ValueError("Saved AP setup has errors; inspect the redacted check results")
    elif args.command == "ap-service-init":
        device = _device(args, args.name)
        config = APServiceConfig(name=device.name, interface=args.interface, phy=args.phy, country=args.country,
                           device_serial=_secret(args.serial_file),
                           account_id=_secret(args.account_id_file) if args.account_id_file else device.client_id,
                           timezone_name=device.timezone_name or "Etc/UTC", model=device.model)
        initialize_ap_service(args.directory, config)
        print(f"Created private local AP and MQTT credentials in {args.directory}")
    elif args.command == "ap-service-run":
        asyncio.run(run_ap_service(args))
    elif args.command == "ap-service-add":
        device = _device(args, args.name)
        parent = load_ap_service(args.directory / "ap_service.json")
        config = replace(parent, name=device.name, model=device.model, device_serial=_secret(args.serial_file),
                         account_id=_secret(args.account_id_file) if args.account_id_file else device.client_id,
                         timezone_name=device.timezone_name or "Etc/UTC")
        add_ap_service_device(args.directory, config)
        print(f"Added {device.name} to the shared AP profile")
    elif args.command == "ap-service-serve":
        from .ap_service_monitor import APServiceMonitor
        from .server import run_server
        run_server(APServiceMonitor(load_ap_service(args.directory / "ap_service.json"), args.directory,
                                   wifi_rssi=args.wifi_rssi), args.host, args.port,
                   allow_control=args.allow_control, web_ui=args.web_ui,
                   history_file=args.history_file, history_retention_days=args.history_retention_days,
                   permissions_file=args.permissions_file, activity_file=args.activity_file,
                   activity_retention_days=args.activity_retention_days)
    elif args.command == "ap-service-charging-preview":
        from .charging_preview_cli import native_preview
        print(json.dumps(native_preview(args.directory, args.name, args.request_file, adaptive=args.adaptive), indent=2))
    else:
        command = {"ap-service-status": "status", "ap-service-readiness": "readiness", "ap-service-set-charge-power": "set-charge-power",
                   "ap-service-wireless-state": "wireless-state",
                   "ap-service-wifi-rssi": "wifi-rssi",
                   "ap-service-set-charge-cap": "set-charge-cap", "ap-service-set-reserve": "set-backup-reserve",
                   "ap-service-set-discharge-floor": "set-discharge-floor",
                   "ap-service-set-temperature-unit": "set-temperature-unit",
                   "ap-service-set-off-grid-alert": "set-off-grid-alert",
                   "ap-service-set-device-timeout": "set-device-timeout",
                   "ap-service-set-fast-charge": "set-fast-charge",
                   "ap-service-set-display-brightness": "set-display-brightness",
                   "ap-service-set-clock-brightness": "set-clock-brightness",
                   "ap-service-set-ac-output": "set-ac-output",
                   "ap-service-set-ac-countdown": "set-ac-countdown",
                   "ap-service-set-display-timeout": "set-display-timeout",
                   "ap-service-set-light": "set-light",
                   "ap-service-set-dc-power-saving": "set-dc-power-saving",
                   "ap-service-set-ac-power-saving": "set-ac-power-saving",
                   "ap-service-set-port-memory": "set-port-memory",
                   "ap-service-set-tou": "set-tou-plan", "ap-service-grid": "return-grid"}[args.command]
        fields = ({"watts": args.watts} if args.command == "ap-service-set-charge-power" else
                  {"upper": args.upper} if args.command == "ap-service-set-charge-cap" else {})
        if args.command == "ap-service-set-reserve":
            fields = {"reserve": args.reserve}
        elif args.command == "ap-service-set-discharge-floor":
            fields = {"lower": args.lower}
        elif args.command == "ap-service-set-temperature-unit":
            fields = {"fahrenheit": args.unit == "fahrenheit"}
        elif args.command == "ap-service-set-off-grid-alert":
            fields = {"enabled": args.state == "on"}
        elif args.command == "ap-service-set-fast-charge":
            fields = {"enabled": args.enabled == "on"}
            config_path = args.directory / "ap_service.json"
            if config_path.exists():
                config = load_ap_service(config_path)
                profiles = load_ap_service_profiles(args.directory, config)
                name = args.name or (config.name if len(profiles) == 1 else None)
                if name in profiles and profiles[name][0].model == Model.C1000:
                    print(ORIGINAL_FAST_CHARGE_WARNING, file=sys.stderr)
        elif args.command == "ap-service-set-display-brightness":
            fields = {"level": args.level}
        elif args.command == "ap-service-set-clock-brightness":
            fields = {"window": args.window, "high": args.high == "on"}
        elif args.command == "ap-service-set-ac-output":
            fields = {"enabled": args.enabled == "on"}
        elif args.command == "ap-service-set-ac-countdown":
            print("An AC countdown eventually stops AC. Zero clears remaining time but cannot revoke a queued stop; use only noncritical C1000 Gen 2 loads.", file=sys.stderr)
            fields = {"seconds": args.seconds}
        elif args.command == "ap-service-set-display-timeout":
            fields = {"seconds": args.seconds}
        elif args.command == "ap-service-set-light":
            fields = {"mode": ("off", "low", "medium", "high", "sos").index(args.mode)}
        elif args.command in ("ap-service-set-dc-power-saving", "ap-service-set-ac-power-saving"):
            print(ORIGINAL_AC_SMART_WARNING if args.command == "ap-service-set-ac-power-saving" else ORIGINAL_DC_SMART_WARNING, file=sys.stderr)
            fields = {"enabled": args.enabled == "on"}
        elif args.command == "ap-service-set-port-memory":
            fields = {"enabled": args.enabled == "on"}
        elif args.command == "ap-service-set-device-timeout":
            fields = {"minutes": args.minutes}
        elif args.command == "ap-service-grid":
            fields = {"timeout": args.timeout}
        elif args.command == "ap-service-set-tou":
            periods = []
            for text in args.period:
                parts = text.split(":")
                if len(parts) != 3:
                    raise ValueError("Period format must be TARIFF:START:END")
                periods.append(TouPeriod(parts[0], int(parts[1]), int(parts[2])).to_dict())
            fields = {"periods": periods, "enabled": args.mode == "time_of_use"}
        if args.name is not None:
            fields["name"] = args.name
        print(json.dumps(asyncio.run(ap_service_request(args.directory, command, **fields))))
