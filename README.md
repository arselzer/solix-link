# SOLIX Link

Local monitoring and controls for Anker SOLIX power stations over **Bluetooth**
or **native MQTT on an isolated Wi-Fi AP**. Async Python library, CLI, terminal
dashboard, optional FastAPI/Vue web dashboard and Home Assistant integration.

## Supported devices

| Device | Python Bluetooth | Native local MQTT | Tested / limitations |
| --- | --- | --- | --- |
| C1000 Gen 2, A1763 | Monitoring, charge limits/power, display timeout, fast charge | Charging/fast/reserve, tariffs, display/memory, discharge floor, temperature/alert, guarded Smart and clock brightness | Main 1.1.4.9 / radio 0.3.3.0; Smart requires its output off and inactive countdowns; older 1.1.4.3 uses legacy BLE |
| C2000 Gen 2, A1783 | Monitoring, power/cap, display timeout | Charging/reserve, tariffs and return to grid | Main 2.1.6.4; AC-output writes blocked |
| Original C1000, A1761 | Twelve legacy controls; ten Prime controls | Charging power, Fast, device/screen timeout, brightness, light, temperature unit and guarded AC/DC Smart | Legacy 1.5.1; Prime/native main 1.7.1 / radio 0.3.3.0; Smart requires its output off; AC Smart/Prime AC output require no active AC timer; charging rates/reboot retention unverified |
| C300/C300X AC, A1722/A1723 | Monitoring, AC output, light, charging power, display timeout | — | C300X tested; C300 sibling untested; C300 DC unsupported |
| Solarbank 3 E2700 Pro, A17C5 | Separate Web Bluetooth app | — | Browser telemetry tested; no Python profile |

Native MQTT connects the station itself to the AP service. The optional
BLE-to-MQTT bridge publishes Bluetooth readings to your existing broker.
Original C1000, C1000 Gen 2 and C2000 Gen 2 passed simultaneous native MQTT,
HA discovery and shared-service recovery checks; see the
[runtime validation](docs/ha-runtime-validation.md). Controls are model and
firmware specific; detailed capabilities are in the [Python guide](python/README.md).

## What you can do

- Monitor battery, temperature, power, outputs and supported settings.
- Set charging power/limits; use Gen 2 reserve and hourly Time-of-Use plans for
  battery use with mains connected, with confirmed return to grid.
- Manage up to eight stations on one isolated AP through terminal, browser or
  authenticated HTTP/SSE; expose Prometheus metrics and scoped device tokens.
- Review UPS observations, command history, control prerequisites and partial
  settings exports. Concurrent HTTP writes are coordinated per station.
- Save battery/power charts and **estimated AC input/output kWh** with coverage
  and gaps; view Gen 2 [native energy counters and nominal kWh](docs/native-energy-values.md)
  in the API, terminal F7, browser and HA. All supported models have
  [HA Energy-compatible AC estimates](docs/home-assistant-energy-dashboard.md);
  both Gen 2 models also expose [observed native Standard AC estimates](docs/native-energy-meter.md).
- Preview solar/price charging policies; optionally prepare HA charging
  blueprints. Previews send no commands and automations start disabled.

## Start locally

Bluetooth requires Python 3.11+ and a working adapter. From this checkout:

```sh
pipx install './python[server,mqtt,tui]'
solix-link                         # full-screen terminal dashboard
solix-link interactive             # line-based fallback
solix-link scan
solix-link monitor --name office    # an already configured station
solix-link --help                   # scriptable commands
```

Use the terminal dashboard to scan, select and pair devices. Legacy C300 AC
and original C1000 / 1.5.1 need no pairing ID. Original C1000 / 1.7.1 needs
explicit Prime selection and was tested with an existing app ID. Generated
local IDs work on tested C1000 Gen 2 BLE/native MQTT; generated native IDs on
C2000 and original C1000 remain unverified. Save working identities for
reconnects. See [account-free setup](docs/account-free-local-setup.md).

## Gateway, browser and Home Assistant

For saved Bluetooth stations:

```sh
SOLIX_HTTP_TOKEN="$(cat /path/to/http-token)" \
  solix-link serve --config /path/to/config.json --web-ui \
  --history-file /path/to/private/history/readings.sqlite3
```

Open `http://127.0.0.1:8765/` and enter the token. Use an owner-only token file
and a dedicated private history directory; omit `--history-file` to disable
recording. The gateway is read-only unless `--allow-control` is supplied.
Native MQTT stations use `ap-service-serve --web-ui`; their AP worker must
separately allow controls. The development C2000 powers servers and its
AC output is protected.

| Guide | Contents |
| --- | --- |
| [Python library and CLI](python/README.md) | Installation, pairing, monitoring, controls and server commands |
| [Web dashboard](docs/web-dashboard.md) / [Gateway API](docs/gateway-home-assistant.md) | Charts, confirmations, JSON, SSE, metrics and deployment |
| [Home Assistant](custom_components/solix_link/README.md) | Sensors, charging controls, selectors and tariff actions |
| [Shared isolated AP](docs/multiple-ap-devices.md) | Provisioning, certificates and multiple stations |
| [History](docs/persistent-history.md) / [HA Energy](docs/home-assistant-energy-dashboard.md) | Saved charts, derived kWh, missing intervals and dashboard requirements |
| [Charging automation](docs/home-assistant-charging-automation.md) / [Surplus charging](docs/home-assistant-surplus-charging.md) | Opt-in blueprints, reserve guards and activation checks |
| [Policy previews](docs/adaptive-policy-preview.md) / [Offline replay](docs/policy-timeline-replay.md) | Read-only surplus/price decisions and synthetic timeline charts |
| [UPS activity and permissions](docs/ups-activity-and-permissions.md) / [Control coordination](docs/control-readiness-and-coordination.md) | Alerts, scoped access, fleet overview and write conflict handling |

## Development

Python source/tests are in `python/`; HA is in `custom_components/solix_link/`.
The gateway dashboard is in `dashboard/`; the separate Web Bluetooth explorer
and protocol tools are in `src/` and `tools/`.

```sh
python3 -m pip install -e './python[server,mqtt,tui]'
npm ci
npm run dev                        # Web Bluetooth explorer
npm run build
npm run build:dashboard             # Vue check + bundled gateway assets
PYTHONPATH=python python3 -m pytest python/tests home_assistant_tests -q
npm run test:protocol
npm run test:dashboard              # synthetic browser fixtures
```

Server/HA tests also need `pytest`, `httpx`, `aiohttp`, `PyYAML` and `Jinja2`;
browser checks need Playwright Chromium. See the web-dashboard guide for setup.
Commit dashboard source and compiled Python assets together.

## Research and license

Start with [implementation gaps](docs/implementation-gaps.md) and
[research progress](docs/server-research-progress.md). Protocol evidence:
[Gen 2](docs/gen2-protocol.md), [original C1000](docs/c1000-original-protocol.md),
[C300](docs/c300-protocol.md). Firmware: [findings](docs/firmware-findings.md),
[reproduction](docs/firmware-analysis-reproduction.md) and
[artifact provenance](firmware/README.md). Findings distinguish static analysis,
instruction replay and physical tests. Keep identities, credentials and raw
captures in ignored, private `.solix-private/` files.

Software is MIT; vendor firmware retains its own notices and provenance.
Protocol references: [SolixBLE](https://github.com/flip-dots/SolixBLE),
[AnkerSolixBLE](https://github.com/thomluther/AnkerSolixBLE),
[HaSolixBLE](https://github.com/flip-dots/HaSolixBLE) and
[anker-solix-api](https://github.com/thomluther/anker-solix-api).
