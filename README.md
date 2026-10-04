# SOLIX Link

Local monitoring and controls for Anker SOLIX power stations over **Bluetooth**
or **native MQTT on an isolated Wi-Fi AP**. Includes an async Python library,
CLI, fixed terminal dashboard, optional FastAPI/Vue web dashboard, and a
Home Assistant integration for the local gateway.

## Features and devices

- Battery, temperature, power, output states and supported settings.
- Charging power and charge limits with fresh telemetry confirmation.
- Gen 2 native MQTT: backup reserve, hourly Time-of-Use plans, battery discharge
  with mains connected, and confirmed return to grid.
- C1000 Gen 2 native MQTT: 100–1200 W charging, fast charge, temperature unit,
  off-grid alert, display brightness/timeout, output-port memory and guarded
  lower discharge limit without silently changing reserve; AC/DC Smart with the
  corresponding output off and inactive countdowns on main 1.1.4.9, plus saved
  brightness for each inactive clock window. A private operator CLI also supports
  a [guarded AC countdown](docs/c1000-gen2-native-ac-countdown-validation.md);
  early cancellation is tested, physical expiry remains untested.
- Original C1000: local MQTT and Prime Bluetooth charging power, Device Timeout,
  screen timeout/brightness, light and temperature units; guarded AC/DC Smart
  with the corresponding output off and Fast charging over both transports;
  additional legacy BLE controls. Prime also adds direct AC output control.
- C1000 Gen 2 solar weak-light-lock diagnostic, traced to firmware; physical
  low-light behavior remains untested. Saved frequency and AC/DC Smart
  settings are also available; [readback semantics](docs/gen2-preference-readback.md).
- C1000 Gen 2 Prime RSSI queries via `wifi-rssi`, with explicit unavailable
  results; [radio readbacks](docs/c1000-radio-readback-validation.md).
- C1000 Gen 2 private operator CLI: [native RSSI](docs/c1000-gen2-native-rssi-validation.md)
  via `ap-service-wifi-rssi` and [radio flags](docs/c1000-gen2-native-wireless-state-validation.md)
  via `ap-service-wireless-state`; flags do not report physical advertising.
- Authenticated JSON HTTP, SSE and Prometheus for multiple stations.
- Opt-in Home Assistant price/solar charging blueprint with telemetry freshness,
  reserve protection and a failure latch; installed automations start disabled.
- Read-only charging-policy previews in the browser, terminal, API and CLI; proposals
  send no commands and do not activate automations.
- Opt-in [adaptive surplus/price previews](docs/adaptive-policy-preview.md) in
  Python, API, CLI and terminal; bounded charging steps and candidate Gen 2 battery-use plans.
  [Offline timeline replay](docs/policy-timeline-replay.md) exports JSON or standalone SVG.
  Adaptive Vue source is prepared; its browser bundle still needs rebuilding.
- Optional private SQLite history with saved battery/AC-power charts, estimated
  AC energy and coverage; gaps and process restarts stop integration. Includes
  [HA diagnostics](docs/home-assistant-history.md) and
  [terminal history/preview panels](docs/terminal-history-preview.md).
- Home Assistant diagnostics downloads with model/firmware, freshness and
  supported settings; the integration report omits credentials and identities.
- [Passive gateway diagnostics](docs/gateway-diagnostics.md) and
  [saved AP checks](docs/ap-service-setup-check.md) through CLI, terminal, browser and HTTP.
- Up to eight supported stations on one isolated AP, with separate certificates,
  telemetry and command queues.
- Web dashboard with device selection, power/battery charts and explicit write
  confirmation. Deployment needs no Node runtime or external assets.

| Device | Python Bluetooth | Native local MQTT | Tested / limitations |
| --- | --- | --- | --- |
| C1000 Gen 2, A1763 | Monitoring, charge limits/power, display timeout, fast charge | Charging/fast/reserve, tariffs, temperature/alert, display/memory, discharge floor, guarded AC/DC Smart and inactive clock brightness | Main 1.1.4.9 / radio 0.3.3.0; Smart requires its output off and inactive countdowns; older 1.1.4.3 uses legacy BLE |
| C2000 Gen 2, A1783 | Monitoring, power/cap, display timeout | Charging/reserve, tariffs and return to grid | Main 2.1.6.4; AC-output writes blocked |
| Original C1000, A1761 | Twelve legacy controls; ten Prime controls | Nine preferences: charging power, device/screen timeout, brightness, light, temperature unit, AC/DC Smart, Fast | Legacy 1.5.1; Prime/native main 1.7.1 / radio 0.3.3.0; Smart requires its output off; AC Smart/Prime AC output require no active AC timer; charging rates/reboot retention unverified |
| C300/C300X AC, A1722/A1723 | Monitoring, AC output, light, charging power, display timeout | — | C300X tested; C300 sibling untested; C300 DC unsupported |
| Solarbank 3 E2700 Pro, A17C5 | Separate Web Bluetooth app | — | Browser telemetry tested; no Python profile |

Native MQTT connects the station itself to the AP service. The optional
BLE-to-MQTT bridge reads Bluetooth and publishes to your existing broker.
Three physical stations—original C1000, C1000 Gen 2 and C2000 Gen 2—passed
simultaneous native MQTT, HA discovery and shared-service recovery checks.
See the [live deployment record](docs/ha-runtime-validation.md) and
[Python guide](python/README.md).
The [completed 13-hour observation](docs/ha-13-hour-observation.md) records
maintenance gaps and no unexpected protected-setting changes.

## Start locally

BLE requires Python 3.11+ and a working Bluetooth adapter. Install in an
isolated environment, for example with pipx:

```sh
pipx install './python[server,mqtt,tui]'
solix-link                         # interactive terminal dashboard
solix-link scan
solix-link --help                  # scriptable CLI and server commands
```

The terminal dashboard scans, selects, connects and pairs supported devices.
A line-based interactive fallback is available. For saved-device CLI usage:

```sh
solix-link add --name office --model c1000 --address AA:BB:CC:DD:EE:04
solix-link monitor --name office
solix-link set-charge-power --name office --watts 300
```

C300 AC and original C1000 firmware 1.5.1 use legacy BLE without a pairing ID.
Original C1000 firmware 1.7.1 uses explicit Prime selection; monitoring and
ten BLE controls and nine native MQTT preferences were verified with an existing
app ID. Generated-ID pairing on this model remains unverified. Prime Gen 2
pairing generates a local ID and can require one short **main power button**
press. Save the ID for reconnects. Generated-identity native MQTT is verified
on C1000 Gen 2; C2000 native provisioning uses an already working identity.
See [pairing and version notes](docs/gen2-protocol.md).

## Browser, API and Home Assistant

Start the gateway for saved Bluetooth devices:

```sh
# Load a generated token from an owner-only file.
SOLIX_HTTP_TOKEN="$(cat /path/to/http-token)" \
  solix-link serve --config /path/to/config.json --web-ui --allow-control
```

Open `http://127.0.0.1:8765/` and enter the token. Omitting `--allow-control`
makes the gateway read-only. Native AP stations use `ap-service-serve --web-ui`;
the AP worker must separately allow controls. CLI/private socket writes use
`--name` when several stations share the AP.

- [Web dashboard](docs/web-dashboard.md): setup, charts, controls and screenshots.
- [Shared isolated AP](docs/multiple-ap-devices.md): registration, provisioning,
  device selection and certificate routing.
- [Gateway API](docs/gateway-home-assistant.md): JSON commands, SSE, metrics and deployment.
- [Home Assistant component](custom_components/solix_link/README.md): sensors,
  charging controls, selectors, alert switch and tariff actions.
- [HA charging blueprint](docs/home-assistant-charging-automation.md): price/solar
  policy, prerequisites, explicit activation and failure review.

HA 2026.7.4 passed live setup, three-device discovery, a restored configuration
control and integration reload. Other releases and long-term operation still
need testing. [Saved history](docs/persistent-history.md) is opt-in with
`--history-file`; AC energy estimates include bypass loads and do not measure
stored battery energy. Firmware energy-counter units remain unverified.

## Development

The Python package lives in `python/solix_link/`, the gateway Vue dashboard in
`dashboard/`, and the separate Web Bluetooth protocol explorer in `src/`.
The explorer provides packet logs, CSV export and research tools; some older
legacy-model maps differ from the newer Python decoders.

```sh
npm ci
npm run dev                       # Web Bluetooth application
npm run build
npm run build:dashboard            # Vue check + bundled gateway assets
PYTHONPATH=python python3 -m pytest python/tests home_assistant_tests -q
npm run test:protocol              # synthetic BLE encryption checks
npm run test:dashboard             # synthetic browser fixture only
```

Install the Python `server` extra plus `pytest`, `httpx`, `aiohttp`, `PyYAML`
and `Jinja2` for server/HA tests. Browser checks need Playwright Chromium; setup is in the
web-dashboard guide. Commit Vue sources and compiled Python assets together.

## Protocol research and firmware

- [Gen 2 protocol](docs/gen2-protocol.md), [original C1000](docs/c1000-original-protocol.md)
  and [C300](docs/c300-protocol.md): wire formats and hardware evidence.
- [Firmware findings](docs/firmware-findings.md): handlers, readiness, tariffs,
  checksums and update verification.
- [Firmware inputs](firmware/README.md): recovered vendor images, hashes and provenance.
- [Original native MQTT](docs/c1000-original-mqtt-followup.md): bootstrap, eight
  controls, deferred status and suppressed ACKs; [charging-path analysis](docs/c1000-bypass-firmware-followup.md).
- [Gen 2 plan persistence and backup readback](docs/gen2-persistent-plan-followup.md):
  live tariff/reserve retention across server/AP reconnect, missing saved
  records and cancellation side effects.
- [Original Prime DC Smart](docs/c1000-prime-dc-smart-validation.md): restored
  Bluetooth control with DC off; [independent native validation](docs/c1000-native-dc-smart-validation.md).
- [Original Prime AC/Smart](docs/c1000-prime-ac-smart-validation.md): AC output
  and guarded AC Smart; [native AC Smart validation](docs/c1000-native-ac-smart-validation.md).
- [Original Fast/BMS analysis](docs/c1000-fast-status-retention.md): live-state
  retention in firmware; [Prime Fast validation](docs/c1000-prime-fast-validation.md)
  and [native Fast validation](docs/c1000-native-fast-validation.md),
  [current limits](docs/c1000-fast-current-limits.md),
  [charging-rate trials](docs/c1000-charging-rate-validation.md) and
  [SOC/history reporting](docs/c1000-soc-and-recharge-firmware.md).
- [Solar weak-light lock](docs/gen2-weak-light-observability.md): passive
  C1000 Gen 2 monitoring, [retry origin](docs/gen2-pv-retry-origins.md) and
  [MPPT actuation guards](docs/gen2-pv-retry-actuation.md), with
  [DSP timing/fault exits](docs/gen2-pv-start-continuation.md).
- [Gen 2 full status](docs/gen2-backup-query-investigation.md): controller UTC
  telemetry and non-clearing queries; [backup-export follow-up](docs/gen2-backup-export-radio-app.md)
  resolves radio/app candidates and diagnostic tracking side effects.
  [Additional status replay](docs/gen2-status-export-limits.md) closes the `FA`
  export candidate and demonstrates saved-state collisions.
  [All 19 ordinary callbacks](docs/gen2-full-status-inventory.md) now have bounded
  replay evidence; complete settings restoration remains unsupported.
- [Charging and reserve validation](docs/c1000-charging-and-reserve-validation.md):
  native fast charge, lower charging power, reserve floor and cached reconnect.
- [Native display/memory validation](docs/c1000-native-preferences-validation.md):
  confirmed C1000 Gen 2 settings and restoration; final BLE check unavailable.
- [Reproduce 1,842 offline cases](docs/firmware-analysis-reproduction.md):
  original instruction execution with synthetic inputs and explicit substitutions.
- [Charging follow-up](docs/c1000-charging-control-followup.md): reserve side
  effects, mirrored limits and why 0 W is unavailable as charge pause.
- [Charging action mapping](docs/gen2-iot-action-firmware-boundary.md): binary
  MQTT admission, [Dart interceptors](docs/gen2-dart-action-interceptors.md)
  and [protected SDK loader recovery](docs/android-loader-carriers.md).
  [Record 2 analysis](docs/android-loader-record-two.md) resolves JNI method
  registration and stub decoders; the charging-pause encoder remains unknown.
  [ClassLoader tracing](docs/android-loader-classloader-boundary.md) and
  [container-record analysis](docs/android-loader-container-record.md) identify
  DEX preparation and its unresolved runtime key without executing Android/JNI code.
- [Archive callback continuation](docs/android-archive-callback.md): resolves the
  type-3 `classes.dex` reader; runtime key and charging-pause encoder remain open.
- [Selector-2 record](docs/android-selector-two-record.md): full static coverage
  of the next loader record, context access and allocation boundaries.
  [Dataflow follow-up](docs/android-selector-two-dataflow.md) inventories its
  pointer-array stores; the runtime transform key remains unresolved.
- [Factory aggregate](docs/gen2-factory-aggregate-export-limits.md): additional
  indirect status replay; cache/timer side effects and incomplete settings export.
  [Asset-transfer startup and replies](docs/gen2-asset-transfer-export-limits.md) supply
  another bounded file-service trace, with no complete export route established.
- [Energy boundaries](docs/gen2-energy-epochs.md): actual reset/width-wrap replay
  and pure offline power comparisons, without calibrated lifetime-energy claims.
- [Server research progress](docs/server-research-progress.md): current findings,
  focused verification and remaining hardware requirements.
- [Original charging gates](docs/c1000-charge-gate-rules.md): input-event
  priority and the second charging channel; no external bypass selector found.
  [Saved-limit validation](docs/c1000-saved-charge-validation.md) distinguishes
  older firmware's write/readback from its configuration-reload defaults.
- [Countdown stop worker](docs/gen2-output-stop-worker.md): late cancellation
  and why reported output state is not an independent physical measurement.
- [Local identity storage](docs/radio-identity-storage.md): cache transitions,
  reconnect requirements and a smaller account-free test sequence;
  [MQTT-assisted BLE recovery candidate](docs/radio-ble-advertising-recovery.md).
- [Radio initialization and identity activation](docs/radio-ble-initialization-activation.md)
  and [native query limits](docs/radio-native-info-queries.md): 70 new offline cases.
- [Original firmware capture plan](docs/c1000-ota-http-wrapper.md): app transport
  branches and the plaintext OTA metadata callback; main 1.7.1 remains missing.
- [Energy accounting](docs/gen2-energy-counter-investigation.md): sampling,
  scheduler gaps and nominal Wh arithmetic; calibrated units remain unverified.
- [Smart auto-off policy](docs/c1000-smart-auto-off-policy.md) and
  [tariff clock](docs/gen2-schedule-clock-audit.md): recovered behavior and limits.

Firmware results are version-specific. Idle sleep, calibrated energy and
actual alert delivery need further hardware validation.
Keep IDs, credentials and raw captures in ignored `.solix-private/` files.
Device tests record baselines, confirm fresh readback and restore settings.
The development C2000 powers servers; its AC output is never toggled.

## Credits and license

Protocol references: [SolixBLE](https://github.com/flip-dots/SolixBLE),
[AnkerSolixBLE](https://github.com/thomluther/AnkerSolixBLE),
[HaSolixBLE](https://github.com/flip-dots/HaSolixBLE) and
[anker-solix-api](https://github.com/thomluther/anker-solix-api).
Repository software: MIT. Vendor firmware inputs retain their own provenance
and notices; the software license grants no rights to vendor firmware.
