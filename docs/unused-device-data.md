# Available device data not yet exposed

Audit dated **2026-10-07**, based on current decoder/presentation code, cached
gateway snapshots and a bounded 6 MiB tail of locally retained station traffic.
No station query, control command, cloud request or phone-capture access was
needed. Raw requests, identities and counter readings remain private.

## Cached audit after the kWh deployment (before presentation update)

A new cached-only check after `5826a07` confirmed all three deployed models
available. It compared public metric names with HA's metric allowlist and entity
descriptions, without printing readings or identities or issuing station queries.

| Candidate | Confirmed presentation gap | Reliability boundary |
| --- | --- | --- |
| Original C1000 USB-C1/C2, USB-A1/A2 and DC-input watts | Present as numeric decoded fields; absent from HA allowlist/entities | Expose as reported port power. This check does not validate a loaded port or calibrate watts. |
| Remaining charge/discharge time | Gen 2 values present but excluded by HA; original C1000 currently explicitly unknown | Device estimate, with charging/discharging labels and idle/sentinel handling; not a guaranteed runtime. |
| AC/DC output countdowns | Original C1000/C2000 report numeric values; HA keeps AC metadata but has no countdown sensor, and drops DC | Remaining timers are distinct from saved output/device timeouts. Nonzero countdown transitions are not exercised here. |
| Component firmware versions | C2000 controller/inverter/BMS/module and C1000 Gen 2 module versions present but excluded by HA | Stable reported version diagnostics, not measurements or update availability. |
| Wi-Fi RSSI on C1000 Gen 2 | Separately validated operator query, excluded from routine HA polling | Known 1.1.4.9/radio 0.3.3.0 read-only route; this audit did not query it. See [native validation](c1000-gen2-native-rssi-validation.md). |

The five candidates in this table were subsequently integrated in
[reported telemetry](reported-telemetry.md), with model guards and opt-in RSSI
polling. The table records the original audit.

These were primarily presentation gaps: the SDK/cached HTTP already retains the
telemetry. Current cached presence does not prove that every retained field was
refreshed with the most recent incremental packet. Port power and firmware
diagnostics are the simplest next additions, followed by guarded duration sensors.

Expansion fields are also decoded, but valid expansion SOC/temperature entities
must require actual expansion presence; C2000's value is only a presence flag.
Health bytes, original-model BMS/charging-source codes, Gen 2 raw PV power,
unmapped fault meanings and extra energy-report blocks still require semantic or
physical validation. The controller UTC field is a retained diagnostic, not a
fresh clock-accuracy measurement. No sensor was enabled or station setting changed.

## Energy and report fields

| Source | Available evidence | Current use / remaining work |
| --- | --- | --- |
| Original C1000 main 1.7.1, `charging_pps_series_c_0002` | 27 request records in the bounded log window; observed top-level fields 1, 2, 6, 19 and 22. Field 19 contains varints 1–8. Records can include retries, not independent measurements. | Upload is retained privately but rejected by the Gen 2-only decoder. Map the original encoder, port sources, scaling, scheduler and resets before adding model-specific counters. |
| C2000 Gen 2 `charging_pps_series_c_0009` | Two request records; additional repeated blocks 14/15, numeric field 22 and nested blocks 23/24 beyond the four decoded mode groups. | Ignored by the public partial decoder. Trace each descriptor/getter before naming diagnostics or assigning units; some nested strings are identifiers and must stay private. |
| General mode-group duration fields | Four raw enable/charge durations per mode are already retained. | Accessible as raw group values, including HA attributes; no converted duration sensors. Firmware timing is suggestive, not physical calibration. |
| Backup variants, nested field 9 | Present alongside the eight decoded group counters in C2000 uploads. | Unknown meaning; do not add it to energy totals. |
| Tariff-only accumulator | C1000 Gen 2 firmware separately tracks solar/grid-to-battery and battery-to-load sums. | No established read-only public transport. Not interchangeable with general AC counters or a ready battery-energy sensor. |
| C2000 MQTT `0503/state_info` | Reference maps six unnamed uint32 words plus a timestamp. | No such message was established by this bounded API-upload audit. Names, scale, epoch and cadence remain unresolved; no invented query is sent. |

Gen 2 counters already exposed are described in
[Native device energy values](native-energy-values.md). They remain uncalibrated
mode counters, not lifetime consumption or measured stored battery energy.

## Original C1000 encoder proof

**Implementation update, 2026-10-08:** the original upload now has a separate
bounded numeric decoder and unitless diagnostics, with no Gen 2 channel aliases
or kWh conversion. Expansion presence-gated telemetry is also integrated.
See [additional controls/data](additional-controls-and-data.md). Live discovery
of original counter sensors awaits a new passive upload after deployment.

The public **main 1.5.9** image has SHA-256
`b295ee8613f5c96e70dcc905896df516621cab4dc590bb580eac6b84519911a6`.
Static inspection finds the `_0002` wrapper at **`08006904`**, its inline report
name at `080069e4`, and the builder callback **`080262ec`** supplied at
`0802666c` through the periodic path. The wrapper selects function `0f`,
command `0401` when network-ready, otherwise `0490`.

[Three bounded actual-encoder cases](../tools/firmware_analysis/emulate_c1000_energy_report.py)
use synthetic RAM at **`20002120`** and the firmware's protobuf descriptor at
**`0802aec4`**. They confirm top-level field **19**, nested fields **1–8**:

- Sum offsets `00`, `10`, `08`, `18` feed fields `3`, `4`, `7`, `8`, using
  unsigned division by **360**.
- Duration offsets `28`, `20`, `2c`, `24` feed fields `1`, `2`, `5`, `6`.
- Encoding leaves the seeded 52-byte counter block unchanged. A dynamic
  protobuf callback is substituted with success and emits no fields.

[Expected output](../tools/firmware_analysis/expected_results/c1000-energy-report.json)
contains synthetic values and source hashes only. This establishes the old
encoder's numeric layout, **not** port identities, Wh units, scheduler timing,
reset/retention behavior or execution of the installed **1.7.1** firmware.
Matching live field shape alone does not establish those properties. `_0002`
must not silently become an alias of `_0009`.

## Decoded telemetry with presentation gaps

These fields already exist in SDK/cached HTTP data. The HA allowlist or terminal
labels omit some; that omission is different from an unknown firmware field.

| Fields | Presentation gap / guard needed |
| --- | --- |
| `time_remaining_minutes` on all three models | Terminal has a label; HA drops it. Distinguish time-to-full from time-to-empty and the original model's bounded/sentinel estimate. |
| C1000 USB-A1/A2/C1/C2 watts and DC input watts | HA drops all; terminal shows USB-C watts but lacks USB-A/DC input labels. Do not substitute ambiguous `AF` as total input. |
| `dc_output_power_w` on Gen 2 | HA exposes it; terminal currently lacks the label. |
| Expansion count/SOC/temperature/health/state on original C1000; expansion flag on C2000 | SDK decodes these but HA/terminal omit them. Require actual expansion presence; C2000's decoded value is a flag, not a proven arbitrary battery count. Health bytes are not independent battery-health measurements. |
| Secondary firmware versions | Browser has labels and SDK decodes available slots; HA metric filtering and terminal omit them. Suitable for diagnostics, not energy. |
| DC countdown remaining on original C1000/C2000 | SDK decodes it; HA/terminal omit it. Keep runtime remaining time separate from saved timeout. |
| C1000 Gen 2 controller UTC time | Decoded from FE; HA/terminal omit it. Retained incremental values must not become host freshness or claimed clock accuracy. |
| Gen 2 clock windows and backup-plan details | Already decoded, but several fields lack HA/terminal presentation. Readbacks are partial; they do not establish a complete settings exporter. |

The first useful additions are original-model counter support after schema/units
validation, port-power sensors, and runtime/firmware diagnostics. Detailed report
blocks need descriptor tracing. True battery kWh still needs independent power
measurements and model-specific reset/retention evidence.
