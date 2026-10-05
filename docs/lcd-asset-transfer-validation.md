# LCD transfer state, staging and resource validation

## Evidence and scope

This continues [LCD completion status](radio-asset-ack-and-lcd-status.md) with
**37 synthetic instruction cases and eight rejection guards**. The input is
public **A1763 C1000 Gen 2 LCD container 0.1.9.6**, supplied with main 1.1.4.9:

- Container: 925,696 bytes, SHA-256
  `c314816f396d6b6958a6a398476eafa182f7c368802569803b60bd6a4e6dc97d`.
- Executed application: 214,016 bytes at container offset 1,024, mapped at
  `08006000`, SHA-256
  `f5683c2f7c9a5bdab638123a7a0a547d3d549e513e18fc10918ed979b83459c6`.

Actual selected MAIN ingress/CRC, transfer state, chunk ordering, page splitting,
byte-sum and first-entry CRC instructions execute. Transport, allocation,
SPI command/address/read/page programming, logs, libc and UI signals are explicit
substitutes. Storage is a **4,096-byte host array** initialized to `ff`.
Synthetic SPI channel 255 skips real channel selection; its control register
is RAM. Real SPI/MMIO, preparation/erase, resource name/commit handling, boot,
interrupt delivery and stations do not execute. This adds no live update API,
charging command or settings exporter and establishes no other-model equivalence.

## Point 0010 starts transfer state

The seven-byte payload is `marker:u8, count:u16-le, argument:u32-le`.
Actual `0802d150 → 0802d5be → 0802d73a` accepts tested markers **0/1** and
counts **1–4,096**. Markers 2/255 produce status 2; counts 0/4,097 produce
status 3. Success returns `10 00 aa ee` at decimal response offsets 10–13.

The accepted path copies all seven bytes into state `+10` hexadecimal, sets
flag/mode to **0/1**, clears staged-byte count at `+4`, and sets last block
at `+8` to `ffffffff`. Marker 0 calls a substituted UI signal `(13, 1)`.
The earlier mode-1 progress code shifts the count left by ten, supporting its
role as a 1,024-byte block count; progress math still does not execute here.

This identifies the earlier MAIN starter's fixed `01 98 00 00 00 00 00`
as marker 1, count **152**, argument zero. It is not a filename or a passive
settings query. [MAIN startup](gen2-asset-transfer-export-limits.md) reads no
pointed-to file contents before producing that descriptor.

## Point 0013 appends staged data

After actual CRC/dispatch, `0802d5fa` obtains the recorded payload length.
`0802d638` compares the incoming two-byte block number with the last block;
the initial `ffffffff` acts as minus one in this selected comparison.
The new-block path `0802dd5a` uses:

```text
destination = 0x00c00000 + staged_byte_count
source      = incoming frame, decimal offset 14
bytes       = recorded payload length - 4
```

It records the incoming block, executes actual page wrapper `08010654`, then
increments staged-byte count. The wrapper splits writes into **256-byte pages**
and calls substituted page driver `0801058e`. A 1,024-byte chunk produces four
page calls; a 257-byte chunk starting after one staged byte splits into 255+2.
The page-call data hashes and complete host-storage hashes are independently
checked against the fixture data.

| Supplied sequence | Executed result |
| --- | --- |
| Initial block 0 | Append data; reply `13 00 aa ee` |
| Block 0 repeated with different data | Same reply, no new page call; original bytes remain |
| Block 0 then 1 | Append the second chunk |
| Block 0 then 2 | Also append at the current byte offset; no gap inserted |
| Block 2 then 1 | Reply `13 00 01 00`; no new write or offset change |
| Altered frame CRC | No response or storage call; state remains unchanged |

The observed ordering rule is not a check for exactly the next block. It does
not establish behavior for all signed/wrapping block values or real transport
retries. Short LCD fixtures demonstrate receiver behavior; MAIN's tested sender
still always forwards 1,024 initialized/padded bytes. A physical flash operation,
its success/error reporting and erase-before-write requirements remain unproved.
Emulated PRIMASK save/disable/restore instructions execute with no interrupts;
the fixtures assert restoration. Timing bookkeeping is inactive in these cases.

## Point 0014 checks staged content

For marker **0**, selected final instructions read each staged byte through a
substituted SPI interface and accumulate a byte sum. Actual comparison
`0802d80e..0802d814` checks it against the descriptor's four-byte argument.
Matching fixtures of length 0/1/257/1,024/2,048 reach resource-header handling.
Mismatch fixtures return `14 00 04 00`, set state flag/mode to **1/0** and
call a substituted UI signal `(13, 0)`.

Marker **1** reaches resource-header handling directly, without the whole-file
sum stage, even for a nonzero supplied argument. This is the marker used by the
earlier MAIN starter. It does not skip the following per-entry CRC validation.

At `0802d818`, actual instructions request a **1,024-byte header allocation**
and call substituted read `080104e2` for staging address `00c00000`. The first
32-byte descriptor begins at header offset **12**:

| Relative descriptor offset | Field observed in instructions |
| --- | --- |
| 0 | Four-byte little-endian payload offset |
| 4 | Four-byte little-endian payload length |
| 8 | Four-byte stored CRC value |
| 12 | Four raw version bytes, reversed for logging |
| 16 | Name bytes; interpretation remains beyond this replay |

Offset `ffffffff` takes the empty-list path. Length zero or greater than the
staged-byte count takes the size-rejection path. For admitted synthetic entries,
actual instructions read `length` bytes at `00c00000 + payload_offset`, compute
CRC-16/MODBUS with initial `ffff` and polynomial `a001`, and compare the value
at `0802d9c0`. Host CRC calculation independently agrees.

Correct CRC fixtures of 1/255/1,024 bytes reach `0802d9c6`; altered CRCs branch
to `0802dd20`. Both stops precede name handling or error cleanup/reply.
The host rejects reads outside its synthetic array; this is **not evidence of
equivalent firmware offset-plus-length validation**. Full descriptor iteration,
name/size policy, cleanup, internal/external commit and recovery remain excluded.

## Public container metadata: host inspection

Separately, host inspection of the pinned public container finds the same
32-byte descriptor layout and verifies both complete payload CRCs:

| Name | Descriptor offset | Payload offset | Bytes | CRC-16 |
| --- | ---: | ---: | ---: | --- |
| `pps_lcd` | 12 | 1,024 | 214,016 | `a038` |
| `pps_lcd_res` | 44 | 215,040 | 710,656 | `4f00` |

The resource payload SHA-256 is
`f61823dde70028254b74a534f88ed249f21136a776acdfd1d0420bc0a20c247c`.
The raw descriptor version bytes are respectively **26/1/29/1** and
**25/10/24/1**; their display/date semantics are not established.
Static name comparisons immediately after the executed boundary reference
`pps_lcd` at `0802e334` and `pps_lcd_res` at `0803978a`.
The full public container is not delivered to the emulated receiver or a station;
these are host checks and static references, not a firmware-installation proof.
MAIN's tested asset consumer caps total size at **163,840 bytes**, below this
925,696-byte container. Shared layout therefore does not establish delivery of
the whole firmware through that particular transfer chain.

Point **0012** remains excluded at `0802d236`. Static instructions issue raw
opcode `d8` across staging addresses `00c00000..00ff0000` in 64-KiB steps,
then reach internal-flash operations through `0802e3e4`. This is a
preparation/erase route and cannot be exposed as a passive settings query.
Real command semantics, hardware selection and flash effects are not replayed.

## Reproduction and next work

```sh
SOLIX_ANALYSIS_OUTPUT=/private/output/lcd-transfer \
python3 tools/firmware_analysis/emulate_lcd_asset_transfer.py \
  --output-dir /private/output/lcd-transfer
```

Both complete `lcd-asset-transfer-{results,manifest}.json` artifacts independently
reproduce, including source hashes. Python **3.14.4**, Unicorn **2.1.4**.
Per-entry bound: **100,000**; observed maximum **75,118** for a 1,024-byte
entry's actual bitwise CRC. Eight guards reject oversized/truncated fixtures,
real page-driver instructions, MMIO, unrelated RAM and retained-context writes.
Code is read-only; all guest writes stay within explicit synthetic regions.
Exploratory disassembly and verification records remain private/ignored.

Next: execute bounded name/size selection after `0802d9c6`, then descriptor
iteration and cleanup with explicit allocator/file/flash substitutes. Establish
exact destination and commit/recovery behavior before considering an update API.
Radio task/header activation, true charging pause, full saved-state readback and
physical energy calibration remain separate open tasks. **Zero station commands
were sent; no live settings, services or deployment changed.**
