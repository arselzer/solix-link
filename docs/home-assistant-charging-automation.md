# Optional Home Assistant charging policy

The [opportunistic charging blueprint](../blueprints/automation/solix_link/opportunistic_charging.yaml)
uses the existing local SOLIX Link integration for **C1000 Gen 2** and
**C2000 Gen 2**. It has not been activated on the development HA node.
The original C1000 is excluded from this policy.

For bounded surplus-following steps instead of two fixed watt levels, use the
separate [adaptive surplus blueprint](home-assistant-surplus-charging.md).
It is prepared locally and starts disabled; this fixed blueprint is unchanged.

## Prepared development-node draft

On 2026-10-02, actual HA **2026.7.4** validated and saved a C1000 Gen 2
solar-export instance through its authenticated configuration API. The automation
and two dedicated helpers are **off**. Creating it changed no station setting.
The selected export candidate reports W; its positive-export meaning still
needs confirmation before activation. No price sensor was selected.

The draft uses export start/stop **600/300 W**, opportunity/other-times saved
charging **1000/300 W**, minimum reserve **20%** and cooldown **180 seconds**.
These are reviewable starting values, not verified surplus-following behavior.
Activation still requires reviewing the sensor, thresholds and station load.
The installed instance resolves all ten exact station roles on one native-MQTT
device. HA device/entity IDs and the full draft remain private.

## What it changes

The blueprint selects a higher **saved charging-power limit** during cheap
electricity, positive solar export, or either opportunity. At other times it
requests a lower, nonzero limit. It keeps the current charge cap and raises
the backup reserve only when below your configured minimum. Below the actual
reserve, it requests the higher limit even when the price is unfavorable.

| Setting | C1000 Gen 2 | C2000 Gen 2 |
|---|---:|---:|
| Saved charging-power range | 100–1200 W | 300–1800 W |
| Power step | 100 W | 100 W |
| Backup-reserve step | 5 percentage points | 5 percentage points |

The station continues charging at the lower limit when its own policy allows.
This does not pause charging, set total AC input, guarantee zero grid import,
or select battery operation. Bypass can supply an AC load independently of the
charging setting. Actual charging also depends on SOC, temperature and device
policy; see the [lower-power measurements](c1000-charging-and-reserve-validation.md).

The blueprint leaves Standard mode, the charge cap, TOU plans, Fast and all
output settings unchanged. It requires Fast already off and rejects any active
tariff or non-Standard mode. Other automation owners should not change the same
station while this policy is armed.

## Install without enabling

1. Install the current SOLIX Link integration with **Last telemetry** and the
   C2000 read-only **Fast charging enabled** sensor.
2. Copy the blueprint to
   `<HA config>/blueprints/automation/solix_link/opportunistic_charging.yaml`.
   In HA, create an automation using that blueprint.
3. Create two distinct Toggle helpers: **Policy enabled**, initially off, and
   **Command latch**, initially off. Use a separate pair for every station.
4. Select the station and its own entities: Last telemetry, mains, AC output,
   Fast, battery, charging power, reserve, cap, usage mode and active tariff.
   Fast is the existing switch on C1000 Gen 2 and the read-only sensor on C2000.
5. Choose the opportunity source and thresholds, recorded charging limits and
   minimum reserve. Keep the resulting automation disabled while reviewing it.

The blueprint's `initial_state: false` keeps the automation disabled at startup,
including after an HA restart. Explicitly enable the automation and turn its
Policy enabled helper on when ready. It evaluates once per minute and on arming.
Turning the helper or automation off sends no station command: the last saved
power limit and reserve remain on the station.

HA's [blueprint tutorial](https://www.home-assistant.io/docs/blueprint/tutorial/)
and [selector reference](https://www.home-assistant.io/docs/blueprint/selectors/)
describe the import/create flow and entity selection.

## Signals, freshness and hysteresis

Price thresholds use the selected sensor's **own units**, without currency or
energy conversion. For example, EUR/kWh thresholds must be expressed in EUR/kWh;
a cents/kWh sensor needs correspondingly different values. Start is below Stop.

Solar input must report **W**, with positive meaning export. A kW/kWh sensor is
rejected. Convert an import-positive grid sensor explicitly in HA before
selecting it. Export Start is above Export Stop. The sensor measures current
export; the blueprint does not predict export after increasing charging power.
Hysteresis and a configurable cooldown reduce repeated changes. The current
saved power, compared with the lower limit, selects the start/stop threshold.

Every selected station entity must belong to the selected Gen 2 device. Mains
and AC output must be on. The current integration also checks each entity's
exact `solix_link_role` and `solix_link_protocol: native_mqtt` attributes.
An unrelated off switch cannot replace the Fast flag, even on the same device.
An older integration without these attributes fails closed; update and reload
the integration before selecting entities.

The actual **Last telemetry** timestamp must be no
more than 30 seconds old or five seconds in the future. The guard runs again
before each station-setting action.

Station freshness uses the gateway's report timestamp. HA `last_updated` can
stay old for an unchanged value, while `last_reported` records a write to HA;
neither substitutes for the station report time. HA documents these differences
in its [state-object reference](https://www.home-assistant.io/docs/configuration/state_object/).
External price/export signals separately need numeric values and recent HA
reports within your configured age limits. That check does not validate their
upstream data source. In `either` mode, both selected signals must be valid and
fresh. An outage or stale input suppresses station writes, including emergency
reserve recovery.

The charging decision is recalculated from the current signals after a reserve
command finishes, immediately before the power command. A price/export change
during that command therefore uses the new opportunity state. The minimum
reserve and emergency SOC rule still apply. HA's manual **Run actions** path
repeats arming, role, freshness, signal, bounds and cooldown checks inside the
action sequence; skipping automation-level conditions does not bypass them.

## Failure review

Before changing a station setting, the automation turns **Command latch** on.
It turns it off only after the complete command sequence returns successfully.
A failed command, changed guard, timeout or interrupted sequence leaves it on;
subsequent evaluations cannot send another station command.

Keep Policy enabled off while reviewing a latched sequence. Inspect the HA
automation trace and fresh station telemetry, compare the reserve and saved
charging power with your intended values, and confirm the station is connected.
Clear Command latch manually only after resolving the uncertain result. Then
re-enable the policy. A timeout does not prove the station ignored the setting.
No automatic rollback or retry is added by this blueprint; existing gateway
guards and confirmation remain authoritative.

Use one policy per station and its own helper pair. `mode: single` serializes
runs of that automation, and its latch blocks later runs after an uncertain
result. A Toggle helper is not an atomic lock between different automations;
duplicating the policy or running another charging owner can still race.

## Persistent energy history

The existing HA power sensors use watts and measurement state classes. The
optional gateway [SQLite history](persistent-history.md) now provides derived
AC energy with explicit outage/restart gaps and per-channel coverage. It is
enabled separately with `--history-file` and does not activate this blueprint.
Its totals are not automatically exposed as HA Energy statistics.

HA's **Integral helper** is another option. Use separate helpers for
AC input and AC output and select kWh output. Input energy includes bypass
loads; it is not the battery's stored energy. Output energy represents AC load
delivery. Their difference does not establish battery energy or conversion
efficiency when other inputs, outputs and losses are present.

The gateway already makes stale power entities unavailable. Before adding an
Integral result to the Energy dashboard, verify the target HA release's
unavailable/restart behavior with a known constant load and a telemetry gap:
it must not silently count the last valid watts through an unknown interval.
This audit did not execute Integral or Recorder, and no helpers were configured.
Keep the integration's reported source values and mark these totals as derived
estimates. Firmware `*_energy_raw` values remain excluded because physical
scaling, reset epochs and cross-model behavior are not fully established.

The [read-only policy preview](charging-policy-preview.md) lets you check manual
price/export inputs against cached native Gen 2 status through CLI, browser or
HTTP. It uses the blueprint's decision rules but cannot verify HA helper roles,
sensor sign or exclusive ownership, and sends no settings or helper changes.

## Battery use is a separate explicit action

This charge-only blueprint does not create a discharge automation. The existing
`solix_link.set_tou_plan` action can deliberately activate an all-day Peak plan
on a supported native Gen 2 station, preserving enabled AC output:

```yaml
action: solix_link.set_tou_plan
data:
  device_id: YOUR_SELECTED_SOLIX_HA_DEVICE_ID
  enabled: true
  periods:
    - tariff: peak
      start_hour: 0
      end_hour: 24
```

Use the station's **Return to grid** button to clear that policy and confirm
grid supply. A positive AC load is needed for flow confirmation; a mode label
alone is insufficient. The station keeps an activated plan if HA or the gateway
stops. Reserve-at-SOC and setting restoration have live evidence; actual descent
to the reserve floor and timed transitions still need validation. See
[TOU behavior and persistence](gateway-home-assistant.md#persistent-settings).
Keep discharge decisions separate from this blueprint until that persistent
behavior and the load's required emergency reserve are accounted for.

## Offline verification

```sh
python3 -m pip install pytest aiohttp PyYAML Jinja2
python3 -m pytest home_assistant_tests/test_charging_blueprint.py -q
```

Synthetic tests render the actual blueprint templates and validate requested
commands against the current HA API contract. They cover freshness, outages,
Fast, exact entity roles/native transport, wrong devices/models/units, reserve
preservation, hysteresis, cooldown, manual Run actions, decisions recomputed
after reserve commands, mid-sequence changes and latched failures. These tests do not execute the HA
automation engine, a device, or physical charging. Run HA blueprint/schema
validation for the target release before activation. On the development node,
HA **2026.7.4** accepted the actual blueprint schema, input substitution and
expanded automation trigger/condition/action configuration. Fifty synthetic
policy tests passed for the earlier version. The revised role/race/Run guards
pass **87 synthetic policy cases** and require renewed HA schema validation
before deployment. The blueprint file is installed there; no automation
instance or charging policy has been activated.
