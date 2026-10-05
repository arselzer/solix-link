# LCD pending callbacks and resource destinations

## Evidence and limits

This follows [resource selection and the pending marker](lcd-resource-selection-and-pending.md).
The new bounded proof executes **58 synthetic instruction cases and ten rejection
guards** against public A1763 C1000 Gen 2 LCD container **0.1.9.6**, supplied with
main 1.1.4.9. The 925,696-byte container has SHA-256
`c314816f396d6b6958a6a398476eafa182f7c368802569803b60bd6a4e6dc97d`;
its 214,016-byte application at `08006000` has SHA-256
`f5683c2f7c9a5bdab638123a7a0a547d3d549e513e18fc10918ed979b83459c6`.

Selected scheduler, queue, callback, placement, bitmap and marker-consumer
instructions execute. Timer registers are synthetic RAM; heap, SPI reads,
memory clearing, logs and the post-update timer notification are substitutes.
Execution stops **before** reset preparation, SPI sector erase, parameter
erase/program and failure handlers. Boot, whole firmware, actual interrupts,
flash writes, resource installation and stations do not execute. Consumer cases
start from supplied headers/versions rather than replaying ingress and CRC.

## The pending fields are scheduler records

The independently reconstructed initialized-data image from the previous proof
contains fourteen 32-byte records at **`20000290`**. Its SHA-256 remains
`cfbbfae6df83687b91c87016bb40622b3e1a41da1698d7f13b888d1f1e909b5f`.
Actual activation `0803028c`, timer scan `080074c8` and queue drain
`0802c184..0802c220` establish this selected layout:

| Record offset | Selected use |
| --- | --- |
| `+00` | Enabled word |
| `+04` | Callback mode byte: 1 runs inline; 0 queues |
| `+08` | Interval in timer-handler invocations |
| `+0c` | Callback pointer |
| `+10` | Retained argument word; consumption is not established here |
| `+14` | Counter incremented once per admitted scan |
| `+18`, `+1c` | Intrusive queue links |

Thus the earlier **`200002b8 = 3000`** is event 1's interval, and clearing
`200002c4` resets its counter. The earlier **`20000400`** context pointer is
event 11's retained argument word. Changing an event to enabled resets its
counter; requesting its existing state preserves the counter. A zero interval
blocks activation, and an already queued event is not appended twice. Disabling
a queued event unlinks it. Counter fixtures 0/2,998 remain below threshold;
2,999 reaches the callback or queue on the next admitted scan. No elapsed-time
unit or interrupt delivery is verified: **3,000 ticks is not a measured delay**.

| Event | Initialized mode | Interval, raw ticks | Callback |
| ---: | ---: | ---: | --- |
| 1 | 1, inline | 3,000 | `0803027a` |
| 11 | 0, queued | 3,000 | `0802ab1c` |
| 13 | 1, inline | 900,000 | `0803027a` |

Event 1's actual callback disables event 1, then reaches **`080109a8`**.
Replay stops there. Static inspection shows that helper disables selected NVIC
interrupts; the following **`08006f80`** requests Cortex-M system reset through
AIRCR at `e000ed0c`. Neither helper executes. This identifies a reset-directed
special-payload route, not bootloader installation or physical output behavior.

Event 11 is removed by the actual main-loop queue fragment. Its actual callback
disables event 11 and reaches **marker consumer `0802a438`**. A dedicated case
stops at that entry and verifies queue removal and retention of `+10`.
The callback does not read that argument word in the selected path; the marker
consumer instead reads persistent words and the staging header. This does not
prove that the retained context is unused elsewhere.

## Refresh calculates a missing-resource bitmap

Actual **`0802a374..0802a40c`**, previously substituted, reads the runtime
resource table pointer at **`20000710`** and fourteen 12-byte records. It sets
bit *i* when the table is absent, record word `+00` is zero, or record word
`+08` is zero. Other records clear their bit. It stores the resulting bitmap
at **`2000022c`**, calls substituted logs and returns.

Cases cover an absent table, all present/all missing, alternating bits, each
individual missing bit and missing pointers despite nonzero leading words.
The runtime table is preserved. This function does not install assets in the
selected replay; interpreting it as a renderer commit would be incorrect.

## Ordinary resource placement

Actual **`0802bf46..0802bf92`** resolves the recovered catalog's `ffffffff`
middle words. Each starts after the previous destination plus its capacity,
rounded up to 4,096 bytes. Name/extent checks execute with bounded actual
`strlen`. The resulting catalog region ends at **`0096e000`**, below staging
base **`00c00000`**.

| Index | Name | Selected SPI destination | Capacity word, bytes |
| ---: | --- | --- | ---: |
| 0 | `startup` | `00001000` | 614,400 |
| 1 | `ss_hour` | `00097000` | 155,648 |
| 2 | `ss_min` | `000bd000` | 155,648 |
| 3 | `ss_bg` | `000e3000` | 155,648 |
| 4 | `was_reset` | `00109000` | 462,848 |
| 5 | `fast_charge` | `0017a000` | 1,847,296 |
| 6 | `cumu_usage` | `0033d000` | 528,384 |
| 7 | `first_pv` | `003be000` | 1,245,184 |
| 8 | `cumu_pv` | `004ee000` | 528,384 |
| 9 | `normal2tou` | `0056f000` | 524,288 |
| 10 | `tou2normal` | `005ef000` | 524,288 |
| 11 | `dev_idle` | `0066f000` | 495,616 |
| 12 | `silent_charge` | `006e8000` | 1,282,048 |
| 13 | `super_output` | `00821000` | 1,363,968 |

For every name, the actual marker consumer reaches **`080102bc`**, requesting
the first sector erase at the corresponding destination. Replay stops before
that service: these are caller-selected logical addresses, not verified
physical partitions or installed resources. Display names do not establish
new charging controls, counters or complete saved-settings export.

## Marker-consumer decisions before writes

`0802a438` reads 20 bytes at **`08005000`** and requires first word
**`a5a5a5a5`** for the pending path. Three other marker values stop at its
nonpending branch without allocating or reading staging. Old parameter words
are synthetic because that page is absent from the supplied application.

For a pending fixture, actual instructions select mode 2, allocate a substituted
1,024-byte header and read staging at `00c00000`. The ordinary catalog lookup
uses actual bounded `strcmp`. It compares the descriptor's byte-reversed
version word with runtime record `+04`:

- A matching version skips the payload read and first resource erase.
- A differing known entry reads its leading u32. Counts **1/300** pass;
  **0/301** accumulate error 8 and later reach parameter erase.
- Declared length plus four must fit the catalog capacity. Supplied `ss_hour`
  lengths **155,644/155,645** take the pass/error-8 paths respectively. These
  large-length cases do not validate a large payload or its CRC.
- Unknown names, including `pps_lcd`/`pps_lcd_res` supplied directly to this
  ordinary consumer, are skipped. Their special ingress route still requires
  the separately identified reset/bootloader path.

Unchanged/unknown and tested error paths release the synthetic header, prepare
first parameter word **zero**, and reach **`08013f84`** with address `08005000`
and length 20. Execution stops before erase; the following program call is
statically visible but does not execute. The input parameter page remains
unchanged. Marker clearing is therefore a requested write on these paths,
not proof of successful installation. The first resource-erase boundary still
has its header live; subsequent copy/reload/cleanup is outside this proof.

The consumer also reads a version word at **`08040000`**; the replay seeds that
absent application page explicitly. Separately, the public 710,656-byte
`pps_lcd_res` payload has SHA-256
`f61823dde70028254b74a534f88ed249f21136a776acdfd1d0420bc0a20c247c` and
leading word **`190a1801`**, matching the compile-time word observed in actual
consumer register `r2` before a substituted log. This agrees with a resource
version relationship; placement of that payload at `08040000` remains an
inference, not an executed bootloader copy.

## Reproduction and next boundaries

```sh
SOLIX_ANALYSIS_OUTPUT=/private/output/lcd-worker \
python3 tools/firmware_analysis/emulate_lcd_pending_worker.py \
  --output-dir /private/output/lcd-worker
```

Compare both `lcd-pending-worker-{results,manifest}.json` artifacts with
`expected_results/`. Python **3.14.4**, Unicorn **2.1.4**, Capstone **5.0.7**
for separate static inspection. Bound **10,000 instructions per entry**;
observed maximum **721**. Guards reject boot/reset/real flash/SPI-write bodies,
MMIO access, callback mutation, catalog writes after placement, unbounded names
and marker overreads. Fixture regions are checked for overlap; catalog and
parameter preservation and PRIMASK restoration are asserted. Raw static
analysis stays ignored/private; public artifacts contain synthetic metadata.

Next: trace the ordinary post-erase copy, payload frame/index reader `08029b60`
and reloading/error cleanup; then obtain a pinned bootloader input for special
code/resource installation and recovery. No complete update API or safe recovery
claim is justified yet. True charging pause, complete settings readback and
energy calibration remain independent investigations. **Zero station commands
were sent, and the live deployment is unchanged.**

The [copy/index continuation](lcd-resource-copy-and-index-validation.md) now
executes the ordinary copy and frame-index reader with storage/flash substitutes.
It verifies chunk boundaries and cleanup, while exposing truncated/unchecked
frame extents and zero caller returns on tested malformed-resource paths.
Rendering, real writes, fatal-handler recovery and bootloader installation
remain unresolved.
