# Offline adaptive-policy timeline replay

`solix-link policy-replay` evaluates a sequence of supplied snapshots and
signal samples using the [adaptive policy](adaptive-policy-preview.md). It
does not connect to a station, apply proposals, activate automation or predict
battery SOC, grid power or conversion efficiency.

```sh
solix-link policy-replay --timeline-file examples/policy-replay/surplus.json
solix-link policy-replay --timeline-file examples/policy-replay/surplus.json \
  --format svg > policy-preview.svg
```

The second command produces a standalone visualization with no external
assets or scripts. The [included illustration](images/adaptive-policy-replay.svg)
uses entirely synthetic inputs.

## Input contract

A document has exactly `schema_version: 1`, `request` and `frames`.
`request` is a complete adaptive request including starting signals and
previous-preview state. Each frame contains:

- `timestamp`: increasing evaluation time, in the same clock as the snapshot
  and signal timestamps; duplicate, backward or nonfinite times reject.
- `snapshot`: independently supplied model/transport, connectivity, freshness
  and current metrics. Unknown station identities are never reflected in output.
- `signals`: the matching export or price sample and its original timestamp.
- Optional `manual_override`: one frame's override; it does not persist.

The example demonstrates bounded charging steps, cooldown, no surplus, a stale
snapshot, reserve recovery and manual hold. A later frame's saved watts are
explicit inputs; the replay never manufactures them from an earlier proposal.

## State and limits

An eligible frame can advance `next_preview_state`; the next frame then uses
it for hysteresis and cooldown. A blocked frame preserves that state. This is
**previous-preview state**, not confirmation that any action occurred. Results
include elapsed times and fixed reason codes, with `commands_sent: 0`,
`executor_available: false`, `snapshots_modified: false` and
`electrical_behavior_verified: false`.

If a supplied later snapshot has an active or saved TOU plan, the current price
preview blocks rather than claiming ownership or reconstructing it. Original
C1000 stays excluded. Request errors use fixed `InvalidPolicyReplay` text;
the local file reader also rejects duplicate JSON keys, symlinks and inputs
over 1 MiB. The pure replay accepts 1–256 frames.

This is a decision replay, not a closed-loop electrical simulator. Metered
physical tests, complete required readback, exclusive ownership and explicit
activation remain prerequisites for any future executor.
