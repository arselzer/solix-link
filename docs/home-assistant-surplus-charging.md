# Optional adaptive surplus charging

The [surplus blueprint](../blueprints/automation/solix_link/surplus_charging.yaml)
adds opt-in charging-limit adjustment for native **C1000 Gen 2 / 1.1.4.9**.
It is prepared locally, **not installed or activated**. Original C1000 needs
physical charging measurements; C2000 needs an established charging-override
readback before this adaptive executor can include it. Other firmware versions
are excluded from this first automation. The existing
[fixed price/export blueprint](home-assistant-charging-automation.md) is unchanged.

## Decision

The external sensor must report **W with positive values meaning grid export**.
Verify that sign before enabling. Start/Stop export thresholds provide hysteresis.
When surplus is available, the candidate saved limit is:

```text
error = measured export - target remaining export
candidate = current limit                       within deadband
candidate = floor((current limit + error)/100)*100 otherwise
```

Clamp to configured nonzero idle/maximum watts, then limit each cycle's change
to `maximum_step_w`. Below the greater of saved/minimum reserve, or with manual
`charge`, step toward maximum watts. With no opportunity, step toward idle.
Manual `hold` stops all commands. Reserve can only increase; charge cap and
discharge floor remain unchanged.

The sensor reports grid export, and the command sets a charging preference.
Neither establishes actual battery charging power. Bypass, load changes,
firmware and charging taper affect consumption. This does not promise zero
import, charging pause, battery use or AC-input disable.

## Prepare in Home Assistant

1. Copy the YAML to `<HA config>/blueprints/automation/solix_link/` and create
   an automation with the integration's station and its matching entities,
   including the read-only **Disaster preparation active** diagnostic. Unknown
   or active blocks commands; it is not a complete disaster-plan export.
2. Create distinct Toggle helpers for arming and the command latch. Leave
   arming off. Create a dedicated Dropdown helper with `none`, `hold`, `charge`;
   start at `hold`. Do not share helpers across stations.
3. Confirm the export sensor's units/sign. Choose reserve, nonzero watt limits,
   thresholds, deadband, target export, bounded step and cooldown. Preview those
   values first using the [adaptive preview](adaptive-policy-preview.md).
4. Use one controller per station. Keep the automation disabled until you can
   validate its consumption response on a noncritical load. No C2000 hardware
   experiment or automatic activation was performed for this implementation.

`initial_state: false` keeps the resulting automation disabled after restart.
Turning arming off or selecting `hold` pauses new decisions; it does not undo
an already saved limit. Restore preferences explicitly if needed.

## Guards and failures

Every command sequence requires fresh native telemetry, mains connected, AC
output enabled, Fast off, Standard mode/no active tariff, valid percentage/watt
bounds, correct entity roles and ownership, a fresh signal, arming and cooldown.
The original model is rejected. Conditions are rechecked after raising reserve.
If the charging limit changes concurrently, the following watt write stops.

The dedicated latch is set before commands and cleared only on success. A
rejection, lost confirmation, stale data, manual hold or conflicting limit during a
sequence leaves it on. Review fresh readings and the automation trace before
clearing it; automatic rollback/retry is not provided. This HA latch is not a
distributed lock against another independent controller.

Synthetic tests render the actual YAML and compare decisions with the pure
preview for C1000 Gen 2, including manual overrides, freshness/ownership,
failure and between-command intervention. C2000/original/other firmware are
explicitly rejected; A1763 disaster activity is checked before and between
commands. This validates the decision contract,
not HA's live automation engine or electrical behavior. Price-driven TOU
execution remains a preview: persistent plan ownership/recovery is unresolved.
