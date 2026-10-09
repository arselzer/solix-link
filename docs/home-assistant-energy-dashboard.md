# AC energy and the Home Assistant Energy dashboard

Implementation updated **2026-10-09**; native continuity changes are prepared,
not deployed by this checkpoint. The earlier Integral-helper source audit
used Home Assistant Core **2026.9.4**; the live installation uses **2026.7.4**.

## Built-in energy entities

| Model | AC input/output energy estimate | Observed native Standard AC estimate |
| --- | --- | --- |
| C300 AC | Cached power history, when the channel is reported | Not established |
| Original C1000 | Cached power history | Installed 1.7.1 native schema unresolved |
| C1000 Gen 2 | Cached power history, all observed modes | 1.1.4.9 nominal Wh |
| C2000 Gen 2 | Cached power history, all observed modes | 2.1.6.4 assumed Wh, no rescaling |

Enable history collection on the gateway and **Configure → Enable newly
discovered AC energy estimates** in HA, or enable the two entities individually.
The new entities are **AC input energy estimate** and **AC output energy estimate**:
energy/kWh, `state_class: total`, no diagnostic category and a persisted history
initialization time as `last_reset`. Once Recorder has registered statistics,
select the appropriate sensor under **Energy → Individual devices**. Existing
history diagnostics keep their old IDs and do not gain a statistics state class.

Power-history and native estimates cover overlapping flows. Choose **one source
and one AC boundary per consumption**, rather than adding both estimates or both
input/output. Native estimates cover Standard mode only and retain their unit
assumptions; history estimates omit recording gaps. Neither is battery-only
energy. Initial lifetime values do not backfill earlier daily consumption.

## What already measures kWh

The optional gateway [history recorder](persistent-history.md) integrates
reported **AC input and AC output watts** separately. With `--history-file`
enabled, `GET /history/summary` returns persisted
`ac_input_energy_kwh_estimate` and `ac_output_energy_kwh_estimate` totals.
Per-device history, CLI `gateway-history`, terminal F5 and browser saved charts
also expose these estimates. The recorder uses cached telemetry, without
additional station requests.

For valid consecutive samples:

`estimated kWh = (previous W + current W) / 2 × seconds / 3,600,000`

For example, 100 W for one covered hour is 0.1 kWh. Both endpoints must have
valid power and increasing source timestamps within the recorder's interval
limit, normally 15 seconds. Repeated cached readings add nothing; missing,
stale or conflicting reports and process restarts break the chain. Totals
survive restart and sample pruning, but excluded intervals are not recovered.
Coverage seconds and gap counts describe that limitation.

| Intended consumption | Suitable channel | Meaning |
| --- | --- | --- |
| Station and attached loads drawing from the wall | AC input energy | Includes battery charging and mains bypass |
| Servers/appliances supplied by the AC sockets | AC output energy | Load delivery from mains or battery |
| Battery charge/discharge or stored energy | Neither AC channel | Bypass, other ports, losses and changes in stored energy prevent isolation |

Choose the boundary that answers the question. Input and output are overlapping
flows; do not add them as independent consumption. For chained stations, use
one boundary or configure appropriate upstream relationships. HA documents
individual-device hierarchies to avoid counting nested loads twice.
[Individual devices](https://www.home-assistant.io/docs/energy/individual-devices/)

## Diagnostics versus statistics

The two [HA history energy entities](home-assistant-history.md) already have
`device_class: energy` and `unit_of_measurement: kWh`, but are disabled by
default and intentionally have **no statistics state class**. Enabling them
only enables diagnostics; it does not make them Energy-dashboard inputs. Use
the separately named built-in estimates described above.

For cumulative energy, HA requires `state_class: total` or `total_increasing`,
a supported unit, and a sensor without statistics errors. Keep the chosen
entity recorded so long-term statistics can be collected. Current power
entities already have `device_class: power`, `state_class: measurement` and W;
they can provide power views, but cannot supply accumulated kWh by themselves.
[Energy FAQ](https://www.home-assistant.io/docs/energy/faq/),
[Long-term statistics](https://www.home-assistant.io/docs/configuration/long-term-statistics/)

Being an estimate does **not** disqualify a sensor. Native-counter calibration
is a separate investigation; derived AC consumption can be supported without
solving battery accounting.

## Available option: HA Integral helper

Create an **Integral** helper under **Settings → Devices & services → Helpers**.
Use the desired AC power sensor, metric prefix **k**, and time unit **h** to
produce kWh. For frequently sampled variable power, start with trapezoidal
integration; left integration is useful for loads that change in steps.
Then select the resulting energy sensor under Energy's individual devices.
HA restores the accumulated helper value across restart.
[Integral helper](https://www.home-assistant.io/integrations/integration/)

Example only; replace the entity ID with the existing station's W sensor:

```yaml
sensor:
  - platform: integration
    source: sensor.example_station_ac_input_power
    name: Example station AC input energy estimate
    unit_prefix: k
    unit_time: h
    method: trapezoidal
    round: 6
    max_sub_interval: 0
```

The helper's `max_sub_interval` forces integration while a numeric source is
unchanged; it is **not** a maximum telemetry gap. Zero disables that timer.
Keep unavailable readings unavailable, rather than converting them to zero or
holding the last watts indefinitely. This fallback does not reproduce the
gateway recorder's per-channel coverage or 15-second interval rejection.

### Gap audit and evidence limits

Selected methods from the actual HA **2026.9.4** implementation were replayed
with synthetic states; HA callbacks were replaced. With 100 W and a 60-second
explicit unavailable interval, trapezoidal and left methods add zero during
recovery; right adds 0.001666667 kWh using the new power. When both endpoints
remain numeric an hour apart, all three add 0.1 kWh: none checks source
freshness or rejects the long interval. Timer source review likewise shows
that a still-numeric value is treated as constant. Use trapezoidal or left for
this fallback and verify outage/restart behavior on the target HA release.

This is bounded Python method replay and source inspection, **not** a running
HA integration, Recorder test or physical metering experiment. The official
source tests also cover unavailable-source timer cancellation and state
restoration; they were inspected, not executed here.
[Implementation](https://github.com/home-assistant/core/blob/2026.9.4/homeassistant/components/integration/sensor.py),
[Upstream tests](https://github.com/home-assistant/core/blob/2026.9.4/tests/components/integration/test_sensor.py)

## Built-in continuity handling

Prefer opt-in Energy-compatible entities backed by the existing gateway
integrals; this preserves their explicit gaps and higher-frequency telemetry
instead of integrating HA's polled values a second time.

1. A persisted database generation/station epoch is exposed by the bounded
   history summary. Preserve it across normal restarts and pruning; change it
   on genuine recreation/reset. Public-name changes already create new history.
2. HA persists continuity checks across integration reloads and rejects
   unexplained regressions. A new generation is accepted only when its first
   source timestamp follows the previous accepted source timestamp.
3. Separate disabled-by-default energy entities have kWh and an appropriate state
   class. HA recommends `total` for never-resetting totals; genuine reset
   counters need explicit reset handling or `total_increasing`. Test the
   chosen semantics, including a replacement first observed above zero.
4. Verification includes synthetic unavailable periods,
   gateway/HA restart, sample pruning, resets, device renaming and partial
   fleets. Keep coverage/gap diagnostics and clear “estimate” names. Dashboard
   selection remains a user configuration step.

HA initially establishes a statistics baseline; existing lifetime values do
not automatically backfill earlier daily consumption.
[Sensor statistics contract](https://developers.home-assistant.io/docs/core/entity/sensor/)

The separate [native-meter continuity guard](native-energy-meter.md#ha-continuity-guard--prepared-2026-10-09)
also persists raw anchors, totals and source/receipt timestamps before exposing
native Energy values. Older gateway backups and storage failures become
unavailable with a HA Repair; raw diagnostics remain visible. First adoption
cannot repair earlier statistics or establish continuity after simultaneous
loss of gateway and HA stores.

## Native counters and verification

The stations **already accumulate energy themselves**; these counters are not
invented by the phone. A separate [app chart audit](app-energy-statistics.md)
traces a cloud-history request and its returned kWh totals. Local report
capture/decoding now feeds per-device persisted snapshots, SDK/API, terminal F7,
browser and optional HA diagnostic sensors. Exact app-category mapping and
device reset epochs remain unresolved. See [native values](native-energy-values.md).

Gen 2 firmware has raw AC-input/output and other-port counters. A1763 main
1.1.4.9 instruction replay supports nominal Wh arithmetic, but physical scale,
snapshot timing, missed callbacks, wrapping and reset/retention behavior remain
unverified across models. Those `*_energy_raw` values remain excluded from
HA lifetime statistics. Battery energy needs independent AC/DC measurements,
controlled SOC changes and per-model reset/retention tests.
[Counter epochs](gen2-energy-epochs.md)

The [2026-10-07 native validation](native-energy-validation.md) adds eleven
covered report intervals from the running deployment. C1000 Gen 2 nominal Wh
is consistent with integer rounding; C2000 shows a repeatable roughly 11%
discrepancy, with a 0.9 Wh/unit hypothesis fitting its tested Standard AC
channels. This is a comparison to reported watts, not independent calibration.
The separate [observed Standard AC estimates](native-energy-meter.md) now
provide Energy-compatible C1000 Gen 2 and C2000 Gen 2 entities with persisted ordering and
quarantine guards. They begin at a fresh zero baseline, omit ambiguous intervals
and do not claim calibrated lifetime or battery energy. C2000 scaling and the
physical station restart test remain open.

Focused verification covers history arithmetic, summaries and entity behavior
using synthetic readings and HA doubles. Deployment and actual HA Recorder
results are recorded separately in [research progress](server-research-progress.md).

Public source SHA-256 pins for the gap/statistics review:

| HA 2026.9.4 path | SHA-256 |
| --- | --- |
| `homeassistant/components/integration/sensor.py` | `d752a7cb511adb20a2f19372346897034de7f837b11f2343e89e8449d7db2923` |
| `tests/components/integration/test_sensor.py` | `899ebc4a45f4b878beb3d1ee715df35b32cdbbd6afa5790a88688e0e914d5d43` |
| `homeassistant/components/sensor/recorder.py` | `ea8cf79e9367fb80f07b34594e659b92f04ecdefb2f11feb4d2be644c213de69` |
