# Owned price charging in Home Assistant

`solix_link.price_policy` adds opt-in price-driven all-day TOU control for
**C1000 Gen 2 main 1.1.4.9**. The service defaults to **preview, disarmed**.
The [blueprint](../blueprints/automation/solix_link/price_charging.yaml) starts
disabled. Installing either does not activate an automation or change a station.
The existing terminal/browser adaptive previews remain read-only.

## Setup

1. Install the updated gateway and complete HA integration directory.
2. Start with **Standard mode and no saved TOU periods**. Existing nonempty
   plans are never adopted or backed up by this policy.
3. Configure the station's reserve yourself, at least the policy minimum
   (default 20%). The policy does not change power, caps or reserve.
4. Select a fresh price sensor whose unit ends in `/kWh`, such as `EUR/kWh`.
   Thresholds use that same currency and unit; `/MWh` is rejected.
5. Create an arming `input_boolean` initially off and an `input_select` with
   `none`, `hold`, `charge`, `grid`, `battery`. Select both in the blueprint.
6. Check a service **preview** with `armed: true`, then review the thresholds
   before enabling the automation and arming it. Required order:
   `charge_start < charge_stop < discharge_stop < discharge_start`.

Cheap prices propose all-day Off-Peak, expensive prices propose all-day Peak,
and intermediate prices restore an empty Standard plan. Decisions use confirmed
previous actions for hysteresis, a cooldown (default 300 seconds), price age,
SOC, reserve and a discharge restart margin. Release and reserve protection may
leave a battery-use plan without waiting for the cooldown.

## Ownership and recovery

HA persists a **pending** record *before* sending a command, including the
protected settings and expected plan. The exact decision snapshot supplies the
gateway's optimistic preconditions. Fresh matching plan, activation and settings
readbacks are required before recording **active** ownership. An ACK is not
enough. Failed persistence prevents a write; cancellation, lost results and
failed confirmation prevent automatic replay after restart.

Freshness, mains, AC output on, Fast off, inactive disaster preparation, clock
and output timers are required. Original C1000 and C2000 are excluded: original
charging behavior and C2000's charging override readback remain insufficiently
qualified for automatic policy execution.

The **Charging policy ownership** diagnostic shows `active`, `pending`, `blocked`
or a storage error; `unowned` means no policy owns a plan. Manual HA controls revoke
ownership before their write. Changes observed through another client also
block the policy; polling cannot detect changes made and reverted between reads.
Do not run another charging automation on the same station.

Price and surplus now use the same durable owner. Release the matching policy
before switching kinds; neither can reset or take over the other's state. The
existing price action remains supported. See
[shared charging ownership and Repairs](charging-controller-and-repairs.md).

| Service mode | Effect |
| --- | --- |
| `preview` | Evaluate without saving ownership or sending commands. |
| `apply` | Require `armed: true`; execute at most one proposed plan operation. |
| `release` | Clear only the currently matching owned plan, then forget ownership after confirmation. No ownership means no command. |
| `reset` | Forget a latch without a station command, only after a fresh empty Standard plan is already present. |

The blueprint requests release when disarmed. **Hold leaves the current plan
in place.** Stale/invalid prices stop evaluation without clearing the last
plan. The blueprint's `initial_state: false` also starts it disabled after HA
restart; explicitly review that choice before arranging unattended operation.
Disabling the automation, stopping HA, losing connectivity or
unloading the integration does not clear a persistent station plan. After an
uncertain result, inspect the station, restore an empty Standard plan explicitly
if appropriate, then use `reset`; do not clear a pending record to force a retry.

Plan confirmation establishes stored settings and activation, not electrical
power flow. Release does not independently verify grid watts; use the existing
Return to grid control for that check. Backend TOU control retains its existing
bounded rollback behavior; this policy still latches any failed operation.
No output switch is sent, and there is no zero-import or true charging-pause
promise. Ownership/execution tests are synthetic; unattended price cycling has
not been physically validated.
