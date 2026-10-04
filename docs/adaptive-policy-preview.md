# Adaptive charging and battery-use previews

`solix_link.adaptive_policy.preview_adaptive_policy(snapshot, request, now=...)`
adds two opt-in, pure proposals for native **C1000 Gen 2 / C2000 Gen 2**.
Original C1000 remains excluded. The function reads supplied dictionaries,
copies validated settings, and sends **zero commands**. It has no executor,
automation activation, configuration writer or station connection.

The existing charge-only preview and HA blueprint keep their previous behavior.
The new contract is available through the Python function and offline CLI:

```sh
solix-link charging-preview --adaptive \
  --snapshot-file snapshot.json --request-file adaptive-policy.json
```

The authenticated read-only endpoint is
`POST /devices/{name}/adaptive-preview`, using the request below against the
gateway's cached snapshot. It works with controls disabled and never calls
the worker or station. The original `/charging-preview` endpoint retains its
contract. Both reject duplicate JSON keys and bodies above 4096 bytes.

Terminal F6 now has an explicit **Fixed / Adaptive** selector; use the matching
request file. Optional manual value/age inputs retain all original validation
and never write the file or returned state. The cached AP CLI also accepts
`ap-service-charging-preview --adaptive`.

Vue source adds **Fixed charging / Adaptive solar / Price-driven battery use**
choices and candidate-plan explanations. **Browser assets are not rebuilt or
verified on this server: Node is unavailable.** The packaged browser still has
the earlier fixed preview until `npm run build:dashboard` and synthetic browser
checks are run. There is no Apply action for either preview contract.

For sequence testing and a standalone visualization, see
[offline timeline replay](policy-timeline-replay.md).

## Surplus-following proposal

Required request example, with **synthetic timestamps**:

```json
{
  "config": {
    "kind": "surplus",
    "armed": true,
    "command_latch": false,
    "cooldown": 180,
    "manual_override": "none",
    "minimum_reserve": 20,
    "idle_watts": 300,
    "maximum_watts": 1000,
    "export_start": 600,
    "export_stop": 300,
    "export_max_age": 30,
    "target_export_w": 100,
    "deadband_w": 50,
    "maximum_step_w": 200
  },
  "signals": {
    "export": {"value": 900, "timestamp": 1000, "unit": "W", "positive_means": "export"}
  },
  "state": {"previous_decision": "grid", "last_changed_at": 700}
}
```

With `now=1000`, SOC 50%, reserve 20%, saved limit 300 W and fresh eligible
telemetry, this proposes **500 W**, bounded by the 200 W maximum step. In an
export opportunity the target before bounds is:

```text
floor_to_100W(current_saved_limit + export_watts - target_export_w)
```

The deadband retains the current setting. Export start/stop hysteresis uses
whether the saved limit is above `idle_watts`. Outside an opportunity it
proposes a bounded step toward the nonzero idle limit. Emergency reserve
recovery and manual `charge` propose a bounded step toward the maximum.
Current saved power outside the policy range blocks the preview.

This arithmetic assumes a change in saved watts will affect consumption;
that assumption needs physical measurement. Saved watts are not measured
battery charging power. Saturation, bypass, temperature and device policy can
prevent the expected response. No zero-import, charging-pause or efficiency
claim is made; the idle limit continues permitting charging.

## Price-driven TOU proposal

Use the same common config fields, replacing `kind` with `price_tou` and the
six export-specific config fields with:

```json
{
  "charge_start": 0.05,
  "charge_stop": 0.10,
  "discharge_start": 0.30,
  "discharge_stop": 0.20,
  "price_max_age": 300,
  "soc_resume_margin": 5
}
```

Replace `signals` with `{"price":{"value":0.40,"timestamp":1000}}`.
Thresholds use the signal's own price units; no currency conversion occurs.
The ordering must be `charge_start < charge_stop < discharge_stop < discharge_start`.
The explicit previous preview decision supplies price hysteresis:

| Decision | Proposed plan |
| --- | --- |
| Cheap / manual charge / below effective reserve | Enabled off-peak, local hours 0–24 |
| Expensive / manual battery, with sufficient SOC | Enabled peak, local hours 0–24 |
| Neutral / manual grid | Retain the Standard, empty-plan baseline |

These are candidate plans using existing Gen 2 TOU controls, not a new firmware
pause command or a demonstrated automation. There is no forecast, clock write
or runtime grid-return confirmation. Physical battery use still depends on
the station, reserve, cap, load and readiness.

**Price proposals require fresh Standard mode, no active tariff and exactly
zero saved tariff slots.** Active or disabled saved schedules block the preview:
the current gateway snapshot exposes the count, not every saved period and
its ownership. It cannot safely replace, manage or restore an existing plan.
A previous preview of `battery` does not establish actual discharge and never
removes the SOC resume margin. Full plan readback and exclusive control ownership
are prerequisites for a future executor.

## Shared guards and state

The established charging-policy validator checks connected/available native
Gen 2 telemetry, station age -5..30 seconds, mains and AC output on, Fast off,
complete valid SOC/cap/floor/reserve/watts, arming, latch, cooldown and fresh
selected signals. See [the original contract](charging-policy-preview.md).

- Reserve is only raised, never lowered; cap and lower discharge limit stay
  unchanged. Battery-use proposals require SOC **strictly above** effective
  reserve plus `soc_resume_margin`, even for a manual override.
- `manual_override` is `none`, `hold` or `charge` for surplus; price additionally
  permits `grid` or `battery`. Overrides do not bypass freshness or safety
  guards. `hold` blocks all proposals.
- Watts and maximum steps use 100 W increments and model bounds. Target export
  is 0–20000 W; deadband 0–1000 W; SOC margin 1–25 percentage points.
- `state` contains only `previous_decision` (`idle/charge/grid/battery`) and
  `last_changed_at`. It is caller-supplied preview state, not an HA helper or
  confirmation of an executed action. Returned `next_preview_state` is never
  persisted; unchanged decisions without proposals retain its timestamp.

Outputs always include `dry_run: true`, `commands_sent: 0`,
`executor_available: false` and `electrical_behavior_verified: false`.
`proposed_settings` contains setting/value pairs, with any reserve raise first;
`proposed_plan` is separate. Unknown/malformed request fields fail with fixed
`InvalidChargingPreview` text; station names, identities and raw errors are
not echoed. Offline file bounds and duplicate-key rejection remain in force.
