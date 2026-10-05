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

## LCD descriptor, chunk staging and first-entry validation — 2026-10-05

This follows local checkpoint **`8bbcb90`** on `server/research-20261004`.
The [LCD transfer proof](lcd-asset-transfer-validation.md) adds **37 synthetic
instruction cases and eight negative guards**; its complete result/manifest
pair independently reproduces, including source hashes. Only analysis source,
synthetic metadata and documentation change.

- Actual point-0010 admission accepts markers 0/1 and tested counts 1–4,096,
  copies the seven-byte descriptor, initializes mode 1, byte offset zero and
  previous block `ffffffff`, and serializes `10 00 aa ee`. MAIN's fixed
  `01 98 00 00 00 00 00` is marker 1/count 152/argument zero.
- Actual chunk ordering and page wrapper execute. Duplicate blocks acknowledge
  without rewriting; higher tested blocks append at the byte offset even when
  a block number is skipped; older tested blocks reject. Actual 256-byte page
  splitting is captured at substituted programming calls, with data/storage
  hashes checked against independent fixtures. Real flash behavior is unproved.
- Marker 0 compares the accumulated staged-byte sum with its argument;
  mismatches emit `14 00 04 00` and clear mode. Marker 1 skips this whole-sum
  stage but proceeds to per-entry CRC validation. Actual first-entry bitwise
  CRC matches host CRC-16/MODBUS; altered CRCs branch to rejection. Stops precede
  name handling/error cleanup, descriptor iteration or resource commit.
- Separate host inspection recovers both public container descriptors,
  `pps_lcd`/`pps_lcd_res`, and verifies CRCs `a038`/`4f00`. Application/resource
  payload lengths are 214,016/710,656. This is not receiver replay of that
  container or an installation proof. Descriptor version bytes remain raw.

Input pins: A1763 LCD container **0.1.9.6**, supplied with main **1.1.4.9**,
925,696 bytes, SHA-256
`c314816f396d6b6958a6a398476eafa182f7c368802569803b60bd6a4e6dc97d`;
application SHA-256
`f5683c2f7c9a5bdab638123a7a0a547d3d549e513e18fc10918ed979b83459c6`;
resource payload SHA-256
`f61823dde70028254b74a534f88ed249f21136a776acdfd1d0420bc0a20c247c`.
Python **3.14.4**, Unicorn **2.1.4**. Per-entry bound **100,000**, observed
maximum **75,118**. Synthetic SPI channel 255 skips real channel selection;
its control register is RAM. PRIMASK instructions execute without interrupts
and restoration is asserted. Real SPI/MMIO, preparation/erase, internal-flash
operations, file/resource commit and boot are excluded. The 4,096-byte host
array bounds are not claimed as firmware offset validation.

Public artifacts contain synthetic hashes/structural metadata and public input
descriptors. Raw static exploration stays private/ignored with restricted
permissions. Focused replays, source hashes, syntax/JSON/whitespace and unchanged
AGENTS.md are verified; earlier product/browser gates were not rerun.

Next offline boundary: name/size selection at `0802d9c6`, then descriptor
iteration, cleanup and precise file/flash commit substitutes. Establish recovery
and destination before exposing a transfer API. Radio task/header activation,
SDK runtime `context+160` provenance/true pause and complete saved-state readback
remain open. Original C1000 charging and Gen 2 counter calibration still require
the previously documented independent physical measurements.

**Zero station commands were sent.** No BLE scan, station/profile/phone access,
MQTT/cloud request, network/service change, observer query, deployment, push,
merge or automation activation occurred. C2000 and all live settings are untouched.

## LCD catalog, name policy and pending-marker destination — 2026-10-05

This follows local checkpoint **`405cd0e`** on `server/research-20261004`.
The [resource-selection proof](lcd-resource-selection-and-pending.md) adds
**43 synthetic instruction cases and eight negative guards**. The complete
result/manifest pair independently reproduces, including source hashes. Analysis
source, synthetic metadata and documentation change; product code is unchanged.

- The selected initialized-data helper `08006cb0` recovers 1,520 bytes from
  421 compressed bytes. Independent host reconstruction matches exactly;
  output SHA-256 is
  `cfbbfae6df83687b91c87016bb40622b3e1a41da1698d7f13b888d1f1e909b5f`.
  Reset/startup and the subsequent zero-fill record do not execute.
- Its 14-entry resource catalog is recovered at `2000053c`, and all name
  lookups execute with actual bounded bytewise strcmp. Clock, TOU-transition,
  charging-related and solar/usage asset names are documented as display
  resources; they do not establish additional charging commands or counters.
- Exact `pps_lcd`/`pps_lcd_res` names select limits 237,568/786,432. Their
  boundary fixtures explicitly change a post-CRC header, proving downstream
  policy rather than large-payload validation. Ordinary mapping checks the
  leading u32 in 1..300 and payload length plus four against catalog capacity.
  Unknown names are skipped. Empty/unknown supplied fixtures can reach pending
  marker handling with the renderer refresh substituted.
- Selected multi-entry, CRC/length/count errors and cleanup branches execute.
  Actual caller accounting releases one or two synthetic headers and returns
  the synthetic used count to zero. Real allocator/error recovery is unproved.
- The pending helper reads 20 bytes at `08005000`, prepares first word
  `a5a5a5a5` while preserving four supplied words, then requests erase/program
  services for that address/length. Actual helper instructions execute;
  flash drivers and writes remain excluded. Supplied old words are synthetic
  because the parameter/bootloader region is absent from this application.
  With service substitutes, final receipt `14 00 aa ee` is followed by actual
  completion poll `16 00 00 32`: pending/raw progress 50, not verified commit.

Pins: A1763 LCD container **0.1.9.6**, supplied with main **1.1.4.9**,
925,696 bytes, SHA-256
`c314816f396d6b6958a6a398476eafa182f7c368802569803b60bd6a4e6dc97d`;
application SHA-256
`f5683c2f7c9a5bdab638123a7a0a547d3d549e513e18fc10918ed979b83459c6`.
Python **3.14.4**, Unicorn **2.1.4**. Copy bound 30,000/observed 8,281;
transfer bound 100,000/observed maximum 9,723. Catalog, parameter page and
PRIMASK restoration are checked. SPI channel/control register, heap and
parameter words are synthetic; code is read-only and MMIO writes are rejected.
Raw initialized data/disassembly remain private/ignored with restricted
permissions. Public artifacts contain structural metadata/hashes. Focused
replays, source hashes, syntax/JSON/whitespace and unchanged AGENTS.md are
verified; earlier product/browser gates were not rerun.

Next: trace pending-counter/context readers, UI dispatch and real refresh
`0802a374`, then marker consumption and precise code/resource destinations.
An absent bootloader consumer may require a separate public input. Commit,
recovery, true pause, SDK `context+160` provenance, complete saved-state readback
and physical calibration remain open. The original C1000 still needs charging
measurements below full; Gen 2 energy units need paired power/counter/timing
samples and separately authorized retention testing.

**Zero station commands were sent.** No BLE scan, station/profile/phone read,
MQTT/cloud request, network/service change, observer query, deployment, push,
merge or automation activation occurred. C2000 and live settings are untouched.

## LCD pending callbacks, bitmap and first erase destinations — 2026-10-05

This follows local checkpoint **`5ef8ca8`** on `server/research-20261004`.
The [pending-worker proof](lcd-pending-worker-and-resource-destinations.md) adds
**58 synthetic instruction cases and ten negative guards**. The complete
result/manifest pair independently reproduces, including source hashes.
Only analysis tools, synthetic metadata and documentation change.

- Recovered initialized data identifies the pending fields as event records:
  `200002b8` is event 1's interval, `200002c4` its counter, and `20000400`
  event 11's retained argument. Transitioning to enabled resets the counter;
  repeated activation preserves it, zero interval blocks activation, and a
  queued event is not appended twice. A seeded
  2,999 counter reaches the 3,000 threshold on the next admitted timer scan;
  elapsed-time units are not physically verified.
- Event 1's actual inline callback disables itself and reaches interrupt-disable
  preparation. Static inspection identifies the subsequent system-reset request.
  Neither preparation nor reset executes. Event 11 queues, is actually unlinked
  by the selected main-loop fragment, disables itself and reaches marker consumer
  `0802a438`. Its supplied argument word is preserved, not read in that path.
- Actual `0802a374` only calculates/stores a missing-resource bitmap from the
  runtime table's leading word and pointer. It does not perform the previously
  unresolved resource commit in this replay.
- Actual catalog placement resolves all fourteen SPI destination words into
  `00001000..00821000`; the reserved region ends at `0096e000`, below staging
  `00c00000`. Each named consumer fixture reaches its corresponding first sector
  erase request. Replay stops before the service; physical placement is unproved.
- The marker consumer skips matching versions and unknown names, validates
  leading counts 1..300 and declared length plus four, and reaches parameter
  marker clearing on tested skip/error paths. Zero prepared marker is recorded
  before erase, while input parameter bytes stay unchanged. This is requested
  marker clearing, not successful installation. Consumer fixtures explicitly
  bypass ingress/CRC and seed runtime versions and saved words.
- Public `pps_lcd_res` begins with `190a1801`, matching the compiled word observed
  before an actual caller's substituted log. Its relationship to runtime version
  address `08040000` is documented; bootloader placement remains an inference.

Pins: A1763 LCD container **0.1.9.6**, supplied with main **1.1.4.9**,
925,696 bytes, SHA-256
`c314816f396d6b6958a6a398476eafa182f7c368802569803b60bd6a4e6dc97d`;
application SHA-256
`f5683c2f7c9a5bdab638123a7a0a547d3d549e513e18fc10918ed979b83459c6`;
resource payload SHA-256
`f61823dde70028254b74a534f88ed249f21136a776acdfd1d0420bc0a20c247c`.
Python **3.14.4**, Unicorn **2.1.4**, static Capstone **5.0.7**.
Per-entry bound 10,000/observed maximum **721**. Guards, disjoint fixture regions,
catalog/parameter preservation and PRIMASK restoration are verified. Static
exploration stays private/ignored with restricted permissions. Focused proof
reproduction, source hashes, syntax/JSON/whitespace and unchanged AGENTS.md are
checked; earlier product/browser gates are not rerun.

Next offline targets: ordinary post-erase copy and payload frame/index reader
`08029b60`, reload/error cleanup, then a separately pinned bootloader input for
special payload installation/recovery. True pause/SDK runtime provenance,
complete settings export and energy calibration remain open. Original C1000
charging still needs measurements below full with a noncritical independently
metered load; Gen 2 counters need paired power/counter/time samples and separate
retention-test authorization.

**Zero station commands were sent.** No BLE scan, station/profile/phone read,
MQTT/cloud request, network/service change, observer query, deployment, push,
merge or automation activation occurred. C2000 and live settings are untouched.

## LCD resource copy, index validation and failure paths — 2026-10-05

This follows local checkpoint **`e968455`** on `server/research-20261004`.
The [copy/index proof](lcd-resource-copy-and-index-validation.md) adds **76
synthetic instruction cases and thirteen rejection guards**, with independently
identical result/manifest artifacts. Only analysis tools, sanitized synthetic
metadata and documentation change.

- Actual copy loops erase the reserved catalog capacity through substituted
  services, then write an eight-byte count/version prefix followed by staged
  payload bytes after their leading count. Seven tail-size fixtures span
  0..8,193 bytes and verify exact chunk addresses, lengths and host-array hashes.
  `ss_hour` requests 38 sector erases even for its minimum supplied payload.
- Actual reader `08029b60` builds eight-byte frame-index entries from a 12-byte
  header: magic 25, format byte and little-endian width/height. Actual helper
  `08017fc4` executes its format table. The observed extent formula, additional
  byte adjustment, two-frame addresses and maximum 300-count index are recorded.
- Bad frame magic truncates the loaded count; zero dimensions and unsupported
  formats yield admitted zero extents. A truncated-pixel fixture is indexed
  despite missing eighteen claimed bytes. A large-dimension fixture wraps its
  extent to 4,294,311,941; the next-frame variant hits a host storage guard,
  which is explicitly not firmware bounds checking.
- The caller returns zero and clears busy mode on tested malformed/truncated
  frame paths, after requested marker clearing through substituted flash
  services. Actual valid/bad-magic callbacks both reach the next timer
  notification. A separate bitmap refresh marks a zero-count resource missing,
  but a partial or truncated count-one resource can clear its missing bit.
  Neither successful return nor an index proves complete rendering/install.
- Actual old-index/buffer/header cleanup matches host live-allocation accounting.
  Header, buffer and index allocation failures reach a fatal-handler boundary
  with mode 2 and no marker-clear request; prefix/payload requests may already
  have occurred. Static inspection identifies that boundary as a system-reset
  request and terminal loop. Reset and bootloader recovery remain unexecuted.
- Synthetic service-return 17 fixtures show that selected callers do not inspect
  that return, including when host programming is skipped and the resource stays
  erased. No real-driver error meaning or physical failure is claimed.
- Seven actual isolated status-wait cases use supplied clock/status reads and
  synthetic channel 255. They distinguish ready/deadline branches and prove
  that the deadline expires strictly after the limit. Both return the supplied
  channel value, not a distinguishable timeout result. Static page-program
  inspection identifies a retained transport-send return across the wait;
  real transport meanings and physical time units remain unresolved.

Pins: A1763 LCD container **0.1.9.6**, supplied with main **1.1.4.9**,
925,696 bytes, SHA-256
`c314816f396d6b6958a6a398476eafa182f7c368802569803b60bd6a4e6dc97d`;
application SHA-256
`f5683c2f7c9a5bdab638123a7a0a547d3d549e513e18fc10918ed979b83459c6`.
Python **3.14.4**, Unicorn **2.1.4**, static Capstone **5.0.7**.
Copy bound **100,000**, isolated waits **10,000**, observed maximum **22,281**.
Ingress/CRC is excluded
from this continuation. Heap and SPI storage are synthetic; parameter writes
are captured without changing the seeded read-only page. Guards, independent
reproduction, source hashes, syntax/JSON/whitespace, disjoint fixture regions,
heap/PRIMASK checks and unchanged AGENTS.md are verified. Raw analysis stays
private/ignored with restricted permissions. Earlier product/browser gates
are not rerun because product code is unchanged.

Next: actual renderer/index consumers and remaining header fields; transport
return semantics and recovery after the identified reset requests; pinned bootloader input
for special installation. True pause/SDK runtime provenance, full settings
readback and energy calibration remain independent open items. Original C1000
charging still needs a battery below full and an independently metered noncritical
load. Gen 2 energy units require paired power/counter/time samples and separately
authorized retention testing.

**Zero station commands were sent.** No BLE scan, station/profile/phone read,
MQTT/cloud request, network/service change, observer query, deployment, push,
merge or automation activation occurred. C2000 and live settings are untouched.

## LCD frame streams, header probe and transport returns — 2026-10-05

This follows local checkpoint **`27bb1d9`** on `server/research-20261004`.
The [frame-stream proof](lcd-frame-stream-and-transport-contract.md) adds **105
synthetic instruction cases and fifteen rejection guards**, with independently
identical result/manifest pairs. Only analysis tools, sanitized synthetic
metadata and documentation change.

- Actual driver preparation assigns drive letter L and file open/read/seek/close
  callbacks. Registry insertion is excluded; actual later filesystem dispatch
  uses an explicitly seeded one-node registry. Actual open selects resources
  0..13 and bounded frame numbers after substituted string helpers. Missing
  runtime/index/count, disabled state and frame bounds reject and free the
  allocated handle. Allocation failure stops before the reset-directed handler.
- Actual read limits a stream by the indexed extent and uses a signed available
  comparison. Zero requested length can still reach storage. Seek does not
  clamp and can move before a frame or beyond its end. An out-of-storage read
  hits a host guard, explicitly not firmware bounds checking.
- One case runs the actual prior index builder and transfers its exact synthetic
  storage/index into a separate file guest. The complete eighteen-pixel-byte
  fixture starts at its twelve-byte header and reaches EOF after only six
  further bytes. This establishes sequential callback behavior, not physical
  display corruption or all renderer access patterns.
- Actual image-header probing opens/reads/closes through the callbacks. It
  rejects short headers, accepts twelve-byte headers without reading pixels,
  and ORs flag `0020` into offset two while preserving other supplied words.
  A deliberately stale/corrupt indexed magic is normalized by the probe;
  the ordinary index builder would reject it. Full flag/format meanings remain
  unresolved; deeper row-size consumers are statically identified.
- Actual address encoding supports three-byte mode zero and four-byte mode one,
  both most significant byte first. Early substituted returns do not stop the
  remaining address sends. Tested unsupported modes send none. Actual device
  addressing mode/capacity is not inferred from these supplied contexts.
- Actual polling send/receive instructions with clock/status/data/registers in
  RAM distinguish invalid buffer/length return six and timeout return eleven.
  Deadline equality still polls; raw units remain uncalibrated. Width one floors
  an odd byte length to halfwords; tested width two returns zero without a
  transfer. Timeout leaves a busy byte set and can follow a partial transfer.
- Five nested cases execute actual storage read wrapper, address encoding and
  transport together, with channel 255 excluding physical chip select. Ready,
  invalid/zero-length receive, timeout and partial-receive cases all return the
  supplied register-block pointer rather than the transport result. Physical
  SPI reads, persistence and recovery remain unproved.

Pins: A1763 LCD container **0.1.9.6**, supplied with main **1.1.4.9**,
925,696 bytes, SHA-256
`c314816f396d6b6958a6a398476eafa182f7c368802569803b60bd6a4e6dc97d`;
application SHA-256
`f5683c2f7c9a5bdab638123a7a0a547d3d549e513e18fc10918ed979b83459c6`.
Python **3.14.4**, Unicorn **2.1.4**, static Capstone **5.0.7**.
New slice bound **10,000**, reused index reader **100,000**, observed maximum
**721**. Guards exclude boot/reset bodies, rendering, real MMIO/programming,
runtime/index mutation, unbounded strings, host-storage overreads and missing
clock/status/receive inputs. Focused independent replay, source hashes, syntax,
JSON/whitespace and unchanged AGENTS.md are checked; raw analysis stays
private/ignored with restricted permissions. Product/HA/browser gates are not
rerun because product and live deployment are unchanged.

Next offline targets: actual pixel requests beyond `08017a34`, header flags and
row-size calculations; full-transfer/release wrapper `080158bc` and retained
page-program return. A pinned bootloader remains necessary for special code/
resource installation and recovery. True pause/SDK runtime provenance, complete
settings readback and physical energy calibration remain open. Original C1000
charging still needs a battery below full and an independently metered
noncritical load; Gen 2 counters need paired power/counter/time samples and
separately authorized retention testing. Charging automation remains disabled.

**Zero station commands were sent.** No BLE scan, station/profile/phone read,
MQTT/cloud request, network/service change, observer query, deployment, push,
merge or automation activation occurred. C2000 and live settings are untouched.

## LCD pixel rows, image-size calculations and cleanup — 2026-10-05

This follows local checkpoint **`712d135`** on `server/research-20261004`.
The [pixel/cleanup proof](lcd-pixel-reads-and-transfer-cleanup.md) adds **94
synthetic instruction cases and fifteen rejection guards**, with independently
identical result/manifest artifacts. Only analysis tools, sanitized synthetic
metadata and documentation change.

- Actual selected pixel-acquisition callback `08017604` uses header offset eight
  as the file source stride, adds twelve header bytes, and requests rows through
  actual seek/read dispatch. Decoder/file/buffer state is supplied; allocation
  and whole initialization are excluded. The caller passes no byte-count pointer
  and tests only read status. Short/empty rows can return one and publish an
  unchanged or partly updated row buffer. Exact bytes and preserved suffixes are
  independently checked; no physical display fault is claimed.
- Formats 15..19 and separate color/alpha format 20 reproduce the earlier
  indexed-extent boundary. Host fixtures including twelve header bytes obtain
  complete row counts, without patching or updating a receiver. Partial rows,
  column subregions, padded output, unsupported format and area-end behavior
  are checked. EOF status success differs from the callback's explicit area end.
- Actual size helper `080076d8` uses stride × height, adds half-stride × height
  for format 20, and adds 8/16/64/1,024 for formats 7/8/9/10. Absent/supplied
  stride callbacks, explicit/odd stride and wrap are checked. Its callback
  remains substituted; remaining flag names, offset ten and default callback
  provenance are unresolved. Static buffer/flag branches are recorded separately.
- Actual full-transfer wrapper `080158bc` preserves neither send nor receive
  return through release. Ready, invalid-send and timeout paths return synthetic
  channel 255 or supplied end-callback seven. Neither is assigned a real error
  meaning. With DMA context absent, actual disable helpers leave a timeout's
  channel busy byte set. With supplied contexts pointing only to RAM, actual
  helpers clear both busy bytes and selected SPI/DMA control bits.
- Busy-before-transfer cases exercise raw deadline 3,000 with equality still
  waiting. Receive-busy is a snapshot while send-busy is reread; explicit host
  flag changes distinguish those paths. No actual interrupt, DMA, concurrent
  task or physical recovery runs.

Pins: A1763 LCD container **0.1.9.6**, supplied with main **1.1.4.9**,
925,696 bytes, SHA-256
`c314816f396d6b6958a6a398476eafa182f7c368802569803b60bd6a4e6dc97d`;
application SHA-256
`f5683c2f7c9a5bdab638123a7a0a547d3d549e513e18fc10918ed979b83459c6`.
Python **3.14.4**, Unicorn **2.1.4**, static Capstone **5.0.7**.
Bound **10,000 instructions per entry**, observed maximum **295**. The size
helper uses a host PC boundary before the supplied return sentinel to handle
Unicorn's conditional-POP stopping behavior, without executing sentinel code
or normalizing CPU state. Guards, independent reproduction, source hashes,
syntax/JSON/whitespace and unchanged AGENTS.md are checked. Raw analysis stays
private/ignored with restricted permissions. Product/HA/browser gates are not
rerun because product and live deployment are unchanged.

Next offline targets: bounded page-program return propagation and timeout
reentry; default stride callback provenance and remaining header flags. Special
installation/recovery still needs a pinned bootloader. True charging pause/SDK
runtime provenance, complete settings export and physical energy calibration
remain open. Original C1000 charging requires a battery below full and an
independently metered noncritical load; Gen 2 counters need paired power/counter/
time samples and separately authorized retention testing. Automation remains
disabled, and the separate observation services are left untouched.

**Zero station commands were sent.** No BLE scan, station/profile/phone read,
MQTT/cloud request, network/service change, observer query, deployment, push,
merge or automation activation occurred. C2000 and live settings are untouched.

## Bluetooth visibility and service-only inspection — 2026-10-05

This continuation follows local checkpoint **`b902e79`** on
`server/research-20261004`. The user requested Bluetooth investigation and had
already authorized read-only BLE checks on the two C1000s. Reviewed adapter
access succeeded: **one ten-second discovery and two later ten-second model
scans completed**, with zero A1761/A1763 matches and zero unclassified
SOLIX-service advertisements. No station connection or query was possible.
The user confirmed they were away, so no button press was awaited or recovery
setter attempted. Lack of advertisements does not prove a station is offline.

The new [BLE inspection command](ble-inspection.md) provides bounded discovery
and opt-in GATT inventory without a SOLIX `Session`, saved profiles or pairing
identity. `ble-inspect --model c1000_gen2` discovers only;
`--connect` permits GATT enumeration only for a unique named model match.
It reads no characteristic values, sends no login/subscription/control packets,
excludes C2000/C300, and omits names, addresses and exception text from JSON.
Timeouts, ambiguous targets, cancellation and cleanup failures have explicit
contracts. GATT inventory is **synthetic-tested only** at this checkpoint.

Source inspection establishes a separate connection boundary: current normal
legacy/Prime negotiation emits **4022** timezone/conference fields, and Prime
emits **4027** registration. Prime creates an ID when none is supplied.
This identifies client-generated traffic, not newly verified firmware storage
or electrical behavior; existing monitoring is unchanged. Calling a subsequent
status request read-only does not remove these setup messages. Factory F0 and
state-consuming diagnostic routes remain excluded.

Verification: **116 focused tests passed in 12.28 seconds** on Python **3.14.4**,
Bleak **3.0.2**, pytest **9.1.1**:

```text
python/tests/test_ble_inspection.py python/tests/test_protocol.py
python/tests/test_wifi_rssi.py python/tests/test_wifi_cli.py
```

Client SHA-256:
`c94f774d627191d5313edfdc61793099dad7102c1c4644e3e1596cb0294acd1a`;
protocol SHA-256:
`337a531dd3863fab30c1f1922cdf81a5a04064a4b0c411e826333da2e3cc745d`.
Both remain unchanged. Exact probe-source hashes, sanitized scan timestamps
and verification/review records are retained only in the ignored private
research folder with restricted permissions. No firmware was replayed in this
continuation; previous firmware/electrical results are not counted as new tests.

Next hardware requirement: a visible C1000 advertisement, potentially after a
short IoT-button press, closer proximity or release of an existing connection.
First enumerate GATT without login. Normal telemetry and saved-setting checks
then need a separately established login/authentication boundary consistent
with the existing prohibition on private profiles and pairing/configuration
changes. Physical original-C1000 charging response and Gen 2 counter calibration
still require suitable load/SOC and independent measurements. True pause,
complete saved-state export and disabled charging previews remain open.

**Zero station commands and zero connection attempts.** Three approved scans
were the only live activity. C2000, settings, identities, AP, gateway, HA,
automations and the separate observation services were neither queried nor
changed. No profile/phone capture, cloud/MQTT request, network/service change,
deployment, push or merge occurred. AGENTS.md and preexisting `python/build/`
are preserved. Changes are prepared for a local reviewable commit only.

## Cached MQTT confirmation and implementation inventory — 2026-10-05

This follows local checkpoint **`884e9c5`**. The user authorized the existing
gateway's cached `GET /diagnostics` and an update to the research handoff.
A restricted, ignored handoff addendum retains that permission for future
cached diagnostic reads. It grants no active MQTT query or station write;
the remaining privacy, deployment and output protections continue to apply.

Two authenticated cached GETs at approximately **21:35 UTC**, five seconds
apart, both returned three connected/available stations over **native MQTT**:

| Model | Reported firmware | First / second telemetry age |
| --- | --- | --- |
| Original C1000 / A1761 | 1.7.1 | 3.67 / 3.28 seconds |
| C1000 Gen 2 / A1763 | 1.1.4.9 | 3.23 / 3.00 seconds |
| C2000 Gen 2 / A1783 | 2.1.6.4 | 2.85 / 2.61 seconds |

All reports satisfy the gateway's 30-second native freshness limit. These
observations confirm fresh cached monitoring, not electrical continuity or
current BLE visibility. They explain HA readings despite the earlier absent
advertisements. Source inspection confirms the diagnostics route calls only
existing snapshots; the native monitor reads existing worker status files.
It does not call the worker request/control interface or poll a station.

The request consumed the existing owner-only gateway HTTP token locally,
refused redirects and external proxies, used bounded responses/timeouts and
retained only validated model/transport/version/freshness fields. No token,
identity, profile, HA configuration or phone capture was copied or published.
The observer/restart-check services were neither queried nor changed.

[Implementation gaps](implementation-gaps.md) now distinguishes missing product
features from protocol uncertainty, physical validation and prepared deployment
work. Repository native allowlists have **9 / 16 / 5** command families for
original / C1000 Gen 2 / C2000 Gen 2; live command capabilities were not queried.
Original battery-use control, true pause, complete restoration, calibrated
battery energy, adaptive execution and offline updates remain open. Gen 2 TOU
discharge, grid return, common charging/preferences and C1000 timeout controls
already exist. Account-free original/C2000-native trials remain unverified.

Node and npm are absent. Adaptive browser source still needs a build and
synthetic browser gate; optional HA history and terminal additions remain
undeployed. No package/UI build or tests are rerun for this documentation-only
inventory. The previous **116 focused tests** remain a separate checkpoint.
Local document links, whitespace, source hashes and unchanged AGENTS.md are
checked; sanitized request/verification records stay ignored with modes 700/600.

**Two cached GETs; zero station commands.** No BLE connection/scan, active MQTT
or cloud request, settings/identity change, network/service operation, deployment,
automation activation, push or merge occurred. C2000 outputs and live HA/AP/
gateway configuration remain untouched. Prepared changes are local only.

## Saved-plan, partial-export and adaptive-surplus integration — 2026-10-05

This reviewable batch follows **`b98430b`**. The latest user request authorizes
feature integration and noncritical C1000 experimentation, with C2000 server
power protected. A restricted private handoff addendum records that scope.
This batch sends **zero station commands** and leaves live services untouched.

### Implemented

- Native Gen 2 `tou_plan_readback` carries validated complete hourly slots and
  an independent host timestamp. Missing D9 cannot refresh it; malformed D9
  invalidates it; connection replacement clears it. Freshness requires both
  station and plan ages within -5..30 seconds, excluding the upper bound.
  This is a complete hourly-slot readback, not a complete settings backup.
  Existing command-result `tou_plan` lists remain compatible.
- SDK/gateway clients, HTTP/SSE snapshots and HA parsing preserve detached,
  bounded public plan data. HA Usage mode exposes `saved_tou_plan` and
  `saved_tou_plan_fresh`. Browser and terminal F3 load a fresh saved plan into a
  local draft without station requests; ordinary polling preserves edits.
- Partial preferences export: Python `export_settings()`, CLI `settings-export`,
  cached authenticated GET `/devices/{name}/settings-export` and browser JSON
  download. Model/firmware and known validated preferences are retained;
  names, identities, credentials, raw bytes and output states are omitted.
  `complete`, `restore_supported` and `field_freshness_verified` are false.
  Unknown/invalid values are omitted, not converted into defaults.
- Opt-in HA surplus blueprint: bounded nonzero charging steps, target export,
  deadband, hysteresis, reserve protection, cooldown, explicit manual hold/charge,
  entity ownership/role checks and a failure latch. It rechecks conditions
  between commands and rejects a concurrent charging-limit change. The existing
  fixed blueprint and preview contracts retain their behavior.
- Firmware-backed disaster preparation can override normal charging bounds.
  The new read-only A1763 HA diagnostic provides a confirmed inactive guard.
  The first adaptive executor therefore accepts **C1000 Gen 2 / 1.1.4.9 only**;
  active/unknown disaster state, original/C2000 models and other firmware block
  it. No C2000 disaster semantics are inferred from A1763. Automation starts
  disabled and has not been installed or enabled.
- Adaptive Vue previews are now type-checked, compiled and browser-verified.
  The packaged bundle and synthetic screenshots are refreshed; runtime needs
  no Node/CDN. Public docs describe interfaces, evidence and remaining limits:
  [export/readback](settings-export-and-plan-readback.md),
  [surplus blueprint](home-assistant-surplus-charging.md).

### Verification and versions

Final focused gate: **565 Python/HA tests passed in 23.95 seconds**. Selected:
`test_plan_readback.py`, `test_settings_export.py`, `test_mqtt_startup.py`,
`test_tou.py`, `test_gateway_client.py`, `test_server_readonly_features.py`,
`test_adaptive_policy.py`, `test_tui.py`, `test_tui_gateway.py`,
`test_plan_readback_contract.py`, `test_surplus_blueprint.py`,
`test_charging_blueprint.py`, `test_gateway_contract.py`,
`test_freshness_entities.py`, `test_diagnostics_privacy.py`.
Socket/terminal regressions ran with reviewed local test access; no timer
adapter or production scheduling changes were needed. Restricted runs first
identified denied synthetic Unix/loopback sockets, not station failures.

`npm run build:dashboard` passed TypeScript/Vue checks and Vite compilation.
**28 browser scenarios passed** against a temporary loopback-only synthetic
gateway, including read-only plan loading, download sanitization, independent
expiry, malformed plans, draft preservation, authentication and existing
controls/previews. Browser errors and external request lists were empty.
Screenshots contain synthetic data only. These do not run HA's live automation
engine or establish electrical behavior. The previous 3,007/24 broad baseline
is not counted again or claimed rerun.

Analysis/test runtime: Python **3.14.4**, pytest **9.1.1**, Bleak **3.0.2**,
FastAPI **0.142.2**, Textual **8.2.8**. Development-only Node **22.20.0** and
Playwright **1.63.0** are private workspace installs, not system/service changes.
The official Node archive was verified against its published SHA-256 before
extraction. Exact source/bundle hashes and invocation metadata remain in the
ignored verification manifest; no development binaries enter Git.

One authenticated cached `GET /devices` at **22:06:37 UTC** recognized:

| Model | Reported firmware | Telemetry age | Exported preferences |
| --- | --- | ---: | ---: |
| Original C1000 | 1.7.1 | 3.356 s | 9 |
| C1000 Gen 2 | 1.1.4.9 | 2.994 s | 15 |

The token remained in memory; redirects/proxies were disabled and the response
was bounded. Only sanitized C1000 exports were saved privately, without station
names or identities. The deployed worker did not provide the new plan field;
that check validates partial export against cached telemetry, not live saved-
plan readback. C2000 was not controlled. No new firmware instruction replay,
BLE connection, cloud request, identity/provisioning change or physical test
occurred. Existing hash-pinned A1763 firmware evidence is reused with its scope.

### Remaining requirements

Review/deploy worker, gateway bundle and HA component together before checking
live plan readback; updating HA alone cannot create the worker field. Deployment
and automation activation are separate steps, neither performed here. The
separate observer/restart-check services were neither queried nor changed.

Before enabling surplus, confirm export sign/units and measure saved-limit
versus actual charging response on a noncritical C1000 Gen 2. Original charging
rate requires a below-full battery, test load and independent meter. Account-
free identity replacement needs physical recovery access. True pause encoding,
calibrated battery energy, hidden settings/full restoration, price-driven TOU
ownership/recovery and qualified offline OTA remain research boundaries.
C2000 adaptive execution needs its own established charging-override readback.

AGENTS.md is unchanged; preexisting `python/build/` is preserved. Private inputs,
phone data, identities, credentials and raw logs stay ignored. No live HA/AP/
gateway/network change, automation activation, push or merge occurred. Changes
are prepared for a local commit only.

## 2026-10-05 — UPS observations, preference comparison and HTTP scopes

Prepared the user's items 1–3 on `server/research-20261004`, based on
`69c574f0fd73938b5f861582abaf9d1d2d6ed4fb`. This batch changes source, tests,
documentation and bundled UI assets only. It makes zero station requests or
commands and does not read station profiles, HA configuration, phone captures
or credentials. Existing live services and the separate observer remain untouched.

### Implemented

- Cached UPS event tracking: explicit mains flags, distinct-report/five-second
  debounce, separate telemetry loss/recovery, low-reserve hysteresis, startup
  baselines and unknown outage timing after gaps. Invalid/regressing reports
  cannot establish power loss; clock/model/transport changes reset baselines.
- Bounded activity in session memory by default; optional private SQLite
  persistence with seven-day/10,000-record limits. Queries expose bounded
  station-scoped cursors. Persistence rejects unsafe files and concurrent writers.
- Sanitized partial-settings comparison in Python, API, offline CLI and browser.
  Missing/new fields and incompatible models are explicit. TOU equality excludes
  reporting timestamps; incomplete exports still cannot restore a station.
- HTTP command start/result correlation, sanitized parameters/errors, cached
  readback matches and setting differences. No completion/ACK/value equality is
  labeled physical verification. Pre-execution audit failure blocks the command;
  post-execution failure preserves the result and marks the missing audit.
- Separate owner-file tokens with explicit device read scopes and command write
  scopes. All JSON, diagnostics, health, history, metrics and SSE routes filter
  reads. Partial readers cannot run whole-profile setup checks. Scoped startup
  replaces the legacy environment token; existing single-token startup is retained.
- HA telemetry-availability/reserve diagnostics and a notification-only alert
  blueprint, initially disabled. The communication entity remains available/Off
  during coordinator failure. Reserve/mains become unknown with stale telemetry.
  The blueprint checks entity roles/device ownership and sends no station commands.
- Browser Activity panel with command results, UPS/settings filters and an
  in-memory partial baseline. Compiled assets and synthetic screenshots updated.

Public contracts, privacy rules and examples:
[UPS activity and permissions](ups-activity-and-permissions.md).

### C2000 HA configuration boundary

Native C2000 already has charging watts, charge cap, backup reserve, hourly TOU
and return-to-grid support; it is not categorically read-only in HA. HA requires
a configured token, gateway-advertised command and required telemetry. Missing
common charging numbers indicate those prerequisites need inspection, not that
the source lacks C2000 charging controls. No live configuration was inspected here.

The native allowlists remain **16 families for C1000 Gen 2 versus five for
C2000 Gen 2**. A1763 main1.1.4.9 preference encoders/readback/preservation evidence
is not transferable to A1783 main2.1.6.4. C2000 general preferences and automatic
charging-override readback remain unestablished. Smart/timeout can stop outputs
indirectly, so no new C2000 preferences or output-changing controls were added.
The adaptive surplus executor retains its C1000 Gen 2/1.1.4.9 qualification.

### Focused verification

**679 Python/HA tests passed in 5.64 seconds**. Selected:
`test_activity.py`, `test_access_activity_server.py`, `test_gateway.py`,
`test_server_web.py`, `test_server_readonly_features.py`, `test_settings_export.py`,
`test_gateway_client.py`, `test_gateway_diagnostics.py`, `test_history.py`,
`test_history_summary.py`, `test_ap_service_cli.py`, `test_freshness_entities.py`,
`test_gateway_contract.py`, `test_history_contract.py`,
`test_ups_alerts_blueprint.py`, `test_surplus_blueprint.py`,
`test_charging_blueprint.py`. They cover scope leaks including concurrent tokens
and SSE updates, audit failure before/after execution, privacy/retention/restart,
stale/unknown states, read-only comparison and existing gateway/HA contracts.
Two old direct SSE tests now supply the middleware-established request principal;
the diagnostics fixture now declares its synthetic station in the configured
scope. Existing model-specific Fast discovery assertions are retained alongside
the new telemetry entity. Initial failures were fixture expectations, not live
device tests, and are resolved in the final gate.

`npm run build:dashboard` passed Vue/TypeScript checks and Vite compilation.
**29 browser scenarios passed** on a temporary loopback-only synthetic gateway.
The new activity/comparison scenario asserts the command POST count does not
increase, settings are model-local and baselines are discarded on station changes.
Browser errors/external request lists were empty. Screenshots contain synthetic
data only. These tests do not execute HA's real automation engine or verify
electrical behavior. Older broad counts are not claimed rerun.

Runtime: Python **3.14.4**, pytest **9.1.1**, FastAPI **0.142.2**, Bleak **3.0.2**,
Textual **8.2.8**, Node **22.20.0**, Playwright **1.63.0**, Vite **8.0.3**,
Vue **3.5.32** and TypeScript **5.9.3**. Source/bundle SHA-256 hashes, exact test
selection and sanitized verification metadata are retained in the ignored
`.solix-private/server-research/ups-features-20261005/` folder. No new firmware
replay or physical experiment occurred; existing versioned firmware evidence
is cited with its original scope.

### Deployment and hardware requirements

These changes are locally prepared and not deployed, pushed, merged or activated.
Deploy gateway assets/source and HA component deliberately before checking the
new entities. Configure separate private tokens; keep C2000 writes absent from
monitor-only clients. The alert blueprint needs a dedicated helper and explicit
enablement. Original C1000 has no established mains flag and cannot provide that
alert input; communication/reserve observations remain separate.

A controlled C1000-only mains/telemetry/reserve trial can validate the new event
timing after deployment. C2000 preference parity still needs its actual controller
firmware and safe model-specific readback, with AC-output preservation established
before exposing any candidate. Original charging rate, true pause, calibrated
battery energy, full restoration, account-free identity replacement and qualified
local OTA retain the previously documented physical/recovery requirements.
AGENTS.md and the preexisting untracked `python/build/` remain unchanged.
