# LCD pixel reads, image-size calculations and transfer cleanup

## Evidence and scope

This continues [frame streams and transport returns](lcd-frame-stream-and-transport-contract.md)
with **94 synthetic instruction cases and fifteen rejection guards**. Input is
the public A1763 C1000 Gen 2 LCD container **0.1.9.6**, supplied with main
**1.1.4.9**: 925,696 bytes, SHA-256
`c314816f396d6b6958a6a398476eafa182f7c368802569803b60bd6a4e6dc97d`.
The 214,016-byte application at `08006000` has SHA-256
`f5683c2f7c9a5bdab638123a7a0a547d3d549e513e18fc10918ed979b83459c6`.

Selected pixel acquisition, format, filesystem seek/read, image-size,
full-transfer, polling transport and release/disable instructions execute.
Decoder state, registry/index, file interface and existing row buffer are
supplied fixtures. String/heap/storage helpers remain substitutes. Clock,
status, received data and SPI/DMA register blocks are RAM; callbacks and
concurrent flag changes are explicit host substitutes. No real MMIO, DMA,
interrupt, whole decoder initialization, palette conversion, rendering, flash
programming/erase, boot, reset or station executes. Source/stride mappings are
instruction evidence, not a rendered image or physical installation test.

## Actual file pixel-acquisition path

The selected callback **`08017604`** receives a decoder context, requested area
and current row coordinates. The supplied context already has a scratch object,
an uncached file interface and an existing one-row buffer; this avoids the
allocation sentinel and does not establish whole image-open initialization.
Actual callback instructions increment the row coordinates and stop after the
requested area's final row. They execute actual format helper `08017fc4`.

For admitted file-source formats **15..19**, the ordinary row request is:

```text
source_offset = 12 + row_index × source_stride + floor(column × bits / 8)
requested_bytes = floor(column_count × bits / 8)
```

The source stride comes from **header offset eight**, observed through decoder
context `+18`. Actual `0800fb14` seeks through registered callback `0802a014`,
then delegates to actual filesystem read `0801e0a0` and resource read `08029fcc`.
The twelve-byte prefix is added for the supplied file-source type one. This
confirms offset eight's role in the selected file-row addressing; it does not
specify alignment or every format's packing rules.

The row caller passes **a null output-count pointer**. It tests the filesystem
return value but never compares the returned byte count with its requested row
size. The resource callback can return zero with fewer bytes, including zero.
The pixel callback then publishes the supplied row buffer and returns **one**.

The replay observes actual internal filesystem counts and independently checks
every written byte and unwritten suffix against bounded synthetic storage. With
the preceding index builder's extent formula seeded for a complete 2×3 frame,
the selected row counts are:

| Format ID | Source stride | Indexed extent excluding header | Bytes supplied for rows 0, 1, 2 |
| --- | ---: | ---: | --- |
| 15, 19 | 6 | 18 | 6, 0, 0 |
| 16 | 8 | 30 | 8, 8, 2 |
| 17 | 8 | 24 | 8, 4, 0 |
| 18 | 4 | 12 | 0, 0, 0 |

All those requested rows return one, even short/empty ones. A row beyond the
requested area returns zero without reading. EOF can leave preceding row bytes
or the initial synthetic fill intact. A partial first-row case writes three
bytes and preserves the rest. A subregion verifies column addressing; a padded
destination verifies that the ordinary request uses column count rather than
reading the entire destination stride. Format 21 is rejected before reads.

Separate fixtures seed an extent **including twelve header bytes** and obtain
complete counts for each tested row. That changes only the host fixture: **the
receiver was not patched or updated**. It isolates the sequential bound without
establishing how real vendor assets, startup or other access paths compensate.
These results do not prove visible corruption on a station.

## Format 20: separate color and alpha requests

Actual format-20 branches at `080177e0..08017840` request two regions per row:

```text
color source = 12 + row × source_stride + 2 × column
color bytes = destination_stride
color destination = beginning of supplied row buffer

alpha source = 12 + source_stride × height + floor(source_stride / 2) × row + column
alpha bytes = column_count
alpha destination = row buffer + destination_stride
```

These establish separate primary and one-byte-per-column regions in the selected
instructions; full color ordering and compositing remain unproved. For the 2×3
fixture, source/destination stride four and indexed extent eighteen, actual
color/alpha counts are **4/0, 2/0, 0/0**. All rows return one. The host fixture
including its header produces **4/2** for each row. Both regions' exact buffer
contents and preserved suffixes are checked; no display operation executes.

## Actual image-size helper

Actual **`080076d8`** computes a 32-bit size from supplied format, height and
stride. If stride is zero it invokes the callback in slot `20014750+0d8`, when
present. That callback is substituted; its width/format arguments and supplied
return are captured. Without it, stride stays zero. Explicit nonzero stride
bypasses the callback.

```text
size = stride × height
format 20: size += floor(stride / 2) × height
formats 7, 8, 9, 10: size += 8, 16, 64, 1024 respectively
```

Arithmetic wraps modulo 2³². Formats 0..21 and 255 exercise absent/supplied stride
callbacks; explicit strides, zero height, odd stride and wrapping values are
also checked. The extra constants correspond to prefix lengths used by the
statically inspected indexed-format loader; palette conversion is excluded.
No allocator, image capacity check or pixel read executes in these size cases.
Width is passed to the supplied stride callback rather than independently
bounding size when an explicit stride is present.

The header's word at offset ten and several flag bits remain unresolved. Static
image-open code tests flag `0008` for another processing route and flag `0010`
before copying a buffer descriptor; the unexecuted allocator seeds `0030`.
These observations do not establish complete flag names, ownership or all
compressed-format behavior. The preceding header-probe proof established its
file-header OR of `0020` independently.

## Actual full-transfer and cleanup behavior

Actual **`080158bc`** waits for previous busy flags, calls the optional begin
callback, sends a buffer, optionally receives only when send returned zero, then
tail-calls release **`080155ec`**. All polling instructions from the preceding
proof execute with supplied clock/status/data. Synthetic channel **255** bypasses
physical chip select. Halfword/initialization branches are outside these byte
transfer fixtures.

- Actual send/receive return zero when ready, six for the tested zero send
  length, and eleven on the tested polling deadlines.
- The full wrapper overwrites those results before tail-calling release.
  Without an end callback, its selected synthetic-channel path returns **255**;
  with the supplied end callback it returns that callback's **seven**, even
  when send/receive returned eleven. The supplied begin return seventeen is
  also ignored. Callback values and channel 255 are fixtures, not asserted
  real success/error values or normal device channel selection.
- Null channel and missing register pointer return raw zero before transfers
  in the selected wrapper, without an explicit distinct invalid-context code.
- Release calls actual receive/send DMA-disable helpers. With the respective
  DMA context absent, they return before clearing its channel busy byte.
  A send/receive timeout therefore retains the corresponding busy byte in
  those fixtures. This does not establish the device's actual DMA context.
- With supplied DMA contexts pointing only to RAM, actual helpers clear SPI
  control bits zero/one, clear each supplied DMA control bit zero and clear
  both channel busy bytes. Exact before/after words and calls are checked.
  This is selected cleanup logic, not actual DMA completion or recovery.

Four additional fixtures cover previous busy flags. The deadline is raw
**3,000** supplied clock units; elapsed equality still waits, greater elapsed
time invokes both disable helpers before proceeding. The wrapper snapshots its
receive-busy byte once but rereads send-busy while waiting. Clearing the receive
byte at a supplied later clock read does **not** end that snapshot-based wait;
clearing the send byte does end its reread path. These are explicit host memory
changes, not an interrupt replay or physical concurrency test.

## Reproduction and next boundaries

```sh
SOLIX_ANALYSIS_OUTPUT=/private/output/lcd-pixel-cleanup \
python3 tools/firmware_analysis/emulate_lcd_pixel_and_cleanup.py \
  --output-dir /private/output/lcd-pixel-cleanup
```

Compare both `lcd-pixel-cleanup-{results,manifest}.json` artifacts with
`expected_results/`. Python **3.14.4**, Unicorn **2.1.4**, static Capstone
**5.0.7**; bound **10,000 instructions per entry**, observed maximum **295**.
The image-size helper uses a host PC end boundary before the return sentinel
to avoid Unicorn's code-hook stop behavior following a conditional POP; no
sentinel instructions or CPU-state normalization execute. Other slices use
the preceding bounded host stop hook. Independent artifacts match byte-for-byte,
including source hashes. Guards reject boot/reset bodies, rendering allocation,
palette conversion/table execution, real MMIO/DMA, flash programming, protected
runtime/index writes, row-buffer overflow and missing clock inputs.

Next: bounded page-program return propagation and post-timeout reentry, default
stride callback provenance and remaining header flags. Special installation and
recovery still require an independently pinned bootloader. Safe local update
support needs independent full-payload validation and receiver readback; neither
a published row pointer, header/version nor zero return proves complete data.
True charging pause/SDK runtime provenance, complete saved-settings export,
original C1000 charging measurements and calibrated Gen 2 energy remain open.
**Zero station commands were sent; C2000 and live services are untouched.**
