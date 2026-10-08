# Optional owned surplus charging

The [surplus blueprint](../blueprints/automation/solix_link/surplus_charging.yaml)
now calls `solix_link.charging_policy` with `policy: surplus`. It shares the same
durable per-station owner as price charging. Only native **C1000 Gen 2 main
1.1.4.9** qualifies. It starts disabled and is never activated by installation.

## Prepare

1. Configure the reserve yourself (at least the policy minimum, default 20%).
   Start with an empty Standard plan, Fast off, mains and AC output on, and
   inactive disaster/clock/output countdowns. Original C1000 and C2000 are excluded.
2. Choose a fresh **W** sensor and independently verify positive values mean grid
   export. The blueprint's confirmation checkbox defaults false.
3. Create an arming Toggle initially off and an override Dropdown with `none`,
   `hold`, `charge`. Choose nonzero idle/maximum watts, bounded step, deadband,
   target export, start/stop hysteresis and cooldown. Preview before arming.
4. Enable only after measured consumption tests on noncritical hardware. Use one
   shared policy per station. Release price ownership before selecting surplus.

The target is current saved watts plus measured export minus target export,
rounded down to 100 W steps, clamped to the configured range and maximum change.
Within deadband it holds; without opportunity it steps toward nonzero idle.
Below reserve, or with `charge`, it steps toward maximum. This changes a saved
charging limit, not measured battery power; it does not promise zero import,
true pause or battery discharge. Reserve and caps are preserved.

## Ownership and migration

The integration persists a pending latch before each command and requires fresh
protected readback. Manual changes or uncertainty block further automatic writes.
Disarming requests guarded restoration of the original watt limit; Hold leaves
the current setting. HA outages and stale signals leave saved settings in place.
The blueprint also starts disabled after HA restart.

**Recreate older surplus automations:** helper-latch and per-entity inputs were
replaced. No reserve-raising sequence or separate latch helper remains. An
existing automation is not silently migrated or enabled. Existing read-only
adaptive previews retain their original proposal contract.

See [shared ownership and Repairs](charging-controller-and-repairs.md) for
release/reset, storage errors, diagnostics, external-controller limits and
physical validation requirements. Price execution uses the same owner via the
existing [price action](owned-price-charging.md).
