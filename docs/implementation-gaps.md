# Implementation gaps and validation priorities

Status on **2026-10-05**, following source checkpoint **`b98430b`**. This separates
missing product features, unresolved protocol support, hardware validation and
deployment work. A firmware replay or saved-setting ACK is not an electrical
measurement. Recent server-branch changes remain local and undeployed.

## Current connectivity and implemented controls

Two authorized cached `GET /diagnostics` reads, five seconds apart, reported
all three deployed stations connected and available over **native MQTT**.
Telemetry ages were **2.61–3.67 seconds**. No station commands were sent.

| Model | Reported main firmware | Native command families in repository source |
| --- | --- | --- |
| Original C1000 / A1761 | 1.7.1 | 9: charging watts, Fast, device/screen timeout, brightness, light, temperature unit, AC/DC Smart |
| C1000 Gen 2 / A1763 | 1.1.4.9 | 16: watts, cap, reserve, TOU/grid return, discharge floor, timeouts, Fast, display/clock brightness, memory, temperature/alert and AC/DC Smart |
| C2000 Gen 2 / A1783 | 2.1.6.4 | 5: watts, cap, reserve, TOU and grid return |

Counts come from `native_commands_for_model()`; the diagnostic check did not
query the live command list or exercise controls. Availability establishes
fresh telemetry, not electrical continuity. HA uses the HTTP gateway backed
by MQTT, so absent BLE advertisements do not imply loss of HA monitoring.
See [runtime evidence](ha-runtime-validation.md) and
[passive diagnostics](gateway-diagnostics.md).

The Python SDK, CLI, full-screen terminal, optional FastAPI/Vue gateway,
multi-station isolated AP and HA integration already exist. Gen 2 battery use
with mains connected is implemented through TOU, with guarded return to grid.
Device Timeout is implemented for both C1000 generations.

## Missing features or unresolved support

| Feature | Current boundary | Next evidence or implementation |
| --- | --- | --- |
| **True charging pause / AC-input disable** | No established public encoder or verified station control. Lower nonzero watts and charge caps are separate controls. | Recover the protected SDK encoder and exact firmware routing/readback, then measure charging and output behavior. [Action boundary](gen2-iot-action-firmware-boundary.md) |
| **Original C1000 battery use with mains connected** | No demonstrated external command selecting battery supply while retaining AC output. Reserve/TOU/charge-cap support is unestablished. | Recover installed main 1.7.1 and trace a supported route; use independent metering if a candidate is found. [Bypass audit](c1000-bypass-firmware-followup.md) |
| **Complete settings export and restoration** | Sanitized partial exports and independently fresh native TOU readback are implemented locally. Ordinary status still omits hidden clock/automatic-backup state; distinct configurations collide across all 19 callbacks. | Establish complete readback before backup/import or full restoration. [Partial export](settings-export-and-plan-readback.md), [full inventory](gen2-full-status-inventory.md) |
| **HA Energy dashboard: derived AC consumption** | Persisted input/output kWh estimates already exist; optional HA history entities lack a statistics state class. This is independent of native battery-counter calibration. | Add explicit persisted epochs, reload-safe regression checks and opt-in Energy-compatible entities; verify real HA Recorder behavior. An Integral helper provides a separate existing fallback. [Energy dashboard](home-assistant-energy-dashboard.md) |
| **Calibrated battery energy / native counters** | Per-device native counters and nominal kWh now reach SDK/API, CLI, terminal F7, browser and optional HA diagnostics. Physical units, timing and device reset/retention epochs remain unverified; AC includes bypass. | Per-model meter/counter/time calibration and noncritical retention tests before lifetime statistics. Existing AC values cannot become battery energy by renaming them. [Native values](native-energy-values.md), [energy boundaries](gen2-energy-epochs.md) |
| **Adaptive solar/price execution** | Pure previews/timeline replay and a compiled browser exist. An opt-in HA surplus blueprint implements guarded C1000 Gen 2 / 1.1.4.9 charging steps but is undeployed/disabled. C2000 needs an established override readback; price TOU stays preview-only. | Verify export sign/consumption before enabling surplus. Establish persistent plan ownership/recovery before tariff execution. [Surplus blueprint](home-assistant-surplus-charging.md), [adaptive contract](adaptive-policy-preview.md) |
| **Local firmware-update workflow** | Official update capture and firmware analysis exist; no offline installer, version chooser or recovery workflow. Installed original main 1.7.1 remains unavailable for analysis. | Obtain qualified artifact metadata, product/component checks, integrity verification and established update/recovery transport before an installer. [OTA metadata](c1000-firmware-metadata-followup.md) |
| **Additional Gen 2 app features** | Clock/theme scheduling, resource upload, language and disaster/Storm Guard plans have partial firmware evidence, without complete safe public controls. Clock brightness is already implemented separately. | Establish enums and hidden-field readback; verify physical behavior and restoration. Active disaster plans can override saved charging bounds. [Clock state](gen2-clock-screen-preservation.md), [disaster plans](gen2-disaster-plan-investigation.md) |
| **Transport/model parity** | Native C2000 general preferences/Smart are not exposed; C300 DC is unsupported and C300 AC native MQTT is not established. The browser BLE explorer retains some older maps. | Validate each model/transport independently, then add interfaces without generalizing A1763 firmware evidence. [Model evidence](../python/README.md) |

Ambient-light writes on the examined A1763 firmware reach a no-op; an ACK alone
does not justify adding that feature. AC-frequency changes are not a benign
display preference. See [preference candidates](gen2-preference-candidates.md).

## Implemented but awaiting validation

- **UPS observations and access separation:** cached mains/communication/reserve
  events, partial preference differences, HTTP command result/readback history,
  an optional disabled HA alert blueprint and per-device read/command tokens are
  implemented and tested synthetically. Source remains undeployed; these do not
  add station controls or establish physical power behavior. See
  [activity and permissions](ups-activity-and-permissions.md).

- **Account-free setup across the fleet:** C1000 Gen 2 generated BLE/native IDs
  work on tested hardware. Original C1000 generated Prime/native IDs and C2000
  generated native ID remain unverified. Local MQTT still uses matching local
  identity fields and mutual TLS. Trials require physical recovery access and
  separate authorization; preserve working profiles. [Setup matrix](account-free-local-setup.md)
- **Original charging/Fast rates:** setters and readback exist. Rate enforcement
  needs a stable below-full battery, noncritical load and independent meter;
  full-SOC tests were inconclusive. [Prepared test](c1000-native-charging-test-plan.md)
- **Timers and scheduling:** Gen 2 hourly TOU and private AC countdown exist.
  Physical countdown expiry, tariff boundary/DST transitions, lower reserve
  hysteresis and all model/firmware combinations need further validation.
  [Countdown trial](c1000-gen2-native-ac-countdown-validation.md)
- **Radio recovery and reliability:** MQTT radio flags/RSSI have established
  A1763 read-only routes; remote BLE enable is still an unexposed recovery
  candidate. The current cached check is not a completed long-term observation
  or whole-host power-cycle test. [Recovery boundary](radio-ble-advertising-recovery.md)

## Prepared deployment work

The adaptive browser and saved-plan/export UI are type-checked, rebuilt and
covered by synthetic browser scenarios. Development tools are isolated in an
ignored workspace directory; the live deployment is unchanged. Eight optional
HA history entities and terminal F5/F6 panels are implemented but remain
undeployed during the separate observation. The newest `ble-inspect` command
is likewise a local repository addition. No automation is activated here.

Suggested order: review the prepared worker/gateway/HA/UI changes; verify
saved TOU readback after a deliberate deployment; measure surplus and original
charging response on noncritical hardware; validate account-free setup with
physical recovery available; pursue pause encoding and calibrated energy
through offline analysis. No deployment or station write is implied by
this list. See [current progress](server-research-progress.md).
