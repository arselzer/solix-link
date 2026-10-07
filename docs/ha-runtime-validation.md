# Three-station Home Assistant runtime validation

Live trial on **2026-10-02** with Home Assistant **2026.7.4**, Ubuntu
**26.04** and Python **3.14.4**. This is physical deployment evidence beyond
the standalone HA contract tests. The gateway package is the verified
`solix_link-0.1.0` wheel; the integration uses the normal authenticated
configuration flow and one shared HTTP coordinator.

## Stations and discovery

| Station | Main firmware | Native controls | Registered / available HA entities |
| --- | --- | ---: | ---: |
| Original C1000, A1761 | 1.7.1 | 9 | 15 / 14 |
| C1000 Gen 2, A1763 | 1.1.4.9 | 13 | 32 / 23 |
| C2000 Gen 2, A1783 | 2.1.6.4 | 5 | 20 / 15 |

These are initial-deployment counts. The later freshness/DC Smart update below
adds entities without replacing the existing integration entry or station profiles.

All three stations simultaneously connected to one isolated **2.4 GHz** AP,
with separate client certificates, telemetry and command queues. Disabled
diagnostics and unavailable derived values account for the remaining entities;
their presence is not a station connection failure. New entities appeared
during ordinary polling, without reloading the integration for discovery.

The C2000 automatically reused its existing network configuration. Only the
two C1000s received Wi-Fi/API/timezone provisioning, through their saved Prime
Bluetooth identities. The original replied `4824=00`, with `4825` timing out;
the Gen 2 replied `4824=00` and `4825=00a10400000000`. Both subsequently
completed local TLS/MQTT activation. A configuration timeout alone is therefore
insufficient to diagnose provisioning failure.

Both C1000 Bluetooth sessions disconnected during activation. Post-activation
checks used fresh native telemetry; a subsequent Gen 2 direct BLE probe could
not find its advertisement and sent **no setting command**. This observation
does not establish a permanent BLE lockout or a universal pairing requirement.

## HA command and restoration

The original C1000's actual HA `select.select_option` service changed its
screen timeout **30 → 60 → 30 seconds**. The gateway confirmed the temporary
value and restoration through native telemetry. All recorded output, charging,
timeout, display and Smart settings across the three stations matched their
baselines afterward. The native setter independently guards the original's
complete F8 and eleven preferences.

All three AC outputs remained enabled in recorded snapshots. No C2000
provisioning, charging or output command was sent in this deployment trial.
These are reported output states, not independent waveform measurements.

## Service and integration recovery

Both dedicated systemd services are enabled at boot:

```sh
systemctl status solix-link-ap.service solix-link-gateway.service
```

Restarting only the HTTP gateway recovered all three available snapshots in
approximately **1.01 seconds after `systemctl` returned**. Restarting the AP
and gateway together recovered all three in approximately **32.07 seconds
after `systemctl` returned**. Neither phase reprovisioned a station or sent a
settings command. All protected settings matched the pre-restart baseline.

Reloading only the SOLIX Link HA integration succeeded. Its entry returned to
`loaded`, with the same device IDs, entity IDs and available-entity counts.
Six additional samples confirmed all three were available and their report
timestamps advanced. These recovery times exclude service-command execution
and are not guaranteed outage durations.

## Passive report continuity

Owner-only copies of the three MQTT logs were decoded locally at **08:40 UTC
on 2026-10-02**, without restarting a service or sending an additional station
command. The latest sessions began during the deliberate AP recovery trial.

| Station | Reports in latest session | Observed session | Median report gap |
| --- | ---: | ---: | ---: |
| C2000 Gen 2 | 615 | 53.50 minutes | 5.235 seconds |
| Original C1000 | 594 | 53.42 minutes | 5.400 seconds |
| C1000 Gen 2 | 609 | 53.23 minutes | 5.234 seconds |

No further TLS connection appeared in those sessions. All decoded protected
settings stayed constant, including enabled AC outputs and inactive countdowns.
The original retained its **720-minute** device timeout; both Gen 2 stations
reported **Never**. This observation does not prove behavior at the original's
12-hour idle boundary.

The C1000 Gen 2's largest gap, **15.069 seconds**, separated its initial status
publication from the first requested status. Request timestamps match the
gateway's intentional 15-second startup grace after subscription; this is not
evidence of a later reconnect. The other maximum gaps were 5.241 seconds
(C2000) and 6.667 seconds (original). MQTT envelope identities and packet
checksums were validated before decoding; no malformed record was encountered.

The HP test switch was disconnected by this point. The original reported
100% SOC and 0 W input/output, so these logs provide no below-full charging-rate
test. Report continuity and logical output states do not measure relay timing,
electrical continuity, long-term availability or durable energy totals.

## Deployment and remaining checks

An allowlisted diagnostics platform was then installed. HA was restarted to
load it, and once more for failed-setup handling. The actual authenticated diagnostics download returned
all three fresh stations and their expected firmware versions. Gateway URL/token
were absent from the complete response. The integration payload omits configured
names, entry/device IDs, serials, account identities and raw captures; it reads
only the coordinator cache. HA adds standard system metadata around that payload.
Twenty-one privacy/staleness tests passed, including unavailable setup without
a coordinator; the full standalone HA suite is now **520 tests**. No gateway
wheel or station configuration changed for diagnostics.

HA runs in a host-network Docker container; its actual configuration mount,
rather than the Compose-file directory, contains the custom component. The
separate privileged AP worker owns a dedicated Wi-Fi adapter in a network
namespace with no LAN interface or default route. The authenticated HTTP
gateway and bundled browser dashboard are reachable from the host LAN.
Unauthenticated station requests return 401; no output-switch API is exposed.

This trial retains existing identities. Generated native identities for the
original C1000/C2000, full host or station power-cycle persistence, long-term
availability, reauthentication/reconfiguration and charging automations remain
separate checks. Original mains/battery-source observations remain unknown
where the firmware provides no validated field. Session charts are not
persistent energy accounting.

Private deployment scripts, baselines, authentication/config backups, native
logs and complete runtime results remain owner-only in ignored local evidence
and the node's restricted runtime directory. Public documentation contains
no account identifiers, station serials, certificates or bearer tokens.

## Freshness, DC Smart and charging blueprint update

A separately built wheel, SHA-256
`1aee184878e4d163d30259afd4d5ad1fa526ce78f9979eb9fc07e0a6d33716ed`,
was installed with no dependency changes. Runtime and component backups were
retained first. One AP/gateway restart recovered all three fresh snapshots
in **22.04 seconds after the recovery check began**, preserving every recorded
output, timer, charging and preference baseline. No station was reprovisioned.

After restarting HA to load the component update:

| Station | Native controls | Registered / available HA entities |
| --- | ---: | ---: |
| Original C1000 | 9 | 16 / 15 |
| C1000 Gen 2 | 14 | 34 / 25 |
| C2000 Gen 2 | 5 | 22 / 17 |

All three enabled **Last telemetry** timestamp sensors were fresh. The C2000's
new read-only **Fast charging enabled** binary sensor reported off. The actual
Gen 2 DC Smart HA switch passed **OFF→ON→OFF**, with native two-report confirmation,
all sampled AC outputs enabled and complete protected-settings restoration.
See [command guards and limits](c1000-gen2-native-dc-smart-validation.md).

The optional [opportunistic charging blueprint](home-assistant-charging-automation.md)
passed the installed HA **2026.7.4** blueprint schema, input substitution and
expanded automation trigger/condition/action validation. It is copied into HA's
blueprint directory, with no automation instance or helper created and no policy
activated. Fifty synthetic cases cover policy gates, hysteresis and failed-write
latching. The complete Python/HA contract suite passed **2,214 tests**, and
**19 browser scenarios** passed against synthetic station data.

No station advertised over BLE during the recovery scan, and the user was away.
The original/C2000 generated-native-ID trial is therefore deferred: an independent
working BLE recovery path must be demonstrated before changing a live identity.
The original remains at its recorded **720-minute** timeout; both Gen 2 stations
retain **Never**. No new charging-rate claim follows from these idle/full-SOC tests.

A bounded **13-hour read-only observation** now samples the authenticated gateway
every 30 seconds. It records report ages, availability and protected-setting
changes in restricted node-local JSONL, with an updated summary. It sends no
commands, performs no recovery and stops after its deadline. The result is
pending; these API samples cannot prove electrical continuity or continuous
availability between samples. Existing full MQTT logs remain retained separately.

## Clock, AC Smart and disabled solar-policy update

The subsequent clock/AC Smart runtime wheel had SHA-256
`df0b19b1d6bc510a9db54b940ed90992f7260727f0d875220cca871bef3db7b3`.
It was installed after retaining code/configuration backups, preserving all
station identities. The [bounded C1000 Gen 2 trial](c1000-gen2-clock-ac-smart-validation.md)
confirmed both saved clock-brightness flags and AC Smart, then restored all
protected settings. Its private AC-output setup/restoration affected only the
noncritical C1000 Gen 2; original/C2000 received status requests only.

The final wheel adds the line-menu entries and corrected browser requirement
text, SHA-256
`806085309f31157d1e892bc74deb57faaf9f9aac2ee48ab20b2156dbdd271a41`.
Only the HTTP gateway needed a second reload for its asset map; the AP sessions
and station profiles were retained. Fresh checks showed all three AC outputs on.

| Station | Native controls | Registered / available HA entities |
| --- | ---: | ---: |
| Original C1000 | 9 | 16 / 15 |
| C1000 Gen 2 | 16 | 37 / 25 |
| C2000 Gen 2 | 5 | 22 / 17 |

Both optional Gen 2 clock selects are registered **disabled by default**.
AC Smart was discovered during the SDK trial's safe AC-off interval and is
unavailable after AC restoration. Live select-service writes remain untested;
the native worker was exercised and HA has forty select contract cases.

The revised charging blueprint passed the installed HA 2026.7.4 schema again.
Normal authenticated HA APIs created two dedicated off helpers and saved a
**disabled C1000 Gen 2 solar charging instance**. No policy was activated or
station charging setting changed. Its candidate export sensor reports W, but
export sign remains unconfirmed. Defaults and activation prerequisites are in
the [charging policy guide](home-assistant-charging-automation.md).

The policy verifies all ten exact roles and native transport on one HA device,
rechecks guards in HA's manual Run path, and recalculates the opportunity after
a reserve action. Eighty-seven actual-template tests cover these conditions.
Final release checks passed **2,362 Python/HA tests** and **20 synthetic browser
scenarios**; regenerated screenshots contain synthetic station data only.
Independent 92-case Gen 2 and 131-case original firmware replays matched both
complete result/manifest pairs. Their limits are documented separately.

The bounded observation remains pending. Deliberate service upgrades and the
AC Smart trial are recorded as interventions, so resulting gaps or setting
changes must not be treated as spontaneous failures. The original remains at
100% SOC/0 W without a test load; no actual charging-rate or newly supported
forced-discharge behavior is claimed. Generated original/C2000 native identities
still require a demonstrated independent recovery path before testing.

## Private AC-countdown runtime update

On 2026-10-02 the existing AP/gateway runtime received the guarded private
C1000 Gen 2 countdown setter, wheel SHA-256
`e8e81f18e13308aab68850e50e0418100bce8523784094cd67c9b1721fa5467a`.
The previous package was backed up; all static station profiles and certificate
hashes were preserved. Only the existing AP/gateway services restarted. All
three stations recovered fresh telemetry and AC enabled within **24.03 seconds**
of the recovery check starting. No HA component/configuration reload was needed.

The [bounded timer trial](c1000-gen2-native-ac-countdown-validation.md) verified
600→594→0 and restoration, with exactly two native timer writes to C1000 Gen 2.
Original/C2000 received status requests only. Final gateway checks remained fresh;
native capability counts stayed **9 / 16 / 5** for original / Gen 2 / C2000.
HA discovery counts remained **16 / 37 / 22** registered entities. The solar
automation was still disabled and both arming/latch helpers off. The release
gate passed **2,402 Python/HA tests**. Browser/component sources did not change.

An immutable observation snapshot covered **253 samples over 2.10 hours** with
zero HTTP request errors. Two samples had empty caches and unavailable stations
4.85 and 16.35 seconds after recorded service upgrades. Those samples do not
establish actual preference changes. The sole nonempty changed-settings sample
was the planned C1000 Gen 2 countdown; subsequent reports returned to baseline.
The 13-hour observation is still running. No completed soak-test or independent
electrical-continuity claim follows from this partial snapshot.

## Read-only native radio query update

On 2026-10-02 the existing AP/gateway runtime received the guarded private
C1000 Gen 2 wireless-state query, wheel SHA-256
`2dffe375c0a486ec7ba63696a15ac8651508a106d582656c9ca388559a3f69de`.
The prior runtime was backed up, and all static profiles/certificates matched
their saved hashes. Only existing AP/gateway services restarted; this upgrade
is recorded in the observation interventions. No HA component/configuration
reload or charging-policy change was performed.

The [one-query trial](c1000-gen2-native-wireless-state-validation.md) returned
BLE application state 0 and Wi-Fi application state 1. Before/after controller
records and three subsequent fleet samples confirmed protected settings and
fresh telemetry with AC enabled. The bounded wire audit found zero setting
writes on every station; original/C2000 received status requests only.

Native capability counts remain **9 / 16 / 5**, and HA registration counts
remain **16 / 37 / 22**, for original / Gen 2 / C2000. The solar automation is
disabled, both arming/latch helpers are off, and the private query is excluded
from HTTP/HA capabilities. The full release gate passed **2,433 Python/HA
tests**; browser/component sources did not change. The ongoing observation
does not establish completed long-duration reliability or physical continuity.

A later immutable snapshot contains **381 samples over 3.17 hours**, with zero
HTTP request errors. Three samples have empty caches near the recorded service
upgrades, including this upgrade at +20.75 seconds. The sole nonempty settings
change remains the planned countdown trial. This snapshot shows no observed
spontaneous settings changes; it is still a partial observation, not the
completed 13-hour run.

## Native RSSI runtime update

On 2026-10-02 the existing runtime received the explicit C1000 Gen 2 native
RSSI query, wheel SHA-256
`79e867b086964173a82f3fce0324b6df8a3623b174d57b1f05b531a2e0a9c9c1`.
The previous package was backed up, static profiles/certificates matched
their saved hashes, and the AP/gateway restart was recorded as an intervention.
No HA component/configuration reload or station provisioning was performed.

The [one-query trial](c1000-gen2-native-rssi-validation.md) returned −42 dBm.
Full protected records and later fleet samples matched; a private wire audit
found one `0022` query and zero setting writes across all three stations.
Original/C2000 received status requests only. HA registrations remain
**16 / 37 / 22** and native capability counts **9 / 16 / 5** for original /
Gen 2 / C2000. The solar policy is disabled and arming/latch helpers are off.
The full gate passed **2,451 Python/HA tests**. Browser/component code did not
change; the ongoing observation is still incomplete.

The latest immutable snapshot contains **461 samples over 3.83 hours**, with
zero HTTP request errors. The same three sampled empty-cache gaps align with
earlier planned upgrades; the sole nonempty settings change is the prior
countdown trial. No additional anomaly was sampled during this RSSI deployment
or query. This does not measure the exact outage duration or complete the
planned 13-hour observation.

## Completed observation and passive diagnostics

The [completed capture review](ha-13-hour-observation.md), dated 2026-10-03,
supersedes the partial progress summaries above. It covers 1,560 samples over
13 hours, one HTTP error and three empty-cache readings per station, all near
recorded runtime upgrades. The planned countdown is the only nonempty
protected-setting change. This is sampled monitoring evidence, not electrical
continuity or whole-host power-cycle validation.

The 2026-10-03 gateway update adds authenticated passive `/diagnostics` and
saved-file `/setup-check`, plus the browser Checks dialog. The terminal and
line interfaces use the same offline setup checker. Deployment retained a
private runtime backup and restarted only the HTTP gateway. The AP worker PID,
saved profile/credential hashes, registered control counts and every protected
baseline setting matched afterward. All three stations were fresh with AC
enabled. GET/HEAD checks required the token; setup checks found three profiles
and no local-file errors. No station command was sent.

Saved BLE pairing remains unchecked without an optional CLI configuration.
The checker continues to mark generated native identities unverified for
original C1000 and C2000; a file check cannot establish identity origin or live
binding. The release passed **2,510 Python/HA tests**, **22 synthetic browser
scenarios**, and both frontend builds. Screenshots use synthetic fixtures.

## Charging preview and persistent history

The next 2026-10-03 update deployed wheel SHA-256
`3eb95e010c35e49a495590f46fad9d57276c3e762c481151cfb15f684ee550f0`.
It adds [read-only policy preview](charging-policy-preview.md) and
[optional private history](persistent-history.md), including saved browser
charts. Only the HTTP gateway restarted; the AP worker PID, saved profile and
credential hashes, control counts **9 / 16 / 5** and protected baseline settings
matched afterward. Three stations remained fresh with AC enabled.

Seven-day SQLite recording is enabled on the node with an owner-only directory
and database. Authenticated GET/HEAD checks returned all three named histories;
missing tokens were rejected. A hypothetical manual-export request returned
proposals through both HTTP and the cached AP CLI with **zero commands sent**.
The original C1000 preview correctly returned `unsupported_model`. These checks
do not validate an actual solar sensor or physical charging rate. HA remained
loaded with three fresh stations, automation disabled and arming/latch helpers
off; no HA configuration, output or charging setting changed.

A separate bounded **48-hour GET-only observation** started after deployment.
Its first two samples had no request errors or protected-setting changes.
It is still running, not a completed reliability trial. Data stays private and
is limited to 64 MiB; the observer makes no station requests or recovery actions.
The release passed **2,751 Python/HA tests**, **24 synthetic browser scenarios**,
both frontend builds and an independent full SDK-record-two artifact comparison.

## Native energy deployment, 2026-10-07

At the user's request, the prepared native-energy package at **`b540fb8`** was
installed on the existing node. The wheel SHA-256 is
`14f42d8210ae9ed2d873133f7b20edc6220a10f92416a5f24d7cc7858c7b4d48`.
All packaged source/assets were compared byte-for-byte with repository input;
installation used the local wheel without dependency or network changes.
Private backups of the previous runtime, component and AP unit were preserved.

The AP worker and HTTP gateway restarted once, preserving existing profile,
credential, certificate, history and control configuration. An AP unit drop-in
adds **`--energy-reports`**, returning point `20001=1` when a station next
requests it. This opt-in affects analytics reporting, not output/charging
configuration. Initial collection succeeded only on C2000; C1000 Gen 2 fetched
the flag later and its first report arrived during a second bounded watch.
No reset, pairing or station restart was used to force refresh.

Actual **Home Assistant 2026.7.4** loaded the updated standalone component.
C2000's first 16 diagnostics were enabled through the normal entity-registry
API. A subsequently installed Configure option, **Enable newly discovered
native energy sensors**, is enabled for the existing gateway. It uses HA's
standard options flow with automatic reload; future entity defaults change,
while existing individual enable/disable choices are preserved. HA restarted
twice to load the component and then the added option; the second component
update did not restart the AP/gateway.

Final cached/runtime verification found:

- **32 registered, enabled and populated native energy sensors**: 16 each on
  C1000 Gen 2 and C2000 Gen 2. Every sensor has kWh units, source
  `device_energy_report`, `units_verified: false` and **no statistics state
  class**. They remain diagnostic counters, not Energy-dashboard lifetime meters.
- All three stations fresh with AC enabled; every protected field present in
  the pre-upgrade baseline still matched. Saved static configuration hashes
  matched after the runtime upgrade. The second HA update left AP/gateway PIDs
  unchanged.
- **Zero station control commands**; no cloud, phone capture, provisioning,
  charging automation activation or identity change. The earlier observation
  services were already inactive and were left untouched.

These are live HA registry/state and cached telemetry checks, not independent
electrical-continuity or kWh calibration measurements. Original C1000 uploads
use an unsupported separate format, investigated in the
[unused-data audit](unused-device-data.md). Raw deployment outputs remain private.

## Observed native AC meter deployment, 2026-10-07

The follow-on [guarded meter](native-energy-meter.md) was installed from a local
wheel with SHA-256
`df874453c0af994ed5d2eb15c8a0fe2d83791e990841189fba9b2d8871cbfae4`.
All 77 packaged source/assets matched repository input. Installation used
`--no-index --no-deps`, retaining dependency versions and private rollback
backups. Existing AP/gateway services and HA restarted to load code. Profiles,
credentials, network/service configuration and history were preserved; all
protected settings and static configuration hashes matched after recovery.

Actual **HA 2026.7.4** discovered and enabled **two new C1000 Gen 2 sensors**
through the existing native-energy option, without changing individual registry
choices. They have numeric kWh states, energy class, `state_class: total`, an
explicit persisted initialization `last_reset`, and no Diagnostic category.
The actual `recorder/list_statistic_ids` API returned both with **`has_sum: true`**
and **`statistics_unit_of_measurement: kWh`**. This confirms live statistics
registration/eligibility. It does not validate a physical energy delta or a
Recorder reset experiment. Existing 32 mode-counter diagnostics remain enabled
without a statistics state class.

The first scheduled C1000 Gen 2 report established its new observed zero
baseline, with meter status `tracking`; no old cumulative value was backfilled.
All three stations were fresh with AC enabled at final verification. No
station control/output/reset command, cloud call, identity change or charging
automation activation was used. Only local monitoring restarted. The user
confirmed they were unavailable for the pending physical button restart;
ordinary remote station restart support remains unverified.

Select the appropriate **Observed Standard AC ... energy estimate** in
Energy → Individual devices. These estimates include bypass and exclude TOU
and backup groups. No dashboard boundary was selected automatically. Raw
requests, meter readings, registry IDs and verification details remain private.

## Reported telemetry deployment, 2026-10-07

The [presentation update](reported-telemetry.md) was built from **79** matching
package source/assets. Wheel SHA-256:
`7b46551b516744d1ba27536ea08c93ee2834b44dfdf3c3043fbe726335625205`.
The gateway/AP and HA restarted to load the package/component, with private
backups and rollback copies of history and energy state. A signal-unit import
was corrected to HA's supported literal `dBm`; only HA restarted for that fix.
The installed component then loaded successfully on **HA 2026.7.4**.

All **18 new entities** are enabled:

| Model | New entities | Populated at final check | Unavailable runtime estimate |
| --- | ---: | ---: | ---: |
| Original C1000, main 1.7.1 | 8 | 7 | 1 |
| C1000 Gen 2, main 1.1.4.9 / module 0.3.3.0 | 3 | 2 | 1 |
| C2000 Gen 2, main 2.1.6.4 | 7 | 6 | 1 |

Reported idle/unknown estimates intentionally remain unavailable. The other
entities have the expected reported-watt, duration or version/signal contract.
C300 presentation is covered synthetically; it is not configured on this HA
node. Firmware and signal are diagnostics; power and duration are ordinary
sensors. Existing **ten Energy-compatible sensors** retained numeric kWh
states and their `total` state class.

A gateway-only service override enables `--wifi-rssi` at five-minute cadence.
One initial explicit C1000 Gen 2 query populated its independently timestamped
signal cache and HA sensor, with complete protected-state confirmation. No
setting writes, output switches, station restarts, cloud requests or queries
on unvalidated models were used. All three stations remained fresh with AC
reported enabled; protected fields and station identity/network-file hashes
matched the baseline. Public package ownership was preserved for the user CLI.
Charging automations and the earlier observation services were left unchanged.

Verification passed **1,219 focused Python/HA tests**, **103 follow-up entity
checks** after the unit correction, Vue type-check/build and **34 synthetic
browser scenarios**, including RSSI expiry. Cached/live presentation does not
establish watt calibration, runtime accuracy, countdown behavior or electrical
continuity. Baselines, HA registry/state output and local logs remain private.
