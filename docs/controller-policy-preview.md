# Preview the HA charging-controller rules

The read-only `preview_controller(snapshot, request, now=...)` adapter calls the
same pure `controller_decision` used by HA's shared price/surplus executor.
It acquires no ownership, saves no state and sends zero commands.

## Ownership and model scope

Only native **C1000 Gen 2 / main 1.1.4.9** passes the controller's qualification.
Original C1000 and C2000 return a blocked result. Reserve must already be
configured; this controller does not raise it. Fresh telemetry, independent D9
plan readback, complete protected settings and guarded gateway capabilities are
required, alongside signal freshness, hysteresis, cooldown and manual overrides.

`ownership: null` explicitly assumes an unowned station. A supplied record
simulates HA's validated price or surplus ownership, including active/pending/
blocked phase and protected baseline. It never proves that HA currently owns
that setting. Another policy's owner blocks takeover; matching release/reset
uses the same reconciliation rules as execution.

Output always includes `commands_sent: 0`, `executor_available: false`,
`live_ha_ownership_verified: false` and an `ownership_basis`. No next ownership
record is saved or returned for automatic adoption. For an authoritative preview
against HA's actual durable owner, use **`solix_link.charging_policy` with
`mode: preview`**, as described in [shared ownership](charging-controller-and-repairs.md).
Saved settings alone do not establish electrical charging, discharge or zero
grid import.

## Entry points

- Browser: **Charging policy preview → HA controller** solar/price choices.
  Optional ownership JSON is simulation input; no Apply action is provided.
- Terminal **F6**: choose **HA controller rules / simulated ownership** and a
  request file. Optional manual signals replace only the copied signal values.
- API: authenticated **`POST /devices/{name}/controller-preview`**, with the
  existing device read scope, cached telemetry and 4096-byte request limit.
  Read-only gateways can return previews, but execution prerequisites block
  when guarded controls are not advertised. The route never calls commands.
- CLI: offline snapshot or already-cached AP-service status:

```sh
solix-link charging-preview --controller \
  --snapshot-file snapshot.json --request-file controller.json
solix-link ap-service-charging-preview --controller \
  --directory /private/ap --name office --request-file controller.json
```

Use a saved `/devices/{name}` response for an offline snapshot: it includes
`controls`, `command_context.expected` and independently timestamped plan
readback. These remain cached simulation prerequisites, not an executable
request ticket. `--controller` and `--adaptive` are mutually exclusive.

## Synthetic surplus request

Use a current signal timestamp for a live cached preview. The timestamp below
is deliberately synthetic; the SDK example assumes `now=1000`.

```json
{
  "policy": "surplus",
  "config": {
    "cooldown": 180,
    "minimum_reserve": 20,
    "idle_watts": 300,
    "maximum_watts": 1000,
    "export_start": 600,
    "export_stop": 300,
    "export_max_age": 30,
    "target_export_w": 100,
    "deadband_w": 50,
    "maximum_step_w": 200,
    "positive_export_confirmed": true
  },
  "signals": {
    "export": {"value": 900, "timestamp": 1000, "unit": "W", "positive_means": "export"}
  },
  "ownership": null,
  "armed": true,
  "override": "none",
  "action": "evaluate"
}
```

A qualified Standard baseline with saved 300 W and reserve 20% proposes 500 W.
Reserve 10% instead returns `configure_reserve_before_arming` with no proposal.
The price request uses `policy: price`, a single `signals.price` value/timestamp,
and the eight price-config keys in [owned price charging](owned-price-charging.md).
Overrides are `none/hold/charge` for surplus and additionally `grid/battery` for
price; actions are `evaluate/release/reset`.

## Exploratory previews remain available

The existing fixed `/charging-preview` and adaptive `/adaptive-preview` schemas
are unchanged. They can consider C2000 and propose a reserve increase; their
previous-preview state is not durable ownership. They are labelled exploratory
in browser/terminal and cannot certify eligibility for HA automation. See
[adaptive preview](adaptive-policy-preview.md) and [offline replay](policy-timeline-replay.md).
