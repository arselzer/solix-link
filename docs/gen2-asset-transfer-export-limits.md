# Asset-transfer startup and replies do not establish settings export

## Executed scope

Eleven synthetic cases execute the C1000 Gen 2 / **A1763 main 1.1.4.9** asset
starter that earlier [clock-screen analysis](gen2-timer-plan-investigation.md)
substituted. Image SHA-256:
`21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9`.

Actual instructions run for starter `0802c264`, transfer reset `08029978`,
readiness, internal descriptor builder `0801c504`, serializer `08026558` and
CRC `0800d300`. The bound is 3000 instructions per entry. Timers, queue
insertion, logging, memory clearing and UART delivery are explicit substitutes.
ROM/MMIO are read-only, and instruction writes are limited to synthetic RAM.
No asynchronous worker, file backend, radio, physical transport, firmware boot
or station runs. A separate thirty-case continuation below executes the reply
and registered timeout paths; the startup suite and its results are unchanged.

## Fixed request and side effects

The starter retains the supplied pointer and callback at transfer state
`20002a44`, sets its active byte at `+8`, and stops/restarts software timers.
When needed it registers timer callback `08017add` with period argument
**180000**. No actual timer fires.

Builder `0801c504` queues callback `08021f05`, serializer `08026559`, timeout
argument **600** and descriptor length **16**. The seven-byte payload is fixed:
marker `1`, little-endian point `0098`, and a four-byte zero argument. The actual
serializer emits a **21-byte `MAIN` frame**, with little-endian header words
`0010, 0005, 0009, 0010` and an independently checked CRC-16. These internal
fields do not establish an external BLE/MQTT opcode or generic filename API.

Eight cases vary queue return 0/1, existing timer and two synthetic pointed-to
contents (`clock_asset` and `sysPara`). All produce the same complete frame.
Executed instructions read neither input contents nor any byte of the saved
settings block. The starter forwards the substituted queue return while leaving
transfer-active set to one for both values. Later worker/recovery behavior is
excluded; this is not a physical stuck-transfer claim.

Three further cases reject a null pointer, an already active transfer and
missing readiness before timer or queue operations. All eleven preserve the
complete **415-byte settings block** and protected eight-byte output-state
region. Public outputs contain frame hashes and synthetic metadata, not raw
identities or saved settings.

## Integration consequence

This route has transfer-state and timer side effects and cannot be offered as
a passive saved-settings query. Its startup/first-request path supplies no
complete backup export or justification for probing a station with a filename.
The continuation resolves the initial reply and timeout and follows a radio
request, but does not prove absence of every generic file route.
Keep complete restoration unsupported until an external producer and fresh,
lossless readback of required fields are established.

See [the complete internal sysPara format](gen2-syspara-backup-format.md) and
[factory aggregate limits](gen2-factory-aggregate-export-limits.md). None of
these A1763 findings establishes C2000 equivalence or calibrated energy units.

## Reply and timeout continuation

Thirty further synthetic cases execute actual initial reply `08021f04`, second
reply `08021e30`, registered timer callback `08017adc`, radio descriptor builder
`08030c8c`, resource-locator serializer `080126a8`, and radio reply `0803058c`.
The timer is a **timeout/error handler**, not the pending chunk worker.

```mermaid
flowchart LR
  A[Fixed initial MAIN request] --> B[Initial reply]
  B --> C[Fixed second MAIN request]
  C --> D[Second reply]
  D --> E[Locator sent to radio: internal 003d]
  E --> F[Radio reply or later events]
```

The initial reply accepts a nonzero callback-success argument and bytes at
offsets `10..13` decimal equal to `10 00 aa ee` hex. It then queues the second
descriptor: serializer `080263ed`, callback `08021e31`, timeout argument
**14000**, length **18** and null payload pointer. Its actual serializer sends
a fixed **16-byte MAIN frame**, header words `0010, 0005, 0004, 0012`, with a
checked CRC. Nothing in that frame names `sysPara` or reads saved settings.

The second reply accepts the corresponding `12 00 aa ee` bytes. It forwards
the retained locator pointer into a radio descriptor, then actual serializer
`080126a8` prepares internal function `0010`, command **`003d`**:

| Field | Actual encoded source |
| --- | --- |
| A1 | Length 4, little-endian integer 0 |
| A2 | Length 2, little-endian integer 1024 |
| A3 | Little-endian **16-bit length**, followed by the supplied string |

The A3 format is distinct from the preceding one-byte-length TLVs. Three
synthetic locator/queue-result combinations include the string `sysPara`.
Changing that string changes the **outbound radio payload**; it does not read
the controller's `sysPara` file. No external BLE/MQTT command, supported locator
syntax, remote source, resource format or actual download is established here.

| Executed branch | Observed synthetic result |
| --- | --- |
| Missing, malformed or error MAIN reply | Increments its stage's retry byte and queues the same stage again |
| Fourth failure, initial retry byte 3 | Calls a substituted application callback with failure/reason 2 or 3, clears transfer state and stops timers |
| Retry byte initially 255 | Wraps to zero and retries; this is a synthetic arithmetic edge, not a measured retry loop |
| Timeout with `timeOut` text | Substituted failure callback reason 6, transfer reset and timer stops |
| Timeout with null/other text | Substituted failure callback reason 7, same reset |
| Radio reply status 0 or missing | Queues another internal `003d` descriptor with null pointer and serializer |
| Radio reply status 2 | Leaves transfer active; later events are outside this replay |
| Other tested radio statuses 1/255 | Substituted failure callback reason 1 and reset |

All thirty cases preserve the protected **415 saved bytes and eight output
flags**, and neither actual instructions nor the libc substitutes read saved
settings. **Application callbacks are substituted**: preservation does not
describe their effects in a real transfer. The known clock-screen callback can
commit staged configuration on success or set failure status. Do not expose
this side-effecting path as a read-only settings query.

Queue insertion, timers, logging, bounded libc operations, allocation, application
callbacks and transport delivery are explicit substitutes. The host delivers
synthetic replies and explicitly invokes captured serializers, including a case
with queue return zero; it does not claim a real queue executed that case.
Actual TLV builder instructions execute; host transport receives and checks the
serialized payload without executing the radio framing layer. ROM/MMIO stay
read-only, the per-entry bound remains 3000 instructions, and four negative
guards reject invalid locator/string/instruction inputs. No station runs.

The [radio continuation](radio-asset-download.md) now replays **34 separate
cases** for 003d admission, download setup, 003e chunk construction and 083d
completion events. It admits HTTP/HTTPS prefixes and forwards synthetic
downloaded data toward the controller. The real HTTP worker, callback buffer
contract, MAIN consumer and end-to-end null-payload retry remain unresolved.
An independent read-only producer returning complete controller settings is
still required. This locator path supplies no fresh saved-file export and no
reason to probe a live station with `sysPara`.

The [next continuation](gen2-asset-chunk-buffer-contract.md) adds **43 cases**
for seeded HTTP buffers, task registration, ACK callback, MAIN chunk forwarding
and chunk responses. It resolves the controller's fixed 1,024-byte staging
copy and final-request construction; final application and end-to-end framing
remain excluded. The observed direction remains download and forwarding.

The [completion and matching continuation](gen2-asset-completion-and-matching.md)
adds **55 cases** for final receipt, the following point-0016 completion poll,
and MAIN pending-request registration/matching/expiry. Application success is
reported only after the supplied completion response; receiver commit, radio
ingress and a saved-state producer remain unproved.

## Reproduction

```sh
SOLIX_ANALYSIS_OUTPUT=/private/output/asset-transfer-start \
python3 tools/firmware_analysis/emulate_gen2_asset_transfer_start.py \
  --output-dir /private/output/asset-transfer-start
```

Compare `gen2-asset-transfer-start-{results,manifest}.json` with
`tools/firmware_analysis/expected_results/`. The manifest records exact image
and source hashes, Python **3.14.4** and Unicorn **2.1.4**. Raw disassembly stays
in the ignored private research directory. Zero station commands were sent.

Run the continuation independently:

```sh
SOLIX_ANALYSIS_OUTPUT=/private/output/asset-transfer-replies \
python3 tools/firmware_analysis/emulate_gen2_asset_transfer_replies.py \
  --output-dir /private/output/asset-transfer-replies
```

Both `gen2-asset-transfer-replies-{results,manifest}.json` artifacts reproduce
exactly in a second run. The result contains synthetic cases, hashes, callback
addresses and substituted outcomes, without captured station data or raw frames.
