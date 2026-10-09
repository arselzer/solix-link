# Shared charging ownership and HA Repairs

The HA action **`solix_link.charging_policy`** shares one durable owner between
`price` and `surplus`. Only native **C1000 Gen 2 / main 1.1.4.9** qualifies.
Both blueprints start disabled; the action defaults to disarmed preview.
Original C1000 and C2000 remain excluded from automatic execution.

## Choose one policy

- **Price:** all-day Off-Peak, Peak, or empty Standard plan. The existing
  `solix_link.price_policy` action uses the same owner for compatibility.
- **Surplus:** positive grid-export readings in **W** adjust the saved charging
  limit in bounded 100 W steps. Confirm the export sign explicitly. The limit
  stays nonzero; this is not charging pause or a zero-import controller.
- Both require a configured reserve, complete fresh protected settings and
  independently fresh saved-plan readback. The controller does not raise reserve
  or change charge caps, outputs, clock, Fast, or disaster settings.

Use `preview` first, then enable and arm only after physical consumption tests.
The price blueprint is [here](../blueprints/automation/solix_link/price_charging.yaml);
the replacement surplus blueprint is
[here](../blueprints/automation/solix_link/surplus_charging.yaml).
**Recreate automations made from the old surplus helper-latch blueprint:** its
inputs changed, reserve is now configured separately, and ownership lives in HA
storage rather than an input_boolean. Upgrading installs no automation.

## Release, switch and recover

1. Disarm the existing policy and request its matching `release` action.
2. Price release clears only its matching owned plan. Surplus release restores
   only the original saved watt limit, provided every protected setting still
   matches. Fresh confirmation is required before forgetting ownership.
3. Confirm **Charging policy ownership** is `unowned`, then preview the other
   policy. Neither kind can take over, release or reset the other's record.
4. After an uncertain result or manual change, inspect fresh settings and restore
   the appropriate baseline explicitly. Matching `reset` is read-only: surplus
   requires its original watts and an empty Standard plan; price requires an
   empty Standard plan. A failed result is never retried automatically.

HA persists a pending latch before each write. Storage errors or cancellation
block execution; existing corruption evidence cannot become a fresh empty store.
Existing price records remain valid in the same `.storage` key, without replacing
their baseline. The diagnostic exposes owner, confirmed decision, phase and the
last executor's reason, arming, override and signal timestamp. Preview does not
replace executor health. Signal/arming diagnostics are in memory; ownership is
durable. After HA restart, review a retained active setting before resuming.

This coordinates both policy actions in one HA gateway entry. Independent
automations, direct gateway clients and the vendor app do not participate in its
lock. Manual HA controls revoke ownership before writing; other clients' changes
are detected through protected readback, subject to polling limitations. Do not
run another controller on the same station.

## Repairs

HA **Settings → System → Repairs** now reports:

- charging ownership storage failures;
- pending or blocked ownership requiring manual reconciliation;
- unusable or expired signals for an armed executor;
- quarantined observed native energy counters.
- native-meter continuity/storage failures that block Energy statistics
  (prepared 2026-10-09; see [continuity guard](native-energy-meter.md#ha-continuity-guard--prepared-2026-10-09)).

Issues use fixed text and anonymous station labels, not identities, URLs or raw
errors. They deduplicate by integration entry and station. Missing energy data
does not clear a quarantine warning; a validated tracking report can. Repairs
do not reset counters, delete statistics or send station commands. The integration
uses the [official HA Repairs API](https://developers.home-assistant.io/docs/core/platform/repairs/)
with informational recovery instructions and no automatic repair flow.

Stopping HA, stale signals, manual Hold or disabling an automation leaves the
last saved setting in place. The blueprints also start disabled after HA restart.
Ownership/readback establishes settings, not electrical charging behavior.

Browser, terminal F6 and CLI also provide the
[exact controller rules as a read-only simulation](controller-policy-preview.md).
They use supplied ownership; HA's own preview action uses the actual saved owner.
