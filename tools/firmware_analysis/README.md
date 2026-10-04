# Offline C1000 firmware replays

These Python scripts execute selected retained C1000 Gen 2 main-controller
instructions in Unicorn with synthetic inputs. They never connect to a device.
The combined runner has **1,842 cases**: 1,063 settings, 20 alert, 245 charging, 212 feature
candidate, 147 additional telemetry and 155 timeout cases. Radio/DSP integrity tools provide separate checks.
Additional standalone suites below have their own counts and are not included
in that total.

See [reproduction instructions](../../docs/firmware-analysis-reproduction.md)
for the required firmware hash, setup, tested versions and substituted services.

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/run_replays.py --output /tmp/solix-replays
```

The bundled image in `firmware/c1000_gen2/1.1.4.9/` is checked before emulation.
Use `SOLIX_FIRMWARE_DIR=/path/to/firmware` to override the directory; it must
contain the exact `MainMcu-decoded.bin`. These tools do not download firmware.

`expected_results/` contains synthetic replay output, not device captures. The
runner compares every result with those files and writes a manifest of runtime,
firmware and source hashes. `verified-run.json` records the successful published
run and exact source hashes. Individual suite scripts accept
`SOLIX_ANALYSIS_OUTPUT` as their output directory. Do not use Python `-O`.

The helper modules are not device-control tools. They contain no BLE, MQTT, SSH,
HTTP or capture-ingestion code. A successful replay confirms the bounded code
path and its stated assumptions; it does not replace testing actual firmware
and hardware behavior.

For separate radio signature and sparse DSP integrity checks:

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/solix-radio-check \
  python3 tools/firmware_analysis/verify_radio_signature.py
python3 tools/firmware_analysis/extract_dsp.py --output /tmp/solix-dsp-check
```

The DSP command exports word-addressed binary files for offline analysis. No
tool flashes a station. Vendor images have separate [notices](../../firmware/README.md).

## Original C1000 v1.5.9

The separate A1761 input is publicly downloaded vendor firmware, older than the
tested station's current main 1.7.1 image. Run its extractor and actual ARM
handler replay independently:

```sh
python3 tools/firmware_analysis/extract_c1000_original.py --output /tmp/a1761-images
python3 tools/firmware_analysis/emulate_c1000_original_commands.py \
  --output /tmp/a1761-commands.json
python3 -m unittest discover -s tools/firmware_analysis -p test_c1000_original_package.py -v
python3 tools/firmware_analysis/emulate_c1000_smart_policy.py --output /tmp/a1761-smart.json
```

Seven extractor tests and **1,317 handler cases** cover integrity, decompressed
dispatch tables, fast-charge bit preservation, Smart mapping/serialization,
charge-power readback clamping and a library information reply. Persistence,
ACK transport and hardware are substituted; no direct local MQTT or physical
charging is established. See the [original investigation](../../docs/c1000-legacy-network-investigation.md).

The separate [Smart-policy suite](../../docs/c1000-smart-auto-off-policy.md)
has **1,347 cases**, including inherited counters, nominal timing, countdown
guards and power-cache serialization. It does not operate any device output.

The [bootstrap-state suite](../../docs/c1000-bootstrap-state-investigation.md)
adds **1,165 cases** for ACK handling, internal module messages, startup retry
state and timer registration:

```sh
python3 tools/firmware_analysis/emulate_c1000_network_state.py \
  --output /tmp/a1761-network-state.json
```

Its main-1.5.9 evidence does not decode the installed radio's HTTP/MQTT parser
or establish a safe remote reconnect command. The installed main was 1.5.1
during that study; the later [official update](../../docs/c1000-original-update-network.md)
and local controls were verified on 1.7.1. Its controller image remains unavailable.
The [app OTA capture audit](../../docs/c1000-app-ota-capture-investigation.md)
keeps app inputs, capture data and its separate two framing cases private.

## Energy accounting continuation

`emulate_energy_counters.py --output /tmp/gen2-energy-counters.json` adds
**49 actual-instruction scenarios and 1,800 arithmetic checks** separately
from the combined Gen 2 runner. It covers port getters, discrete sampling,
scheduler gaps, SysTick setup, report division and duration remainders.
See [the timing and calibration limits](../../docs/gen2-energy-counter-investigation.md).

## Schedule clock continuation

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/solix-clock-check \
  python3 tools/firmware_analysis/emulate_schedule_clock.py
cmp /tmp/solix-clock-check/schedule-clock-results.json \
  tools/firmware_analysis/expected_results/schedule-clock-results.json
```

These **47 separate cases** combine actual clock synchronization, plan parsing,
tariff selection and D9/FE serialization with synthetic RTC and gate inputs.
See [UTC-offset retention](../../docs/gen2-schedule-clock-audit.md).

## Preference, LCD schedule and disaster-plan continuations

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/solix-preferences \
  python3 tools/firmware_analysis/emulate_preference_candidates.py
python3 tools/firmware_analysis/emulate_timer_plan.py \
  --output /tmp/solix-clock-screen.json
cmp /tmp/solix-clock-screen.json \
  tools/firmware_analysis/expected_results/timer-plan-results.json
SOLIX_ANALYSIS_OUTPUT=/tmp/solix-disaster \
  python3 tools/firmware_analysis/emulate_disaster_plan.py
```

- [Preferences](../../docs/gen2-preference-candidates.md): **1,060 cases** cover
  brightness, the ambient-light stub, raw language storage and Smart settings.
  Raw acceptance does not establish supported values or safe output behavior.
- [LCD clock screen](../../docs/gen2-timer-plan-investigation.md): **60 cases**
  establish `0091` as display/theme scheduling, not charging scheduling. Timer,
  display and asset-transfer boundaries are substituted. These are separate
  from the 47 tariff-clock cases above.
- [Disaster plans](../../docs/gen2-disaster-plan-investigation.md): **762 cases**
  cover manual/automatic activation and policy. Active plans request effective
  BMS limits 100%/1% and the internal fast-charge ceiling; cancellation can
  invalidate other windows. Saved settings alone are not the effective limits.
  No live actuator or physical AC-continuity guarantee is established.

These suites require the hash-checked C1000 Gen 2 main 1.1.4.9 image. Their
results and source hashes are independent of `verified-run.json`; use their
documented commands and substitution limits rather than adding their counts
to the combined runner's reported total.

## Complete configuration files

Two separate suites execute full synthetic file loading rather than starting
after file validation:

```sh
python3 tools/firmware_analysis/emulate_original_settings_load.py \
  --output /tmp/original-settings-load.json \
  --manifest /tmp/original-settings-load-manifest.json
SOLIX_ANALYSIS_OUTPUT=/tmp/gen2-syspara-backup \
  python3 tools/firmware_analysis/emulate_gen2_syspara_backup.py
```

- [Original saved settings](../../docs/c1000-saved-charge-validation.md):
  **108 cases**, main 1.5.9. Command acceptance differs from saved-limit
  validation; defaults affect timeout, Fast and Smart preferences too.
- [Gen 2 complete backup file](../../docs/gen2-syspara-backup-format.md):
  **28 cases**, main 1.1.4.9. The 415-byte `sysPara` roundtrip preserves all
  four records and raw switches; malformed or invalid files cause defaults.

Expected results and manifests are in `expected_results/`. File I/O is a
synthetic in-memory service; no physical storage, reboot, external export or
hardware output is exercised. These counts are separate from the combined runner.

## BLE and native identity separation

```sh
python3 tools/firmware_analysis/emulate_radio_ble_identity_separation.py \
  --output /tmp/solix-ble-identity-separation
```

[32 A1763 radio 0.3.3.0 cases](../../docs/ble-native-identity-separation.md)
execute BLE allowlist lookup, Prime registration, native account changes and
explicit list saves. The normal provisioning replay stops at stated boundaries;
physical confirmation, asynchronous activation and durable recovery are untested.
Original/C2000 firmware equivalence is unproved. Result/manifest fixtures are
synthetic, separate from the combined runner and contain no operational identity.

## Clock-screen preservation

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/gen2-clock-screen-preservation \
  python3 tools/firmware_analysis/emulate_gen2_clock_screen_preservation.py
```

[121 main-1.1.4.9 cases](../../docs/gen2-clock-screen-preservation.md) show a
reachable hidden-enable mismatch after A2 toggle/restore, identify pending asset
staging overwrite, and verify complete scalar-field preservation. Expected
results and manifest are in `expected_results/`; independent runs matched both
byte for byte. No physical storage/display/output or asset download is exercised.

## Native output readiness and original diagnostic charging paths

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/gen2-native-output-readiness \
  python3 tools/firmware_analysis/emulate_gen2_native_output_readiness.py
python3 tools/firmware_analysis/audit_original_diagnostic_charging_paths.py \
  --output /tmp/original-diagnostic-charging-results.json \
  --manifest /tmp/original-diagnostic-charging-manifest.json
```

[92 main-1.1.4.9 cases](../../docs/gen2-native-output-readiness.md) exercise AC/DC
off-task timer cleanup, on-branch boundaries and AC Smart saved-byte restoration.
[131 original-main-1.5.9 cases](../../docs/c1000-diagnostic-charging-audit.md) cover
48 diagnostic entries, selector rejection and selected radio-staging paths.
Independent results and manifests matched their `expected_results/` files byte
for byte. All RAM and timestamps are synthetic. No radio/relay/flash operation
or newer-original firmware equivalence is established; counts remain separate
from the combined runner. Diagnostic commands are not runtime API features.

## Countdown lifecycle and original DC-input qualification

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/gen2-ac-countdown \
  python3 tools/firmware_analysis/emulate_gen2_ac_countdown_roundtrip.py
python3 tools/firmware_analysis/emulate_original_dc_input_qualification.py \
  --output /tmp/original-dc-input-qualification-results.json \
  --manifest /tmp/original-dc-input-qualification-manifest.json
```

[98 C1000 Gen 2 main-1.1.4.9 cases](../../docs/gen2-ac-countdown-roundtrip.md)
cover early cancellation, normal rearming, adverse hidden states and asynchronous
off/on resets. [1,064 original main-1.5.9 cases](../../docs/c1000-second-input-qualification.md)
cover the second input qualifier, debounce history, qualified AC priority and
selected DCDC validity boundaries. Complete results/manifests independently
matched `expected_results/`. Both suites use synthetic inputs, omit physical
outputs and remain separate from the combined runner. Installed original
1.7.1 equivalence and a user-selectable charging-source command are unproved.

## Radio-local MQTT BLE-enable candidate

```sh
python3 tools/firmware_analysis/emulate_radio_ble_advertising.py \
  --image firmware/c1000_gen2/1.1.4.9/c1000-radio-validated.bin \
  --output-dir /tmp/radio-ble-advertising
```

[50 A1763 radio-0.3.3.0 cases](../../docs/radio-ble-advertising-recovery.md)
execute native admission, local opcode `0024`, wireless query `0003`, reply
framing and initialized BLE/timer paths. An ACK can occur without advertising.
Protected identity regions remain unchanged in the executed initialized path;
physical callbacks and first initialization are excluded. Complete results and
manifest independently match `expected_results/`. Optimized Python, wrong-size
and wrong-hash images are rejected. No runtime recovery API, station trial or
other-model equivalence is claimed; counts are separate from the combined runner.

## Radio initialization and private query providers

```sh
PYTHONPATH=/tmp/solix-analysis-tools:tools/firmware_analysis \
  python3 tools/firmware_analysis/emulate_radio_ble_activation.py \
  --image firmware/c1000_gen2/1.1.4.9/c1000-radio-validated.bin \
  --output-dir /tmp/radio-ble-activation
python3 tools/firmware_analysis/emulate_radio_native_info_queries.py \
  --output /tmp/radio-native-info
```

[47 activation/initialization cases](../../docs/radio-ble-initialization-activation.md)
trace first-enable timers/defaults, BLE connection flag writers and bounded
normal provisioning callbacks. [23 query-provider cases](../../docs/radio-native-info-queries.md)
resolve raw MAC selectors, failure scratch bytes and private configured network
text. Complete result/manifest pairs independently match `expected_results/`.
Both use the exact A1763 radio 0.3.3.0 image, reject optimized Python and enforce
synthetic provider boundaries. They add no physical recovery or account-free
trial and remain separate from the combined runner.

The [static app HTTP-wrapper note](../../docs/c1000-ota-http-wrapper.md)
documents the OTA business-response capture point; it adds no emulation cases.

## Network-state producers and original protocol negotiation

```sh
PYTHONPATH=/tmp/solix-analysis-tools:tools/firmware_analysis \
  python3 tools/firmware_analysis/emulate_radio_network_producers.py \
  --image firmware/c1000_gen2/1.1.4.9/c1000-radio-validated.bin \
  --output-dir /tmp/radio-network-producers
python3 tools/firmware_analysis/emulate_original_capability_negotiation.py \
  --output /tmp/original-capability-negotiation-results.json \
  --manifest /tmp/original-capability-negotiation-manifest.json
```

[31 A1763 radio cases](../../docs/radio-network-state-producers.md) resolve
got-IP, cached IPv4 text and normal MQTT callbacks. [39 original main-1.5.9
cases](../../docs/c1000-power-method-and-negotiation-audit.md) show protocol-limit
negotiation changes session state, including selected error continuations.
Compare full results/manifests with `expected_results/`. Physical callbacks and
installed original 1.7.1 remain outside scope; counts are separate from the
combined runner.

`inspect_android_sdk_boundary.py` accepts the exact externally retained base
and ARM64 APKs; see [SDK inventory](../../docs/android-sdk-native-boundaries.md)
for its command. It checks input hashes and produces sanitized static metadata,
without executing app code or exporting assets. Its deterministic fixture pair
adds no emulation count or cloud/action protocol implementation.

## SDK action admission and protected Android loaders

```sh
python3 tools/firmware_analysis/audit_gen2_iot_action_boundary.py \
  --output-dir /tmp/gen2-iot-action-boundary
python3 tools/firmware_analysis/inspect_android_loader_carriers.py \
  --base-apk /private/path/base.apk \
  --output-dir /tmp/android-loader-carriers
```

[48 Gen 2 boundary cases](../../docs/gen2-iot-action-firmware-boundary.md)
execute numeric head selection and binary head-17 admission with a substituted
controller forwarder. Named SDK arguments are not a station MQTT payload;
arbitrary base64 action JSON can select an accidental forwarding opcode.

[3 loader replays plus 256 protector cases](../../docs/android-loader-carriers.md)
recover readable native loader instructions from the exact external APK, using
only emulated memory and pinned execution regions. No Android/JNI code or guest
host-call forwarding occurs. Default output is selected metadata; optional
recovered virtual images must stay private. Protector opcodes are not station
commands. Full results/manifests independently match the corresponding
`expected_results/` pairs. These cases are outside the combined firmware runner;
the linked Dart/asset audits are static and add no CPU-case count.

The [68-case string follow-up](../../docs/android-loader-string-initializers.md)
requires the prior unpacker's exact restricted virtual-image directory:

```sh
python3 tools/firmware_analysis/inspect_android_loader_strings.py \
  --base-apk /private/path/base.apk \
  --images-dir /private/path/recovered-virtual-images \
  --output-dir /tmp/android-loader-strings
```

It regenerates init-array/FDE selection and replays only bounded pure routines,
with read/write/import-slot guards and reset guest state. JNI/Android methods
remain excluded. Complete metadata results/manifests independently match the
fixture pair; recovered code and arbitrary decoded strings stay private.

The [static protected-JNI continuation](../../docs/android-loader-vm-boundary.md)
uses that same pinned carrier image, pure ELF relocations and no guest execution:

```sh
python3 tools/firmware_analysis/inspect_android_loader_vm_boundary.py \
  --base-apk /private/path/base.apk \
  --images-dir /private/path/recovered-virtual-images \
  --output-dir /tmp/android-loader-vm-boundary
```

Compare both complete `android-loader-vm-boundary-*.json` files with
`expected_results/`. Its 189 instruction checks, three record boundaries and
five prefix tokens identify the next bounded wrapper record; they recover no
SDK action serializer or device packet. Inputs are hash-pinned, wrong inputs
fail before output, and raw app records/images stay private.

The [record-two continuation](../../docs/android-loader-record-two.md) adds
616 static instruction checks, 59 bounded CFG states and complete 178-byte
body coverage across both GetEnv branches, with zero guest execution:

```sh
python3 tools/firmware_analysis/inspect_android_loader_record_two.py \
  --base-apk /private/path/base.apk \
  --images-dir /private/path/recovered-virtual-images \
  --initialized-image /private/path/libexec.so.decoded-strings-memory.bin \
  --output-dir /tmp/android-loader-record-two
```

Compare both complete `android-loader-record-two-*.json` files with
`expected_results/`. The pinned retained initializer image supplies only seven
whitelisted registration signatures. `b2b`, `m` and `sa` are exact stubs;
the real `al` ClassLoader method is a next static lead, not a recovered SDK
serializer or executable decoder. No Android/JNI/VM method is invoked.

The [ClassLoader boundary](../../docs/android-loader-classloader-boundary.md)
adds 336 static instruction checks and exact JNI-header slot verification.
The [complete container record](../../docs/android-loader-container-record.md)
adds 1,063 checks, 530 symbolic control-flow states and all 1,640 body bytes:

```sh
python3 tools/firmware_analysis/inspect_android_loader_classloader.py \
  --base-apk /private/path/base.apk \
  --images-dir /private/path/recovered-virtual-images \
  --initialized-image /private/path/libexec.so.decoded-strings-memory.bin \
  --jni-header /path/to/pinned/include/jni.h \
  --output-dir /tmp/android-loader-classloader
python3 tools/firmware_analysis/inspect_android_loader_container_record.py \
  --base-apk /private/path/base.apk \
  --images-dir /private/path/recovered-virtual-images \
  --initialized-image /private/path/libexec.so.decoded-strings-memory.bin \
  --output-dir /tmp/android-loader-container-record
```

Compare each complete result and manifest against `expected_results/`. The
retained `ijiami.dat` has a type-3 header; the candidate callback requests
`classes.dex`. Its runtime key and actual filename selection remain unresolved.
The later [archive callback proof](../../docs/android-archive-callback.md)
resolves the callback to an archive entry reader. These tools perform no
transform or Android/JNI/VM execution.

## Additional saved-state export checks

```sh
python3 tools/firmware_analysis/emulate_gen2_status_export_limits.py \
  --output-dir /tmp/gen2-status-export
```

Compare both `gen2-status-export-limits-*.json` files with `expected_results/`.
The 29 offline cases execute actual A4/D9/DA/FA serializers in synthetic RAM,
retain declared harness substitutes, and demonstrate distinct saved settings
with identical responses. FA reads no saved settings and retains some caller
buffer bits. See [response limits](../../docs/gen2-status-export-limits.md);
no complete export, physical behavior or additional model support is claimed.

## Server continuation: archive, full status and counter epochs

```sh
python3 tools/firmware_analysis/inspect_android_archive_callback.py \
  --base-apk /private/path/base.apk --images-dir /private/path/images \
  --initialized-image /private/path/libexec.so.decoded-strings-memory.bin \
  --output-dir /private/output/archive-callback
python3 tools/firmware_analysis/emulate_gen2_full_status_inventory.py \
  --output-dir /private/output/status-inventory
python3 tools/firmware_analysis/emulate_gen2_energy_epochs.py \
  --output-dir /private/output/energy-epochs
```

Compare corresponding result/manifest pairs with `expected_results/`.
The archive proof adds 44 exact static instruction checks and zero guest
execution. The status suite executes 240 synthetic cases across all 19
callbacks, including twelve complete-response collisions. The epoch suite
adds 13 actual-instruction reset/encoded-wrap cases. These counts are distinct;
none is a physical test. See [progress](../../docs/server-research-progress.md).

`inspect_android_selector_two.py` takes the same private input arguments as
the archive tool and covers all 406 body bytes of the next 410-byte record:
127 static states, 984 exact instruction checks, three unexecuted allocator
calls. No runtime key or plaintext SDK is recovered; see
[the selector boundary](../../docs/android-selector-two-record.md).

`emulate_gen2_factory_aggregate.py --output-dir /private/output/factory-aggregate`
adds seven synthetic cases for factory selector 1/property 0016. Set
`SOLIX_ANALYSIS_OUTPUT` to that output directory before running. Its ten-byte
aggregate excludes saved backup records and has cache/timer side effects;
see [the export limits](../../docs/gen2-factory-aggregate-export-limits.md).

`inspect_android_selector_two_dataflow.py` takes the same private SDK input
arguments. It adds 43 exact checks and inventories both symbolic paths' stores
without evaluating memory/native effects. The direct writer is `context+128`;
runtime aliases and the `+160` key remain unresolved. See
[dataflow limits](../../docs/android-selector-two-dataflow.md).

`emulate_gen2_asset_transfer_start.py --output-dir /private/output/asset-transfer-start`
adds 11 actual-instruction cases for an FTP-named startup route, internal
descriptor/serializer and CRC. Set `SOLIX_ANALYSIS_OUTPUT` to that output
directory. The fixed first request reads no saved settings and startup changes
transfer/timer state; no external export query is implemented. See
[asset-transfer limits](../../docs/gen2-asset-transfer-export-limits.md).

`emulate_gen2_asset_transfer_replies.py --output-dir /private/output/asset-transfer-replies`
adds **30 separate cases** for both MAIN replies, timeout cleanup, the radio
resource descriptor/locator serializer and its reply. Four negative guards
reject malformed or unbounded inputs. Set `SOLIX_ANALYSIS_OUTPUT` to the same
directory. Queue/libc/transport/application callbacks are explicit substitutes;
internal `003d` forwards a string without reading controller settings. Later
radio/chunk processing and complete export remain unresolved. Compare both
`gen2-asset-transfer-replies-*.json` files with `expected_results/`.
