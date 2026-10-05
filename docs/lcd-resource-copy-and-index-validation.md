# LCD resource copy, index rebuild and validation limits

## Evidence and scope

This extends [pending callbacks and resource destinations](lcd-pending-worker-and-resource-destinations.md)
with **76 synthetic instruction cases and thirteen rejection guards**. Input is
the public A1763 C1000 Gen 2 LCD container **0.1.9.6**, supplied with main
1.1.4.9: 925,696 bytes, SHA-256
`c314816f396d6b6958a6a398476eafa182f7c368802569803b60bd6a4e6dc97d`.
The 214,016-byte application at `08006000` has SHA-256
`f5683c2f7c9a5bdab638123a7a0a547d3d549e513e18fc10918ed979b83459c6`.

Actual selected consumer, copy-loop, index reader, format-size helper,
ownership accounting, final state, callback and isolated busy-wait instructions
execute. Heap, SPI erase/read/write, parameter erase/program, memory clearing,
logs and the
following timer notification are substitutes. SPI writes affect only bounded
host byte arrays; code and parameter pages remain read-only. Real drivers,
MMIO, reset, boot, fatal handlers, rendering and stations do not execute.
These cases bypass ingress/CRC and seed versions and saved parameter words.
They prove selected caller/decoder behavior, not a physically applied update.

## Copy layout and erase scope

The ordinary consumer at `0802a438` selects the catalog destination and capacity
described in the preceding proof. Actual `0802a6a6..0802a6e6` requests one sector
erase per 4,096 bytes of **reserved capacity**. A static remainder branch adds
one erase for unaligned capacities; all recovered catalog entries are aligned.
The tested `ss_hour` catalog capacity is 155,648 bytes: **38
erase requests**, even when the supplied payload contains only its count word.
The erase service itself never executes; the host array models each sector.

After erase, actual instructions release an old runtime index, clear the runtime
record and write this new resource layout through a substituted SPI service:

| Destination offset | Selected contents |
| --- | --- |
| `+00..03` | Payload's leading little-endian u32 frame count |
| `+04..07` | Descriptor version word after byte reversal, stored as a u32 |
| `+08..` | Remaining payload bytes, copied from staging offset plus four |

The consumer allocates a 4,096-byte copy buffer, transfers complete 4,096-byte
chunks and a final remainder, then frees it. Tail-size fixtures **0, 1, 4,095,
4,096, 4,097, 8,192 and 8,193** reproduce all chunk boundaries and exact host
array contents, including the untouched erased suffix. The eight-byte prefix
is a separate write. These are calls to the write wrapper; this proof does not
execute its lower-level page splitting or establish flash persistence.

## Frame index format consumed by `08029b60`

The actual reader fetches the eight-byte resource prefix directly into its
12-byte runtime record. Counts **0, `ffffffff`, and 301** normalize the first
two runtime words to zero. Accepted counts **1..300** allocate count × eight
bytes for the index, before inspecting frame headers.

Each indexed frame begins at the resource destination plus eight, then advances
past a 12-byte header and a computed pixel extent:

| Frame-header offset | Field read by the selected decoder |
| --- | --- |
| `+00`, u8 | Required magic **25 / `19` hex** |
| `+01`, u8 | Format ID passed to actual helper `08017fc4` |
| `+04`, u16 LE | Width |
| `+06`, u16 LE | Height |
| Other header bytes | Not interpreted by this selected index reader |

Each eight-byte index entry stores the frame-header SPI address and computed
pixel extent. It does not read or validate the pixels. This is an observed
indexing format, not a complete vendor image specification or renderer proof.

The actual format helper executes its inline table branch; table bytes are
excluded from the instruction allowlist. All format IDs 0..21 and 255 are
tested. Its selected return values and the caller's extra-byte adjustment are:

| Format IDs | Helper result, raw bits | Additional byte per pixel |
| --- | ---: | ---: |
| 6 | 8 | 0 |
| 7, 11 | 1 | 1 |
| 8, 12 | 2 | 1 |
| 9, 13 | 4 | 1 |
| 10, 14 | 8 | 1 |
| 15, 19 | 24 | 0 |
| 16 | 32 | 1 |
| 17 | 32 | 0 |
| 18 | 16 | 0 |
| 20 | 16 | 1 |
| Other tested IDs | 0 | 0 |

The caller computes:

```text
extent = (ceil(helper_result / 8) + extra_byte) × width × height
next_header = current_header + 12 + extent
```

Multiplication and address arithmetic use 32-bit results. Sub-byte format
results are rounded per pixel by this caller; packing or color meanings are
not inferred. Width/height 2×3 cases cover every admitted helper format, and
a two-frame fixture verifies successive header addresses. A 300-frame fixture
with zero extents verifies the maximum accepted count and index allocation.

## Validation gaps: indexed does not mean complete

The executed reader and caller establish these concrete limits:

- **Bad magic truncates the runtime count.** Invalid first-frame magic yields
  count zero; an invalid second header preserves the first indexed frame.
  The allocation still has the original requested size. The caller's update
  error accumulator remains zero in both tested cases.
- **Zero dimensions and unsupported format IDs are admitted.** They can
  produce a count-one index with extent zero; the selected reader does not
  reject them separately.
- **Pixel extent is not checked against the supplied byte count.** A copied
  12-byte header claiming eighteen pixel bytes is indexed as one frame even
  when those pixels were never supplied. The modeled suffix remains erased.
  The reader is not passed the descriptor's declared length.
- **Large dimensions wrap the computed extent.** Format 16 at 65,535×65,535
  records raw extent **4,294,311,941**, despite the fixture supplying only a
  header. A two-frame variant attempts a following read outside host storage
  and is rejected by the proof's guard. That guard is not firmware validation.

For valid, bad-first, bad-second and truncated-pixel consumer cases, the actual
caller returns **zero**, requests parameter erase/program with prepared marker
word zero, releases its temporary header and clears busy mode. Those flash
services only capture the requests; the input parameter page remains unchanged.
Actual event-11 callback cases with valid/bad magic both disable event 11 and
reach the substituted post-update timer notification `(3, 1)`.

A separate actual bitmap refresh reports the bad-first resource missing, while
partial/count-one cases clear its missing bit. Thus even a present index and
the missing-resource bitmap are insufficient to verify a complete image.
Rendering or electrical behavior has not been tested.

## Failure and ownership boundaries

Actual caller bookkeeping decrements the synthetic used count when freeing the
old index, copy buffer and transfer header. Normal fixtures retain only the
new index, including the declared-size allocation when parsing truncates count.
An independent host sum of live synthetic allocation sizes matches the used
count. Real allocator integrity and concurrent access are outside the proof.

| Failed substituted allocation | Write requests already made | State at fatal-handler boundary |
| --- | ---: | --- |
| 1,024-byte transfer header | 0 | Old index retained; mode 2 |
| 4,096-byte copy buffer | Prefix only | Old index freed; header retained; mode 2 |
| New frame index | Prefix and payload | Old index/buffer freed; header retained; mode 2 |

Each reaches **`08007154`**, where execution stops. No parameter marker-clear
request occurs before that boundary. Static inspection of `08007154..08007176`
identifies a system-reset request through AIRCR at `e000ed0c`, followed by a
loop at `08007174`. The proof records that stub's hash; it does not execute
the stub, reset the controller or establish bootloader recovery. An allocation
failure can therefore reach reset-directed code after destructive requests.

Additional fixtures return raw **17** from the substituted SPI and parameter
services. The selected caller still returns zero, both when writes are applied
to the host array and when all host programming is skipped. The latter leaves
an erased resource and normalizes the runtime count to zero. This proves that
the chosen return values are not inspected on these caller paths; **17 has no
established meaning in the real driver contract**. It is not a physical write
failure test or a claim that real drivers report failures this way.

## Isolated storage busy-wait

Seven cases also execute actual **`08010718..080107f6`**, with transport calls
substituted, RAM clock `20000628` supplied and synthetic SPI channel **255**
skipping real chip-select/MMIO selection. A substituted command sends status
opcode 5; supplied status bytes exercise the busy-bit decision. Clock values
are injected only when actual clock reads occur.

- Busy clear exits normally.
- Elapsed time equal to the supplied deadline still permits a status read;
  elapsed time **greater** than it exits without another read.
- A zero deadline can either read immediately at unchanged time or exit after
  time advances. Parameter `ffffffff` disables the deadline check; the bounded
  fixture supplies ready after three polls.
- Ready and timeout cases both return **255** in this synthetic-channel branch,
  from the channel field rather than a distinguishable success/error outcome.
  The routine also writes raw one to `20000600`; its broader meaning is unproved.

Time units, actual busy durations and real channel-selection branches remain
unverified. Static inspection shows the page-program primitive `0801058e`
preserves the transport-send result and returns it after the busy-wait call,
rather than exposing a distinct wait-timeout result. The sector wrapper uses
raw deadline 60,000; the page primitive uses 3,000. Transport return meanings
and hardware failure behavior still require separate evidence. The synthetic
nonzero-return cases do not establish an error meaning for raw 17.

## Reproduction and next work

```sh
SOLIX_ANALYSIS_OUTPUT=/private/output/lcd-copy \
python3 tools/firmware_analysis/emulate_lcd_resource_copy.py \
  --output-dir /private/output/lcd-copy
```

Compare both `lcd-resource-copy-{results,manifest}.json` artifacts with
`expected_results/`. Python **3.14.4**, Unicorn **2.1.4**, static inspection
with Capstone **5.0.7**. Bound **100,000 instructions per entry**; observed
maximum **22,281** for the 300-frame index; isolated waits have the inherited
10,000-instruction bound. Independently repeated artifacts
match byte-for-byte, including source hashes. Guards reject boot/reset/real
drivers, MMIO, catalog/inactive-record mutation, inline-table execution,
unbounded names and host storage overreads. Disjoint RAM fixtures, unchanged
catalog/parameter bytes, PRIMASK and synthetic heap accounting are checked.
Public artifacts contain synthetic metadata and hashes; raw exploration stays
ignored/private. No product code or live deployment changes.

Next: follow indexed frames into actual renderer consumers and identify the
remaining header fields; trace transport-return semantics and recovery after
the identified reset requests. A pinned bootloader input is still needed for special
LCD code/resource payload installation. Any future transfer API needs independent
payload/extent validation and receiver readback; receipt, zero caller return,
marker clearing and an index are insufficient verification. True charging
pause, complete settings readback and physical energy calibration remain open.
**Zero station commands were sent; C2000 and all live settings are untouched.**
