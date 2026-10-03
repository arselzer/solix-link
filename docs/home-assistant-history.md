# Home Assistant history diagnostics

The SOLIX Link integration can display the gateway's optional persistent AC
energy estimates. Enable recording on the gateway as described in
[Persistent history](persistent-history.md), then enable the desired diagnostic
entities in **Settings → Devices & services → SOLIX Link → device → Entities**.
No Home Assistant entity or charging automation is enabled by this feature.

## Entities and meaning

All eight history sensors are **disabled by default**:

| Sensor | Meaning |
| --- | --- |
| Estimated lifetime AC input energy | Integrated AC input power, in kWh |
| Estimated lifetime AC output energy | Integrated AC output power, in kWh |
| AC input / output history coverage | Valid integrated intervals for each channel, in seconds |
| History gaps | Persisted continuity-break count |
| History collection start | First saved telemetry timestamp for this public station name |
| History sampler last update | Last successful complete cached-fleet recording poll |
| History continuity | Continuous chain, open gap, or awaiting a first report |

The energy estimates include only intervals that have valid AC power at both
ends and satisfy the gateway's interval limit. Missing intervals are excluded.
Zero coverage means no energy has been integrated; it does not establish zero
physical consumption. A continuous chain does not establish complete channel
coverage: missing input or output power can leave that channel uncovered. The
energy entities also carry `coverage_seconds`, `gap_count`, `collection_start`,
sampler/source timestamps, interval limit and `estimated: true` attributes.

AC input can include mains passing through to attached loads. AC output can
come from mains or battery. These are neither firmware energy counters nor
calibrated meters, and do not measure energy stored in the battery. No energy
statistics state class or automatic Energy-dashboard enrollment is provided.

## Availability, epochs and limits

A healthy recorder can serve saved totals while a station is disconnected;
its open-gap indication makes the interruption visible. Live telemetry and
control entities retain their existing connection and freshness requirements.
A failed gateway poll makes all its entities unavailable. A failed history
request makes only history unavailable, while valid live monitoring continues.
The sampler timestamp must be no more than 90 seconds old or five seconds in
the future. First-report and collection-start timestamps may be unknown.

Changing a station's public name starts different history. Within an unchanged
collection-start epoch, decreasing totals, coverage, gap count or timestamps
are rejected for that station until the previous values are reached again.
An explicitly changed epoch permits a reset. This check is held in integration
memory and resets when HA reloads the integration. Collection start is not a
database UUID: recreation that repeats the same first telemetry timestamp
cannot be distinguished from an in-place reset. Unknown legacy epoch starts
remain unknown. These limits are another reason not to treat the estimates as
long-term calibrated energy statistics.

## Read-only API contract

HA requests authenticated `GET /history/summary` once every 30 seconds for the
whole fleet, with a three-second request deadline and 64-KiB response cap.
Disabled recording or an older gateway's `404` is retried every five minutes.
No per-station history scan, BLE/MQTT request, command, capture download or
database modification is performed by HA. History polling shares the existing
coordinator I/O lock; an in-flight summary can delay a queued action by its
bounded request deadline. `401`/`403` requires gateway reauthentication;
other errors and malformed envelopes affect history only.

Schema version 1 requires exact `enabled`, `estimated: true`, `read_only: true`
flags. An enabled response additionally requires `recording: true`,
`updated_at`, `limits.max_power_w`, `limits.max_gap_seconds` and at most 32
station records. Each record has public name/model/protocol, collection start,
last accepted report timestamp, exact boolean `gap_open`, and finite nonnegative
`lifetime_totals`: `ac_input_energy_kwh_estimate`,
`ac_output_energy_kwh_estimate`, `ac_input_coverage_seconds`,
`ac_output_coverage_seconds` and integer `gap_count`. A disabled response may
omit the enabled-only fields. Unknown fields are dropped. Duplicate or invalid
station records are isolated from healthy peers; a station must match the live
fleet's public name, model and transport. Private identifiers, credentials,
database paths and arbitrary server error text are excluded from entity data
and diagnostic exports.

## Verification

```sh
python3 -m pytest home_assistant_tests/test_history_contract.py -q
```

The suite uses synthetic summaries, scoped HA platform doubles and a loopback
HTTP server. It verifies absent/disabled/error APIs, privacy, chunked response
bounds, finite values, per-channel limits, gaps/restarts, epoch resets, partial
fleets, dynamic discovery and serialized requests. It does not exercise HA's
state machine, registry UI, a live station or calibrated physical metering.

The eight descriptions were also imported in an independent Python process
against HA **2026.7.4**. Disabled defaults and energy units with
`state_class=None` were confirmed. That check did not load the new component
into the running integration or change HA configuration. Deployment remains
separate from the current runtime observation.
