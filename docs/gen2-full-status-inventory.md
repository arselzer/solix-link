# All ordinary Gen 2 status callbacks: export limits

## Result and pinned scope

The [earlier FA investigation](gen2-status-export-limits.md) left fifteen
ordinary status callbacks unexecuted. The new bounded suite executes **all
19 callbacks**, using their actual descriptor entries and getters, in
**240 synthetic cases**. Twelve pairs of different dormant saved configurations
produce identical values across **all 19**, extending the earlier four-response
counterexamples. Complete restoration remains unsupported.

Input: **C1000 Gen 2 / A1763 main 1.1.4.9**, 198,656 bytes at `08005000`, SHA-256
`21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9`.
This does not establish C2000, original C1000 or other firmware behavior.
No physical station, transport, flash, electrical output or whole firmware runs.

## Descriptor context and freshness

The real full-status traversal at `080224a0` uses 19 twelve-byte entries.
It copies each entry's word at `+8` into callback context **`+16`**. Shared
port serializers use that pointer for string comparisons; omitting it causes
false unmapped-memory failures. The new suite supplies the original word,
correct type, leading tag, buffer/length pointers, capacity and refresh mode.

Each callback runs independently for internal modes **1/2/3** with previous
buffer fills **00/55/aa/ff**: 228 cases. Instrumentation tracks every actual
saved-byte read and verifies the `0x19f`-byte saved block and output word at
`20000164` remain unchanged. Original memcpy/strcmp/getters execute. Logger
and calendar-year conversion are the only step-hook substitutes. RAM, RTC,
GPIO, initial cache and dormant future records are synthetic; RTC/GPIO and
firmware memory are read-only. Each callback has a 30,000-instruction bound.

| Tags | Observed saved-state reads across those cases |
| --- | --- |
| A2 | Settings offsets `0d..0e`; not a complete preferences export |
| A4 | Ordinary preferences, language and alert; no complete hidden records |
| D9 | Offset, caps/reserve/mode/count, manual timestamps and switches |
| DA | Portions of the first clock-window record |
| FE | Stored UTC offset |
| A3/A5/A6/A7/A8/AA/AB/AC/AE/B2/DC/F9/FA/FD | No reads within the tracked saved block |

These reads are input/path observations, not an exhaustive static assertion
for every possible runtime state. Sensor/cache reads outside the saved block
are not included in that column.

Previous-buffer bytes matter even in full mode 1: **A2[20]**, parts of
**A4[32..33]**, **F9[9..12]**, and parts of **FA[5/7/8/9]** vary with the fill.
The suite records fill-sensitive offsets for every mode; mode 2/3 can retain
more. Offsets include the type byte but exclude tag/length. Arbitrary fill
may produce invalid typed bytes; these are stress fixtures, not packets to
send. A recent message does not establish per-field freshness.

## Full-response collisions

Each of the remaining twelve cases compares two independently seeded machines
with a different saved field:

- Maximum, start or end of each of three **disabled future automatic backup
  records**: nine pairs.
- First clock-window enable, text or reserved bits: three pairs, with the
  common display state held at the synthetic baseline.

Every callback's mode-1 value matches within each pair, while the saved blocks
remain different and preserved. The counterexamples therefore rule out a
complete backup from this ordinary response set in these reachable dormant
states. They do not prove that an indirect protocol, another runtime branch
or a different firmware cannot export the missing fields.

Next work needs an independently established indirect read-only export, or a
validated backup of every hidden field before testing writers. More A4/D9
queries or an ACK cannot supply missing automatic windows, maxima or clock
text. Do not reset, erase or reconstruct guessed records.

The [factory aggregate follow-up](gen2-factory-aggregate-export-limits.md) also
rules out selector 1/property 0016 in seven synthetic cases. It refreshes cache
and its enclosing dispatcher stops an upgrade timer; it is not a passive query.

## Reproduction

```sh
python3 tools/firmware_analysis/emulate_gen2_full_status_inventory.py \
  --output-dir /private/output/status-inventory
```

Compare both `gen2-full-status-inventory-*.json` files with `expected_results/`.
The manifest records every imported local harness hash, Python **3.14.4**
and Unicorn **2.1.4**. Public results contain hashes and synthetic read/coverage
metadata; raw diagnostic disassembly remains private. The enclosing request
handler/traversal is not rerun here; its earlier bounded proof remains separate.
