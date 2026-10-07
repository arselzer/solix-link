# Observed native AC energy estimates

## Support and meaning

C1000 Gen 2 **main 1.1.4.9** can supply two Energy-compatible HA sensors:
**Observed Standard AC input energy estimate** and **Observed Standard AC output
energy estimate**. They count ordered increases in native Standard-mode counters
using the firmware-supported nominal **1 Wh/raw unit**. Both include mains
bypass; neither measures battery-only charge/discharge. Other modes are not
included. A TOU/backup interval may therefore leave these Standard totals flat.

The first usable report establishes a zero baseline. Existing device totals are
not credited, and no earlier daily consumption is backfilled. The accumulator
is an **observed estimate**, not a calibrated lifetime meter. Independent meter
calibration and physical restart retention remain outstanding. C2000's unresolved
[scaling discrepancy](native-energy-validation.md) keeps its native counters out
of statistics; original C1000 uses a different report schema.

## Persistence, ordering and uncertain boundaries

The existing private `energy-state.json` gains an optional `meter` object. It
persists a random generation, initialization time, per-channel raw anchors and
integer accumulated deltas, together with the accepted radio/receipt times.
Ordinary gateway or HA restarts keep the generation and accumulated values.
Legacy files migrate when a timestamped, qualified report arrives; corrupt or
unsafe state remains preserved and unusable.

Only the upload's top-level `utc_ts` is retained as `event_timestamp`. It is
**radio request-construction time**, not established MCU snapshot time. A
multi-event request has no established member order. Older uploads and exact
timestamp/counter duplicates earn no energy and do not refresh the meter.

The meter freezes in persistent **quarantine** for counter decreases (including
possible wrap), conflicting same-time reports, missing/stale/future timestamps,
missing AC channels, ambiguous batches, a changed firmware version, report gaps
of 1,800 seconds or more, overflow, or implausible deltas. A broad 10 kW ceiling
plus two rounding units bounds accepted increases. No reset/wrap correction or
cross-gap energy is guessed. Quarantine does not affect station operation or
ordinary diagnostics. Recovery currently requires reviewed offline state
maintenance after preserving the evidence; there is no automatic rebase or
station reset. Do not edit/remove the active worker's state file.

## Interfaces and HA configuration

`native_energy.meter` reaches SDK, cached HTTP/SSE and `gateway-energy` JSON.
Terminal F7 and line status add Observed Standard rows. Prometheus exposes
`solix_native_meter_energy_kwh_estimate`, availability and initialization time
as gauges; a new generation can restart the estimate, so it is not a raw
Prometheus lifetime counter. Raw counters remain separately available.

The two HA entities have energy/kWh, `state_class: total`, no diagnostic category,
and an explicit `last_reset` equal to the persisted **gateway meter's** start.
This is not a device boot timestamp. Unavailable/quarantined data cannot advance
statistics. Enable the desired entities or the existing **Enable newly discovered
native energy sensors** option, then select the appropriate AC boundary under
Energy → Individual devices. Keep them recorded. Do not add input and output as
independent consumption, or double-count chained stations.
[HA sensor contract](https://developers.home-assistant.io/docs/core/entity/sensor/),
[Energy requirements](https://www.home-assistant.io/docs/energy/faq/)

## Restart test still pending

The user authorized a C1000 Gen 2 restart, then confirmed they were unavailable
for a physical button restart. No verified remote ordinary restart command is
implemented. The app's generic `action_restart_device` string does not establish
model support, and factory/upgrade reset handlers are not substitutes. No
station restart, reset or output command was sent. A future test must capture
native reports before/after the exact authorized restart; host monitoring
restarts only test gateway persistence. Leave the server-backed C2000 untouched.
