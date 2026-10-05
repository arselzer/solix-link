# LCD frame streams, header probing and transport returns

## Evidence and scope

This follows [resource copying and index validation](lcd-resource-copy-and-index-validation.md).
The new bounded proof runs **105 synthetic instruction cases and fifteen
rejection guards** against the public A1763 C1000 Gen 2 LCD container
**0.1.9.6**, supplied with main **1.1.4.9**. The 925,696-byte container SHA-256
is `c314816f396d6b6958a6a398476eafa182f7c368802569803b60bd6a4e6dc97d`;
the 214,016-byte application loaded at `08006000` has SHA-256
`f5683c2f7c9a5bdab638123a7a0a547d3d549e513e18fc10918ed979b83459c6`.

Actual selected file callbacks, filesystem dispatch, image-header probe,
address encoding and polling send/receive instructions execute. Heap, string
helpers and storage reads are substitutes. Runtime indexes, registry nodes,
clock, status/data and the SPI register block are supplied RAM fixtures.
There is no real MMIO, driver initialization, rendering, flash programming,
erase, reset, boot or station access. This establishes selected instruction
behavior, not a physical fault, complete installation or electrical behavior.

## Resource index to file interface

Actual `0802bfd6..0802c02c` prepares this 52-byte driver record at `2000071c`:

| Driver offset | Selected value / callback |
| --- | --- |
| `+00`, byte | ASCII **L** |
| `+04`, word | Zero; admitted filesystem dispatch takes its uncached path |
| `+0c` | Open `08029e6c` |
| `+10` | Close `08029e2c` |
| `+14` | Read `08029fcc` |
| `+18` | Stub returning raw 9 at `0802a040` |
| `+1c` | Seek `0802a014` |
| `+20` | Stub returning raw 9 at `0802a03c` |

Execution stops before registry insertion at `0800914c`. Later probe cases
seed one registry node explicitly; they do not initialize a real display.
Static animation code at `0802a2fe..0802a31e` constructs the source string
`%c:/%d/%d.bin` and passes it to `0801f09c`. The selected filesystem opener
`0801df70` finds a matching drive letter, removes its optional colon and calls
the registered open callback with the remaining path.

The open callback copies a bounded path, splits on `/`, and converts two tokens
as decimal numbers. **Those library operations are host substitutes**; printf
truncation, malformed numeric input and libc overflow behavior are not proved.
Actual following instructions check resource index below fourteen, runtime
pointer, index pointer, positive frame count, frame index below that count and
enabled byte `20000714`. The checked path returns a newly allocated 12-byte
handle:

```text
+00 current SPI address = frame-header address
+04 initial SPI address = frame-header address
+08 extent = second word of the selected frame-index entry
```

All fourteen resource selections and rejection cases are replayed. The
callback frees its allocated handle on missing runtime/index/count, disabled
state and out-of-range frame. Allocation failure stops before the previously
identified system-reset handler. Close returns raw zero after actual heap-used
bookkeeping; a null close argument returns raw eleven.

One case also runs the preceding proof's **actual index builder** on two
complete synthetic frames, then transfers its exact index and host-storage
bytes into a separate file-callback guest. This verifies the connection between
the two slices without claiming startup, concurrency or a complete renderer.
Other cases explicitly seed their indexes, including malformed/stale examples.

## Read and seek limits

Actual read computes, with 32-bit arithmetic:

```text
available = extent + initial_address - current_address
if signed(available) >= 1:
    requested = min_unsigned(requested, available)
    request storage read(current_address, requested)
    current_address += requested
else:
    requested = 0
return 0 and write requested to caller's count word
```

The storage substitute's deliberately supplied raw eleven is ignored. A zero
requested length still reaches the storage service when positive space remains.
Null handle/buffer arguments return raw eleven without updating the count word;
the admitted filesystem wrapper initializes its own count first. Seeded extents
`80000000` and `ffffffff` appear exhausted in the initial signed comparison.

The index builder's computed extent excludes the 12-byte header, yet open places
the initial cursor **at that header**. In the complete 2×3, format-15 fixture,
the index records eighteen bytes. Reading twelve header bytes followed by a
request for eighteen pixel bytes supplies only **six**, then reaches EOF.
This is a reproducible sequential-stream boundary. Actual display corruption,
all renderer access patterns and any other compensating path are not proved.

Seek accepts origins zero, one and two:

| Origin | New cursor, modulo 2³² |
| --- | --- |
| 0 | `initial + offset` |
| 1 | `current + offset` |
| 2 | `initial + extent - offset` |

It returns zero without clamping to the frame. Fixtures seek past EOF, before
the frame and through unsigned wrap. Subsequent reads can request earlier bytes
in the synthetic resource, including prefix bytes. A seek outside host storage
is rejected by the proof's guard, **not firmware bounds checking**. Origin three
returns eleven and preserves the cursor. Restoration/export APIs must not infer
safe resource readback from these callback return values.

## Actual 12-byte image-header probe

Selected `08017990` performs extension lookup, actual filesystem open/read/close
dispatch and a twelve-byte header request. With the seeded uncached driver,
these reach the actual callbacks above. Indexed extents zero, one and eleven
produce a short read and probe result zero. Extents twelve and eighteen produce
probe result one **without reading pixels**.

For a magic-25 file header, the probe ORs **`0020`** into the little-endian
word at offset two. Other supplied fields, including nonzero words at offsets
eight and ten, are preserved. The deeper decoder statically uses offset eight
in row-size/stride comparisons, but this proof does not assign a complete
vendor-format meaning to those fields or remaining flag bits.

A deliberately stale/corrupt indexed-header fixture with first byte 24 is
normalized to first byte 25 and second byte 24, then receives the same flag bit
and probe result one. The ordinary index builder would reject that first-byte
magic; this case explicitly bypasses it. It demonstrates a probe branch, not
an admitted ordinary update or valid color format.

## SPI address encoding

Actual `080107f6` reads a mode byte from the storage context:

- **Mode 0:** sends the low twenty-four address bits, most significant byte
  first, through three one-byte transport calls. `12345678` becomes `34 56 78`.
- **Mode 1:** sends all thirty-two bits, most significant byte first, through
  four calls.
- Tested modes **2 and 255:** send no address bytes and retain that raw mode
  value as the return in this slice.

Sixteen cases cover zero, staging address `00c00000`, `12345678` and `ffffffff`.
Transport is substituted only in these address-only cases. Supplied returns
six/eleven from early bytes do not stop subsequent sends; the final call's return
is retained. These fixtures do not establish the device's actual context mode,
flash addressing mode or capacity.

## Actual polling transport return contract

Actual send `080159f8` and receive `080156dc` run with a synthetic register block
at **`2001d000`**, never a hardware address. Hooks supply the clock at `20000628`,
status bits and received data at the exact admitted reads. Mode-change byte
`channel+2` is zero; actual mode switching and initialization are excluded.

| Selected case | Raw return | Other executed behavior |
| --- | ---: | --- |
| Null buffer or length zero | 6 | Does not start a transfer |
| Admitted ready polling loop | 0 | Clears the corresponding channel busy byte |
| Poll deadline exceeded | 11 | Leaves that channel busy byte set |
| Width byte 1 with odd length | 0 | Processes only `length // 2` halfwords |
| Tested unsupported width byte 2 | 0 | Clears busy byte without processing data |

Width zero transfers bytes. Width one transfers little-endian halfwords; a
one-byte request therefore performs no units. Timeout fixtures cover both
widths, transmit-ready waiting, receive-ready waiting after a data-register write,
and partial byte transfers. The raw polling deadline is **100** clock units:
equal elapsed time still permits another status read, greater elapsed time exits.
No real time units, peripheral readiness, asynchronous mode or recovery is proved.
These actual returns give meaning to six/eleven on the selected transport paths;
the preceding proof's arbitrary service return seventeen remains a substitute.

Five additional cases execute the actual storage read wrapper `080104e2`, the
actual address encoder, and actual send/receive loops together. Synthetic channel
**255** excludes physical chip-select branches. The wrapper sends opcode `6b`,
three address bytes and four `a5` dummy bytes, updates only the supplied RAM
control word, then requests data. Ready, zero-length/invalid receive, timeout
and partial-receive cases all return the synthetic **register-block pointer**,
not the receive routine's zero/six/eleven. Thus even the actual nested transport
return is not exposed by this selected wrapper. Physical reads and the normal
chip-select branch remain unverified.

## Reproduction and remaining work

```sh
SOLIX_ANALYSIS_OUTPUT=/private/output/lcd-frame-stream \
python3 tools/firmware_analysis/emulate_lcd_frame_stream.py \
  --output-dir /private/output/lcd-frame-stream
```

Compare complete `lcd-frame-stream-{results,manifest}.json` artifacts with
`expected_results/`. Python **3.14.4**, Unicorn **2.1.4**, static Capstone
**5.0.7**. New slice bound **10,000 instructions per entry**; the reused index
builder retains its 100,000 bound. Observed maximum **721**. Independently
repeated result/manifest pairs match byte-for-byte, including source hashes.
Guards reject rendering, boot/reset bodies, flash programming, real MMIO,
index/runtime mutation, unbounded strings, host-storage overreads and unsupplied
clock/status/receive values. Raw exploration remains ignored/private.

Next: trace actual pixel requests beyond `08017a34`, remaining header flags and
row-size calculations, then the full-transfer/release wrapper `080158bc` and
page-program caller's retained send result. An independently pinned bootloader
is still required for special LCD code/resource installation and recovery.
Any future local update workflow needs host validation of full frame extents,
verified receiver readback and bounded recovery; ACK, marker clearing, header
acceptance and zero returns are insufficient. True charging pause, complete
settings export, original C1000 charging measurements and physical Gen 2 energy
calibration remain open. **Zero station commands were sent.**

Follow-up: [pixel reads, image-size math and transfer cleanup](lcd-pixel-reads-and-transfer-cleanup.md)
executes selected row requests and nested cleanup with supplied file state and
RAM register blocks. It does not render images or access stations.
