# Implementation gaps and validation priorities

Reviewed **2026-10-09**, against `main` baseline **`41a2512`** and the prepared
continuity/preview changes described below. This is a source inventory, not a
fresh live-device audit. The latest recorded deployment is
[2026-10-08](server-research-progress.md#shared-charging-ownership-repairs-and-calibration--2026-10-08).
Firmware inference, instruction replay, setting readback and electrical
measurements are separate evidence.

## Implemented functionality

The Python SDK, CLI, full-screen terminal, FastAPI/Vue gateway, shared isolated
AP and HA integration support local monitoring and model-specific controls.
HA reads the gateway; it does not need a separate BLE advertisement.

| Native MQTT model | Recorded main firmware | Command families in source |
| --- | --- | --- |
| Original C1000 / A1761 | 1.7.1 | 9: watts, Fast, device/screen timeout, brightness, light, temperature and AC/DC Smart |
| C1000 Gen 2 / A1763 | 1.1.4.9 | 16: watts, cap, reserve, TOU/grid return, discharge floor, timeouts, Fast, display/clock brightness, memory, temperature/alert and AC/DC Smart |
| C2000 Gen 2 / A1783 | 2.1.6.4 | 6: watts, cap, reserve, TOU/grid return and screen timeout |

Counts are from `native_commands_for_model()`, not a claim that every value was
tested physically. C2000 native screen timeout remains ready for supervised
testing; its output writes are blocked. See [model/transport details](../python/README.md).

- **Energy:** all supported models have optional persisted AC-power kWh
  estimates for HA Energy. Qualified Gen 2 firmware also has observed native
  Standard AC estimates; C2000 units remain assumed. Raw mode/channel counters
  stay diagnostic. Both methods include bypass and cannot isolate battery energy.
- **Charging automation:** price and surplus actions share durable HA ownership,
  protected readback, reserve checks, cancellation/storage latches and Repairs.
  Only native C1000 Gen 2 / 1.1.4.9 qualifies. Blueprints start disabled; no
  automation has been activated in the recorded deployment.
- **Presentation:** reported port power, runtime, timers, component firmware,
  expansion presence/SOC/temperature, optional Wi-Fi RSSI and original unitless
  report fields reach the supported interfaces when available.
- **Operations:** scoped tokens, command coordination/results, cached UPS events,
  partial settings export/comparison, saved-plan readback, history, calibration
  sessions and offline policy replay are implemented.

## Prepared changes awaiting deployment

The 2026-10-09 software work adds HA persistence for native-meter continuity:
old gateway backups, regressing counters/epochs and storage failures cannot
publish Energy statistics. Existing entity IDs and unit assumptions stay the
same. It cannot repair earlier Recorder data or detect loss of both stores.
See [native meter handling](native-energy-meter.md).

The browser, terminal F6, API and offline/cached CLI now offer the
[exact HA controller rules](controller-policy-preview.md), alongside explicitly
exploratory previews. Supplied ownership is a simulation; only HA's preview
action uses its actual durable owner. No preview sends commands.

CI now covers Python/HA tests, both frontend builds, bundled-asset consistency,
protocol tests and synthetic browser scenarios. These changes are prepared in
the repository; this checkpoint does not update the running HA or gateway.

## Remaining investigations

| Feature | Remaining boundary | Next evidence |
| --- | --- | --- |
| True charging pause / AC-input disable | No established encoder or verified public station control. Nonzero watts, caps and TOU are separate settings. | Trace protected SDK runtime-key/DEX loader and exact firmware route; measure input/output behavior. [Pause boundary](gen2-iot-action-firmware-boundary.md) |
| Original C1000 charging and battery use | Setters/readback work; loaded rate enforcement and battery supply with mains connected are unverified. No established reserve/TOU/cap controls. | Stable below-full battery, noncritical load and independent meter; installed main 1.7.1 artifact/route analysis. [Charging test](c1000-native-charging-test-plan.md), [bypass audit](c1000-bypass-firmware-followup.md) |
| Physical energy calibration | C2000 scaling discrepancy, MCU timing, reset/wrap/retention and mode/channel semantics remain unresolved. Original raw reports have no assigned units. | Labelled meter intervals at stable loads and supervised noncritical station restarts. No automatic rescaling. [Calibration workflow](energy-calibration-workflow.md) |
| Complete settings backup/restore | All 19 ordinary status callbacks still collide for hidden clock/backup state. Partial export cannot restore a station. | Establish independent readback of every required field before import/restoration. [Full inventory](gen2-full-status-inventory.md) |
| Automation qualification | C2000 lacks established protected override readback; original is excluded. C1000 Gen 2 needs physical consumption/export-sign verification. | Supervised price/surplus cycles, reserve hysteresis, tariff boundaries and recovery checks. [Shared controller](charging-controller-and-repairs.md) |
| Additional Gen 2 preferences | C2000 parity, clock schedules/resources/language and disaster plans lack complete safe readback/restoration. | Model-specific enums, preservation proofs and supervised round trips. [Preference candidates](gen2-preference-candidates.md) |
| Account-free fleet setup | Generated BLE/native IDs worked on tested C1000 Gen 2. Original generated Prime/native and C2000 generated native IDs are unverified. | Physical recovery access; preserve working profiles. Local MQTT still requires matching local identity and mTLS. [Setup matrix](account-free-local-setup.md) |
| Local firmware updates | Official capture exists, but no qualified offline installer/recovery workflow. Original installed main 1.7.1 artifact is unavailable. | Product/component/version metadata, integrity checks and established recovery transport. [OTA metadata](c1000-firmware-metadata-followup.md) |
| Transport and hardware coverage | C300 DC/Python Solarbank unsupported; C300 AC native MQTT unestablished. Expansion hardware, original report semantics and some countdown/recovery behavior remain untested. | Separate model/transport fixtures and supervised physical tests. [Unused-data audit](unused-device-data.md), [radio recovery](radio-ble-advertising-recovery.md) |

No setting ACK certifies an electrical effect. Continue offline analysis when
physical measurements are unavailable; leave the server-powered C2000's
outputs, resets and provisioning excluded from experiments.
