# C2000 Gen 2 authorized AC recovery

## Hardware observation — 2026-10-14

The user explicitly authorized interrupting both attached servers to recover an
unresponsive machine, and confirmed the SOLIX controller, AP and router had
independent power. This was a recovery operation. The later
[guarded interfaces](ac-output-controls.md) expose the confirmed command only
with per-station owner opt-in; no output automation is enabled.

Tested device: **C2000 Gen 2 / A1783, main 2.1.6.4**. Baseline: native MQTT
connected, battery 100%, Standard mode, no saved tariff slots, mains present,
AC on, DC off, both output countdowns zero and approximately 175–176 W reported
AC input/output. Bluetooth scans found no advertising station.

The private request uses native **0101**, only **A2 = 01 + boolean** plus the
established source **A1=22** and FD millisecond timestamp; response **0901**.
An idempotent ON probe acknowledged and two fresh full-status requests confirmed
AC on and preserved A4/D9 settings. That probe alone did not establish that OFF
or restoring from OFF would work.

The subsequent authorized cycle reported:

1. AC off, 0 W AC input/output, mains present and DC off.
2. A 20-second hold after OFF confirmation.
3. AC on confirmed on the first restoration attempt, initially 0 W while loads
   started; a later fresh report showed approximately 153 W input/output.

The switch remained powered and connected over native MQTT. Both C1000 outputs
remained on; no commands targeted them. Reported A4 settings and D9 data after
its two-byte runtime prefix were preserved; A4[22] runtime LCD activity was
allowed to vary. This does not establish complete hidden-settings restoration,
waveform behavior, calibrated watts or native energy-counter reset semantics.
Server boot/reachability is separate from output restoration.

The previously unresponsive server subsequently answered ICMP and accepted an
SSH TCP connection after approximately three minutes of boot time. No login or
server configuration change was attempted. This demonstrates recovery of that
specific load/network endpoint; it is not an uptime guarantee for other servers.

## Private maintenance interface

The original owner-only AP Unix socket supported **recovery-ac-output** for this exact
model/firmware. It requires `enabled` and `acknowledge_power_interruption` as
explicit booleans. OFF requires acknowledgment, an on/mains-present Standard
baseline, DC off, no tariff slots/countdowns and at least 20% SOC. The helper
performs one write and two fresh confirmations; it never retries an uncertain
OFF. Full fresh state is required for ON too. That temporary maintenance name
has been replaced by the canonical, opt-in `set-ac-output` and `cycle-ac-output`
interfaces. No generic raw-packet API or automatic output policy was added.

The private operator preserves local evidence, attempts ON in `finally` after
any OFF attempt, and allows up to three deliberate ON recovery attempts after
fresh-connection failures. It never resends OFF. Power restoration can still
require physical access after a failed link/guard, controller failure or device
fault; a generic wake packet cannot restart a frozen running computer. The
server's BIOS must support power-on after restored AC, or an appropriate wake
method must be available.

## Deployment and evidence

A temporary AP-module/helper installation, with code rollback copies, enabled
the maintenance operation. The AP/gateway were reloaded; HA, identities,
certificates, station provisioning and charging automation were unchanged.
A startup readiness-check error caused the first installation to revert before
any setting request. The corrected check waited for a post-restart report.
The recovery runner was renamed to avoid shadowing Python's `operator` module
and runs with isolated imports. No output request came from either failed setup.

**75 final focused synthetic tests passed** (72 before the live operation),
covering request fields, pre-write guards,
unknown settings changes, lost ACKs, ignored writes, private RPC validation and
unchanged public capabilities. Raw request evidence, timestamps, private host
addresses and operator events remain in the ignored, restricted recovery folder.
The temporary runtime operation is removed after AC restoration; the normal AP
code was restored and monitoring reloaded at that operation's checkpoint.
The subsequent [guarded interface release](ac-output-controls.md) supersedes
the temporary private route. No phone data or private identities belong in public Git.

## Repeat recovery through the shipped API — 2026-10-14

After the server froze again, the user explicitly requested another restart.
Ping and SSH were unreachable before the operation. The previously confirmed
independent controller/AP/router power arrangement remained applicable.
Source/runtime **`20f9207`**, native A1783 main **2.1.6.4**, reported 100% SOC,
AC on/DC off, mains present, Standard/no tariff slots, zero output timers and
approximately 192 W AC output. The permission initially remained locked.

The operator temporarily enabled the canonical owner permission endpoint, sent
exactly one authenticated, coordinated `cycle-ac-output` request with a
20-second hold and both acknowledgments, then restored the original locked
permission. This exercised the shipped HTTP, AP RPC and worker cycle rather
than the removed private maintenance route.

The retained MQTT window confirms exactly **one OFF and one ON** `0101`
request, each containing only **A1/A2/FD**. Telemetry reported OFF/0 W, then
ON; the API completed with matching readback, recorded audit and no pending
restoration intent. No interruption was retried and the ON restoration needed
no retry. Both C1000 AC outputs remained on, and all compared published
preferences matched their recorded baselines.

The first post-cycle reachability check, approximately **63 seconds after API
completion**, found ping and an SSH greeting. This is an observation bound,
not a measured boot duration; no SSH authentication or OS command occurred.
It establishes a second recovery of the attached server, while electrical
waveforms, other firmware and the cause of the recurring freezes remain unverified.
No HA entity was enabled or automation changed. Private request/response,
MQTT/telemetry windows and boot evidence remain in ignored
`.solix-private/server-research/c2000-recovery-repeat-20261014/`.
