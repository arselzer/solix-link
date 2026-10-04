# Read-only charging-policy preview

`solix_link.charging_policy.preview_charging_policy(snapshot, request, *, now=None)`
evaluates one cached **native MQTT C1000 Gen 2 or C2000 Gen 2** snapshot.
It returns fixed reason codes and proposed settings with `dry_run: true` and
`commands_sent: 0`. It never polls a station, sends a command, persists policy
state or activates an HA automation. Original C1000 and C300 are unsupported.

The separate [adaptive preview](adaptive-policy-preview.md) adds opt-in offline
surplus steps and price-driven TOU proposals. It does not change this contract,
the original HTTP preview contract or the HA charging blueprint. Terminal and
prepared Vue source select the adaptive contract explicitly.

## CLI and browser

```sh
solix-link charging-preview --snapshot-file snapshot.json --request-file policy.json
solix-link ap-service-charging-preview --directory /private/ap-service \
  --name office --request-file policy.json
```

The first command reads bounded local JSON files; the second reads the saved
AP profile and cached status without starting a worker or opening its socket.
Both return a JSON diagnostic and can report stale cached telemetry. The
optional browser dashboard has a manual-signal form and no Apply action.
Its armed/latch/cooldown assumptions do not change HA helpers. Gateway token
authentication still applies when controls are disabled.

## Request contract

The HTTP interface is `POST /devices/{name}/charging-preview`. The gateway
supplies its cached snapshot and current server time. The request contains only
`config` and `signals`; the body limit is **4096 bytes**. All config fields below
are required, including the thresholds for the unused signal mode:

```json
{
  "config": {
    "signal_mode": "price",
    "armed": true,
    "command_latch": false,
    "latch_changed_at": 700,
    "charging_watts": 1000,
    "idle_watts": 300,
    "minimum_reserve": 20,
    "cooldown": 180,
    "price_start": 0,
    "price_stop": 0.05,
    "price_max_age": 7200,
    "export_start": 600,
    "export_stop": 300,
    "export_max_age": 30
  },
  "signals": {
    "price": {"value": -0.01, "timestamp": 1000}
  }
}
```

Those timestamps are synthetic: the example is suitable for a direct function
call with `now=1000`, not a current gateway request. Supply real Unix seconds
for the signal reports and the policy latch's last change. `armed` and
`command_latch` describe the caller's assumed policy state; these inputs do
not change an HA helper or prove ownership of a station.

For `export`, supply only this signal instead of `price`; `either` requires
both signals, even when only one opportunity is favorable:

```json
{"export": {"value": 700, "timestamp": 1000, "unit": "W", "positive_means": "export"}}
```

| Fields | Accepted values |
| --- | --- |
| `signal_mode` | `price`, `export`, `either` |
| `armed`, `command_latch` | JSON booleans |
| Timestamps | Finite Unix seconds, 0–253402300799; booleans/strings rejected |
| Charging/idle watts | Integer 100 W steps; idle ≤ charging; C1000 100–1200 W, C2000 300–1800 W |
| `minimum_reserve` | Integer 5–100%, 5-point steps, within the current lower/cap bounds |
| `cooldown` | Integer 60–3600 seconds |
| Price thresholds | Finite -1000–1000, start < stop, expressed in the supplied price's own units |
| `price_max_age` | Integer 30–86400 seconds |
| Export thresholds | Integers 0–20000 W, stop < start |
| `export_max_age` | Integer 5–300 seconds |
| Signal values | Finite numbers; booleans/strings/NaN/infinity rejected |

No units or currency conversions are inferred for price. Export requires the
exact unit `W` and the explicit positive-export declaration; this does not
independently verify the sensor's sign or source. Unknown fields, missing
selected signals, entity IDs, URLs and credentials are rejected. Malformed
requests raise `ChargingPolicyRequestError` with fixed text
`InvalidChargingPreview`; the HTTP adapter reports a request error without
reflecting input values.

## Guards and decision

The decision matches the [HA charge-only blueprint](../blueprints/automation/solix_link/opportunistic_charging.yaml):

- The cached station must report connected and available, with no current
  error. Actual `last_seen_timestamp` age must be **-5 to 30 seconds**; no
  timestamp is synthesized from a poll or from `last_seen` text.
- Mains and AC output must be confirmed on, Fast confirmed off, usage mode
  `standard`, and active tariff `none`. Missing flags never become defaults.
- Current saved watts, battery percentage, cap, discharge floor and reserve
  must be complete native integers in their validated domains. Reserve must
  satisfy `lower + 5 ≤ reserve ≤ cap`. The policy minimum also respects the
  HA reserve entity's rounded minimum.
- The caller must report armed, latch off, and elapsed cooldown. A latched
  result never clears or retries itself. Selected signals must be fresh,
  with at most five seconds of future skew; stale/invalid data cannot be
  overridden by emergency reserve recovery.

Saved charging power above `idle_watts` selects the stop thresholds; otherwise
the start thresholds apply. Price uses `≤`, export uses `≥`. Cheap price,
positive export, or battery SOC below the greater of current/configured reserve
selects `charging_watts`; otherwise the policy selects `idle_watts`.

The reserve is only raised, and the cap/discharge floor are preserved. Proposals
are ordered reserve first, then saved watts, and include only changed values:

```json
[
  {"command": "set-backup-reserve", "reserve": 20},
  {"command": "set-charge-power", "watts": 1000}
]
```

## Response and limitations

Every response contains `schema_version`, `dry_run`, `commands_sent`,
`eligible`, `decision`, `reasons`, `proposed_settings`, `current_settings`,
`desired_settings` and `telemetry_age_seconds`. Decisions are `blocked`,
`opportunity`, `emergency`, `idle` or `unchanged`. Numeric setting summaries
use the actual metric keys `ac_charging_power_limit_w`,
`backup_reserve_percentage`, `max_charge_percentage` and `min_charge_percentage`;
invalid/incomplete current settings produce null summaries. Blocked previews
have no desired settings or proposals.

Reasons are fixed codes such as `telemetry_stale`, `command_latched`,
`fast_not_confirmed_off`, `price_signal_stale`, `below_effective_reserve` and
`settings_already_match`. Arbitrary names, addresses, identifiers, error text,
raw telemetry and request strings are absent from the response.

Unlike HA, this diagnostic cannot establish entity roles, helper identity,
policy ownership, sensor-source correctness or exclusive access. It uses strict
native numeric types instead of HA state-string conversion. Gateway control
permissions are not inferred or enabled. `eligible` means these diagnostic
inputs passed; it is not command authorization.

Each call recalculates from current inputs and does not pretend a previous
proposal succeeded. This cannot make a two-command sequence atomic or cover
changes after the preview; an executing owner must retain the existing fresh
checks and command confirmation, including recomputation after reserve changes.
Saved charging watts are not actual battery watts or a total AC-input budget.
No charging pause, forced discharge, TOU, output, Fast or cap command is proposed.

## Offline verification

```sh
PYTHONPATH=python python3 -m pytest python/tests/test_charging_policy.py -q
```

The focused suite has **134 synthetic cases**, including twelve comparisons
with the actual HA decision template, timestamp/threshold boundaries, outages,
missing settings, emergency freshness, persistent latches, changed signals
between previews, strict schemas, input immutability and privacy. The template
comparisons need PyYAML/Jinja2; the pure module has no added runtime dependency.
These tests do not execute a station, HA automation or physical charging.
