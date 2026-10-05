# Implementation gaps and validation priorities

Status on **2026-10-05**, source checkpoint **`884e9c5`**. This separates
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
| **Complete settings export and restoration** | Ordinary status omits hidden clock/automatic-backup state. All 19 Gen 2 callbacks were replayed; distinct configurations still collide. | Establish a separate complete readback route before backup/import or full restoration. [Full inventory](gen2-full-status-inventory.md) |
| **Calibrated battery energy / HA Energy statistics** | Saved AC-power integrals include bypass loads and gaps. Raw firmware units, timing and reset/retention epochs are unverified. | Per-model meter/counter/time calibration and noncritical retention tests. Existing AC estimates cannot become battery lifetime energy by renaming them. [Energy boundaries](gen2-energy-epochs.md) |
| **Adaptive solar/price execution** | Python/API/CLI/terminal previews and timeline replay exist; no adaptive executor. Existing HA fixed charging blueprint is prepared and disabled. | Verify export sign and consumption response, expose complete required plan state, establish control ownership, then design opt-in execution. [Adaptive contract](adaptive-policy-preview.md) |
| **Local firmware-update workflow** | Official update capture and firmware analysis exist; no offline installer, version chooser or recovery workflow. Installed original main 1.7.1 remains unavailable for analysis. | Obtain qualified artifact metadata, product/component checks, integrity verification and established update/recovery transport before an installer. [OTA metadata](c1000-firmware-metadata-followup.md) |
| **Additional Gen 2 app features** | Clock/theme scheduling, resource upload, language and disaster/Storm Guard plans have partial firmware evidence, without complete safe public controls. Clock brightness is already implemented separately. | Establish enums and hidden-field readback; verify physical behavior and restoration. Active disaster plans can override saved charging bounds. [Clock state](gen2-clock-screen-preservation.md), [disaster plans](gen2-disaster-plan-investigation.md) |
| **Transport/model parity** | Native C2000 general preferences/Smart are not exposed; C300 DC is unsupported and C300 AC native MQTT is not established. The browser BLE explorer retains some older maps. | Validate each model/transport independently, then add interfaces without generalizing A1763 firmware evidence. [Model evidence](../python/README.md) |

Ambient-light writes on the examined A1763 firmware reach a no-op; an ACK alone
does not justify adding that feature. AC-frequency changes are not a benign
display preference. See [preference candidates](gen2-preference-candidates.md).

## Implemented but awaiting validation

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

Adaptive browser source needs type-checking, synthetic browser tests and a
rebuilt bundle; this server currently has neither Node nor npm. Eight optional
HA history entities and terminal F5/F6 panels are implemented but remain
undeployed during the separate observation. The newest `ble-inspect` command
is likewise a local repository addition. No automation is activated here.

Suggested order: finish browser/HA presentation for review; improve complete
cached plan readback; validate original charging and account-free setup when
physical access is available; pursue pause encoding and calibrated energy in
parallel with offline analysis. No deployment or station write is implied by
this list. See [current progress](server-research-progress.md).
