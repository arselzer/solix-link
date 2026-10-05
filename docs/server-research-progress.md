# Server research progress — 2026-10-04

Offline continuation on `server/research-20261004`, based on public commit
`1ef1b958071915643f581a7374c4f97c58c31f8c`. This checkpoint prepares local changes
for review; it is not pushed, merged or deployed.

An earlier ordinary Git staging attempt received a read-only error for
`.git/index.lock`. After reviewed escalations became available, approved staging
of the explicit public candidate list succeeded. A complete review patch and
verification manifest are retained in the ignored private research directory;
the final local Git outcome is recorded there. Private research, `AGENTS.md`
and the preexisting `python/build/` artifacts are excluded.

## Findings and implementations

| Investigation | New evidence or implementation | Still unresolved |
| --- | --- | --- |
| [True charging pause / Android loader](android-selector-two-dataflow.md) | Archive callback provenance and full selector-2 token coverage are established. Both static dataflow paths now inventory local copies and indirect stores: the direct context writer is `+128`, followed by pointer-array bookkeeping. | Actual runtime key writer/value at `context+160`, indirect pointer aliases, selected asset filename, plaintext SDK DEX and pause encoder. The retained `ijiami.dat` is type 3; the separate type-2 route is not its established decoder. |
| [Adaptive previews](adaptive-policy-preview.md) | Pure surplus/price proposals now have authenticated cached HTTP, offline/cached CLI and terminal F6 entry points. Reserve, freshness, hysteresis, cooldown and manual overrides remain mandatory. Vue source and two synthetic browser scenarios are prepared. | Physical consumption response, full plan readback and control ownership. No executor or enabled automation; original C1000 excluded. Existing fixed preview and HA blueprint retain their contracts. Browser build/verification is pending. |
| [Energy accounting](gen2-energy-epochs.md) | Actual reset clears 216 accounting bytes without restarting sample phase. All four encoded groups can wrap from `2^32-1` to zero while internal sums increase. Pure helpers identify ambiguous segment boundaries and compare covered AC-power intervals. | Physical units, MCU snapshot timing, real reset/retention epochs and per-model calibration. AC history estimates include bypass loads; they are not battery energy or HA lifetime statistics. |
| [Settings export](gen2-asset-transfer-export-limits.md) | Ordinary status and factory aggregate remain incomplete. Eleven startup cases and thirty reply/timeout cases follow the fixed MAIN requests and supplied locator into an internal radio `003d` request. No saved settings are read; retries, transfer resets and substituted callback outcomes are recorded. | Radio `003d` processing, later chunk events and an independently established passive export route. This side-effecting transfer path is not a read-only backup API. Complete backup/restoration remains unsupported. |
| [Timeline replay](policy-timeline-replay.md) | New `policy-replay` CLI evaluates supplied frames, carries previous-preview cooldown state and exports JSON or a standalone SVG. A public synthetic example includes stale input, reserve recovery and manual hold. | Electrical response is deliberately not modeled. Each frame's watts, SOC and mode must be supplied independently; no proposal fabricates telemetry or confirms execution. |

The counter-reset wrapper `08029880` is now linked to the previously audited
factory selector-0 property **DC**. This identifies a firmware caller, not a
physically verified reset route or calibrated counter unit.

The SDK proof is **static analysis**. The status and counter suites replay
bounded **actual firmware instructions with synthetic state and documented
substitutes**. None establishes physical electrical behavior. An ACK or stored
setting is not evidence of measured charging or discharging.

## Inputs and verification

Exact input SHA-256 values:

| Input | SHA-256 |
| --- | --- |
| C1000 Gen 2 / A1763 main 1.1.4.9, 198,656 bytes | `21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9` |
| Retained Android APK | `27986f94a992f3aac1189be746d310553e99909ed81dfbb1abd5880f71662110` |
| Packed `libexec.so` | `4fd13d12d64e6d451f5b567c173917225c1b1e2deb97abdeca10110b8dc4f379` |
| Original reconstructed image | `b4c434ffec580a371ece4f7ddc2f410f40d49ce73c3c2e09158194e1bd15dd57` |
| Earlier initialized image, hash-checked only | `14c73475f02e708a07d982b65d5fcea65792c8d477ba293ca95187f461e6e06f` |

Environment: Python **3.14.4**, Unicorn **2.1.4**, Capstone **5.0.7**.

- **362 focused Python tests passed**, including authenticated adaptive API,
  timeline replay, existing charging-policy/HA blueprint parity, terminal
  selector/invalidation, energy analysis, AP CLI and cached server behavior.
  One existing real-socket HTTP/SSE test was excluded: the sandbox rejects
  socket creation. It is not counted as passing.
- **240 synthetic status cases** and **13 synthetic reset/wrap cases passed**.
- **Seven synthetic factory aggregate cases passed**; complete saved settings
  and output flags are preserved, while cache/timer side effects are recorded.
- **Eleven synthetic asset-transfer cases passed**: actual startup/reset,
  descriptor/serializer and CRC; saved settings/output preservation, early
  gates, identical frames across input variants and substituted queue results.
- **Thirty additional asset reply/timeout cases passed**, with four negative
  input/instruction guards. Actual instructions serialize the supplied string
  for the radio; a synthetic `sysPara` string is not a settings-file read.
  Application callbacks and queue execution remain substituted/excluded.
- Archive SDK proof: **44 exact instruction checks**, eight interface
  relocations, six import resolutions and eight function span hashes.
  Selector-2 proof: **984 exact checks** including inherited checks, five
  selected relocations and full static body coverage; **zero guest execution**.
  The follow-up adds **43 semantic checks**, two bounded static expression paths
  and four negative shape/instruction cases. Native effects and aliases remain
  unresolved; the inherited 984 checks are not counted again as new checks.
- Four negative archive checks and four negative selector-2 checks rejected
  altered APK/image/record/descriptor/instruction/dispatch inputs.
- Sanitized results and source manifests are in
  `tools/firmware_analysis/expected_results/`. Independent reruns reproduce all
  sixteen JSON artifacts exactly. The latest result hash is
  `9fdf440984b214dedbccd87b4c1b4f75cabc3096c5333a5f9942ec8387df581d`.

The previous **3,007 Python/HA tests and 24 browser scenarios** are baseline
results, not rerun here. Node is unavailable; the changed Vue source has not
been type-checked, rebuilt or browser-tested. Bundled browser assets remain at
the previous implementation. The new terminal screenshot is from an actual
headless Textual run using a synthetic gateway and policy file:
[adaptive terminal preview](images/tui-adaptive-preview.svg).

This sandbox's asyncio worker completion stalled until another local timer
woke the event loop. The focused gate and terminal capture therefore use an
ignored **test-only 10 ms timer adapter**, not a production change; it grants
no socket/network access. Its exact source hash and test invocation are retained
in the private verification manifest. The focused gate selects:

```text
test_adaptive_policy.py test_adaptive_surfaces.py test_policy_replay.py
test_charging_policy.py test_terminal_preview.py test_energy_analysis.py
test_energy_report.py test_server_readonly_features.py test_tui_gateway.py
test_tui.py test_ap_service_cli.py test_server_web.py
```

Run those files with `pytest -q` and
`-k 'not real_http_sse_disconnect_and_server_shutdown_stop_monitor'` in this
environment. The adapter is loaded only for that test process. Raw research
outputs and verification records remain ignored/private. Public artifacts
contain synthetic metadata, addresses and hashes, not phone data, station
identities, credentials, raw vendor code or recovered keys.

## Next research and hardware requirements

1. **Charging pause:** follow a concrete context-construction or indirect
   writer path toward runtime `context+160`. Selector-2's new direct writer
   inventory establishes `+128` bookkeeping, while loaded aliases remain open.
   Actual asset selection and the type-3 container boundary remain unresolved;
   static token coverage does not recover plaintext DEX or a pause command.
2. **Complete readback:** trace a concrete ordinary/radio producer or generic
   file-export route toward `sysPara`. Both MAIN replies and timeout are now
   replayed; the new continuation below also covers radio command `003d` and
   selected chunk/progress events. The real HTTP producer/buffer contract and
   MAIN consumer remain precise boundaries, without controller file export.
   Another status query or side-effecting route cannot justify restoration of
   unreturned fields.
3. **Preview presentation:** type-check/build Vue source and run the two added
   synthetic browser scenarios when a Node environment is available. Rebuild
   packaged assets together with source before offering the new browser choices.
4. **Automation preparation:** expand saved timelines with measured/sanitized
   frames after physical calibration and complete required readback. Keep
   proposals, control ownership and confirmed executed actions distinct.

Physical follow-up needs an original C1000 below full charge with a noncritical
load, and independent metering for charging-rate behavior. Gen 2 adaptive tests
need confirmed export-sign semantics, dense power samples, paired raw reports
and identified event/host/controller timing. Counter retention tests require a
separately authorized noncritical restart. Missing C2000 firmware prevents
assuming A1763 results apply there; do not reset the server-backed C2000.
Account-free provisioning still needs model-specific generated-identity tests
and physical pairing/recovery access for original C1000 and C2000 native MQTT;
the C1000 Gen 2 result must not be generalized to them.

## Read-only Bluetooth access check

The user subsequently authorized **read-only BLE checks on both C1000s**, with
C2000, pairing, resets, output switching and settings writes excluded. Two
discovery attempts, the second explicitly requested, were blocked by
`PermissionError` / `EPERM` at the **D-Bus socket connection**, before discovery
completed. No device was contacted, identified or queried; no private profile
or authentication input was read. Authorization is recorded, but it does not
by itself remove the sandbox restriction. Those two attempts used the ordinary
sandbox, without escalation or a bypass.

After the session enabled reviewed sandbox escalations, a third, approved
eight-second discovery command **completed successfully**. No advertisement
name matching A1761 or A1763 was seen in that window. This establishes local
D-Bus/adapter access for the approved command; it is not a station connection,
settings readback or proof that nonadvertising devices are offline. No station
profile, pairing identity or authentication input was read.

A fourth discovery attempt, also approved, completed a **ten-second** scan
using the library's supported-name classifier and SOLIX service UUID. It saw
neither C1000 and no unclassified advertisement carrying that service UUID.
This still does not establish that the stations are offline or which device
owns an active connection. No station connection or query was possible, and
no saved profile or authentication input was read.

BLE can later confirm connectivity, telemetry and documented saved-setting
readbacks. It cannot by itself establish calibrated energy units, a true pause
command, complete missing-field readback or electrical charging response.
Those require an established protocol route and, for electrical claims,
independent measurements under suitable load/SOC conditions.

**Zero station commands were sent.** BLE activity consisted of the two blocked
discovery attempts and two approved discovery scans. No station connection,
MQTT or vendor cloud access occurred. No live configuration, network
changes, service operations or automation activation occurred. The
48-hour observer and guarded restart check were neither queried nor changed.
Their outcome remains pending separate review; the handoff's optional HA history
sensors and terminal F5/F6 panels remain undeployed by this checkpoint.

## Additional private static leads

Exploratory FDE-bounded register/operand screening of the pinned Android image
retains context-reference and immediate-store disassembly privately. Apparent
direct global-context-cell writes identified in `649bc` and `7ac74` clear the
cell; they do not establish its constructor or the key pointer's origin.
Several immediate `+160` candidates are wide buffer clearing/copy operations;
their runtime destination aliases remain unresolved. This screening is not a
new proof suite or a whole-program absence claim. Follow an independently
established constructor, VM writer or bulk-copy destination toward `context+160`
before attempting the type-3 asset transform. No native code executed and no
SDK seed/key/record data was published.

## Radio download and SDK copy follow-up — 2026-10-04

Prepared on `server/research-20261004` after local checkpoint `df327d2`.
This continuation changes analysis tools, synthetic metadata and documentation
only. Product code, live services, HA, AP, gateway, identities and station
settings remain unchanged. No new BLE scan or station command was sent.

### Asset download direction and limits

The [radio proof](radio-asset-download.md) adds **34 actual-instruction
synthetic cases** and **five negative guards**. Hash-pinned A1763 radio
**0.3.3.0**, supplied with main **1.1.4.9**, admits HTTP/HTTPS prefixes for
internal 003d, prepares task ID 20, builds 003e controller-bound chunks and
083d completion statuses. It rejects the bare `sysPara` fixture. This resolves
another boundary of the MAIN resource path without establishing file export.

The 1,025-byte progress fixture is forwarded as two 1,024-byte chunks, including
initialized synthetic padding. The actual HTTP buffer allocation/padding
contract remains unknown; this is not a demonstrated hardware memory flaw.
Task execution, HTTP/DNS/TLS, ingress/session, controller, transport and ACKs
remain excluded or substituted. An admission status or completion callback is
not a verified physical transfer or electrical behavior.

Radio input SHA-256:
`e291ec115f013953e825cb51b9e457a8731889547ab55b3058640e599e8cfec8`,
**1,482,800 bytes**. No original C1000 or C2000 equivalence is claimed.

### SDK copy provenance

The [bulk-copy proof](android-bulk-copy-context.md) adds **31 exact static
instruction checks**, four function-span hashes and four negative checks;
guest execution remains **zero**. The immediate `+160` store at `dd1ac` copies
32 bytes from a caller-supplied object. One direct caller and its nested chain
use stack destinations. Two callers' incoming aliases remain unresolved.
This does not establish runtime `context+160` initialization, key material,
plaintext DEX or a charging-pause encoder. APK/library/image pins are unchanged.

### Verification and next work

The two new result/manifest pairs independently reproduce exactly. Earlier
sixteen artifacts and the 362-test product gate belong to the prior checkpoint;
they were not rerun for these analysis-only changes. Environment remains Python
**3.14.4**, Unicorn **2.1.4**, Capstone **5.0.7**. Public artifacts contain
synthetic hashes and structural metadata, without raw SDK code, records, seeds,
identities or captured phone data. Raw exploratory disassembly and verification
records stay ignored with restricted permissions. AGENTS.md is unchanged.

Next useful offline boundaries:

1. Trace task ID 20 through `42013a2a → 42012a98` to the HTTP callback producer
   and its buffer/tail contract; then trace MAIN's 003e/083d consumer. Neither
   supplies saved-file readback without independent producer evidence.
2. Follow an established context constructor/protected VM store or a concrete
   incoming alias to `dcd60`/`dcf0c`. The tested bulk copy cannot supply a key
   merely because its destination offset matches 160.
3. Complete the pending Vue build/browser verification when Node is available;
   keep preview source and packaged assets clearly distinguished.

Physical requirements are unchanged: original C1000 below full with a
noncritical load and independent metering; Gen 2 paired power/counter/timing
samples and export-sign confirmation; separately authorized noncritical
restart for retention calibration. The two C1000s were absent from the prior
approved BLE discoveries; readback awaits advertising devices. No person is
being waited on and the observer/live deployment remain untouched.

## HTTP buffers and MAIN chunk consumer — 2026-10-04

This follows local checkpoint **`b746860`** on `server/research-20261004`.
The [new bounded proof](gen2-asset-chunk-buffer-contract.md) adds **43 synthetic
instruction cases and eight negative guards**, using the same A1763 main
**1.1.4.9** and radio **0.3.3.0** image hashes above.

- **19 radio cases:** two task-ID-20 descriptor registrations, fourteen seeded
  Content-Length allocation/read/callback cases and three ACK-callback cases.
  Actual allocation decisions use 2,048 bytes with a 1,024-byte fallback and
  clear once. Tested final default chunks stay within that allocation; after
  reuse, their padding contains bytes from earlier reads. Deliberate positive
  short reads exit without forwarding in this selected branch. HTTP setup,
  real reads, task services, timers, transport and delivery remain substituted
  or excluded. The actual ACK callback, rather than a host-written flag, runs.
- **24 MAIN cases:** a callback-registration prefix, function-10 003e dispatch,
  parsed TLV lookup, chunk staging, MAIN descriptors/serializers and bitwise CRC,
  chunk replies and ACK construction. The consumer stages exactly 1,024 bytes
  and emits a 1,040-byte MAIN frame. A valid reply clears pending, builds 083e
  ACK status 0, and queues point 0014 when offset plus recorded length reaches
  total. Missing replies retry; the fourth resets with a substituted failure
  callback and ACK status 1. Final receiver application/commit remains excluded.

MAIN preserves 415 saved-setting bytes and eight output flags with zero saved
reads; application callbacks/queue execution are substituted. This continues
to support an asset download/forwarding interpretation, without recovering a
saved-file exporter or charging-pause command. The two suites do not run an
end-to-end radio framing/parser or a physical/electrical test. No hardware
memory vulnerability follows from the initialized/reused-tail observation.

Environment remains Python **3.14.4**, Unicorn **2.1.4**. Radio entries are capped
at 10,000 instructions; MAIN at 100,000 for its actual bitwise CRC over 1,038
bytes. The largest observed MAIN entry uses **68,642** instructions. Both new
result/manifest pairs independently reproduce exactly, including source hashes.
Earlier product test gates and proof suites were not rerun for these analysis
changes. Raw disassembly and verification records stay ignored/private; public
artifacts contain synthetic metadata and hashes. AGENTS.md remains unchanged.

Next precise boundaries are full task/header activation of the registered
callbacks, controller point-0014 completion/application, and 083d/083e
framing/parser and request matching. Chunked HTTP mode and other radio chunk
sizes remain separate limits. Runtime SDK `context+160` provenance and a
read-only complete saved-state producer also remain open.

**Zero station commands were sent in this continuation.** No new BLE discovery,
phone/profile access, network/service operation, deployment, push, merge or
automation activation occurred. Hardware requirements and the previous
unavailable C1000 advertisements are unchanged; the observer and live gateway
remain untouched.

## Final asset receipt, completion polling and request matching — 2026-10-04

This follows local checkpoint **`126442f`** on `server/research-20261004`.
The [new proof](gen2-asset-completion-and-matching.md) adds **55 synthetic
instruction cases and ten negative guards**, using public A1763 main
**1.1.4.9**, 198,656 bytes, SHA-256
`21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9`.
Only analysis tools, synthetic metadata and documentation change.

- **25 completion cases:** actual final callback `08017bd0` accepts supplied
  receipt bytes, then queues point 0016 with timeout argument 10. Receipt does
  not invoke application success. Actual `08017b4c` requires completion state
  1, calls a substituted success callback and resets. Final-reply failures
  retry and fail/reset on the fourth; incomplete polls requeue and execute a
  wrapper preparing timer period argument 20,000. Units, timer execution and
  receiver commit remain unproved. The host-delivered three-request sequence
  verifies serialized frames and callback ordering without a running worker.
- **30 matching cases:** actual fifteen-slot registration, function-10 reply
  dispatch/matching, countdown expiry and selected actual 003d response
  callbacks execute. Matching consumes the first active command/function slot;
  callbacks run before clearing. Registration's update branch compares the
  stored command to both incoming command and incoming function. Repeated
  synthetic 003d/0010 registrations therefore occupy two slots. This behavior
  is pinned to the tested image; the real worker and ingress remain excluded.

Both suites preserve 415 saved-setting bytes/eight output flags and read zero
saved-setting bytes. Application callbacks, queues, timer services, libc,
logging and delivery remain substitutes. No complete settings exporter,
charging-pause encoder, account-free ingress or electrical behavior is claimed.
Public artifacts contain synthetic structural metadata and hashes; exploratory
disassembly and verification records remain ignored with private permissions.

Both new complete result/manifest pairs independently reproduce, including
source hashes. Python **3.14.4**, Unicorn **2.1.4**. Entry bounds are 100,000
(completion, maximum 68,642 for the earlier chunk CRC) and 3,000 (matching,
maximum 307). Earlier proof/product/browser gates were not rerun for these
analysis-only additions. AGENTS.md is unchanged.

Next offline work: locate receiver handlers/resource commit for points
0013/0014/0016; connect radio 083e parsing to its ACK callback argument;
follow the full task/header activation boundary; and continue SDK runtime
`context+160` provenance toward the unresolved pause encoder. A saved-state
producer and physical counter/charging calibration remain independent needs.
Original C1000 charging measurements still require battery below full with a
noncritical load and independent metering. Gen 2 energy calibration requires
paired counters/power/timing and a separately authorized retention test.

**Zero station commands were sent.** No new BLE scan, profile/phone access,
network/service operation, deployment, push, merge or automation activation
occurred. No live observer, gateway, AP, HA or station setting was changed.

## Radio receipt ACK and LCD status receiver — 2026-10-05

This follows local checkpoint **`7090023`** on `server/research-20261004`.
The [new proof](radio-asset-ack-and-lcd-status.md) adds **26 synthetic instruction
cases and ten negative guards**, with two independently reproduced complete
result/manifest pairs. Analysis tools, synthetic metadata and documentation
change; product code and the live deployment remain unchanged.

- **15 radio cases:** actual 003e framing/send-object construction, incoming
  validation/RX processing, general ACK helper, selected worker and actual
  asset callback execute. A matching 083e receipt produces callback argument
  zero/ACK flag 1 even for the supplied failure TLV `A1 01 01`. Body values and
  the tested different function do not affect this selected receipt helper.
  Wrong command/nonreply/checksum fixtures retry three sends and produce
  callback argument one/flag 2. Queue, scheduler, transport and session services
  remain substituted; no application success is proved by receipt.
- **11 LCD cases:** the container supplies an ARM application at file offset
  1,024, length 214,016, CRC-16/MODBUS `a038`; its pinned reset stub supports
  mapping at `08006000`. Reset is not executed. Actual MAIN CRC/header checking,
  the matching 0010–0016 dispatch table and selected completion/error branches
  serialize lowercase-main responses. An idle supplied state reports completion
  and 100, without a preceding transfer. Ordinary storage `0802d5fa`, final
  resource handling `0802d3d8` and progress math `0802d652` remain exclusions.

Input pins: radio **0.3.3.0**, 1,482,800 bytes, SHA-256
`e291ec115f013953e825cb51b9e457a8731889547ab55b3058640e599e8cfec8`;
LCD container **0.1.9.6**, 925,696 bytes, SHA-256
`c314816f396d6b6958a6a398476eafa182f7c368802569803b60bd6a4e6dc97d`;
extracted application SHA-256
`f5683c2f7c9a5bdab638123a7a0a547d3d549e513e18fc10918ed979b83459c6`.
These are public A1763 inputs supplied with main 1.1.4.9; no other model or
firmware equivalence is established.

Environment: Python **3.14.4**, Unicorn **2.1.4**, cryptography **50.0.2**.
Instruction bounds: radio 100,000, maximum 16,927; LCD 10,000, maximum 407.
Protected radio state is preserved; LCD writes stay within synthetic scratch
and its two-byte state word. ROM/code are read-only and MMIO writes are rejected.
Raw exploratory disassembly remains ignored/private; public results contain
synthetic structural metadata and hashes. Earlier product/browser/proof gates
were not rerun for these analysis-only additions. AGENTS.md is unchanged.

Next useful offline work is the LCD point-0010 start-state producer, then its
ordinary chunk/final resource paths with bounded storage substitutes and format
validation. Radio full task/header activation and default-size fragmentation
remain separate boundaries. SDK runtime `context+160` provenance, true charging
pause and complete saved-state readback remain open. Physical calibration still
requires original C1000 below full with an independently metered noncritical
load; Gen 2 paired power/counter/timing samples and separate retention testing.

**Zero station commands were sent.** No BLE discovery, private profile/phone
capture read, MQTT/cloud access, network/service change, observer query,
deployment, push, merge or automation activation occurred. C2000 and all live
settings remain untouched.
