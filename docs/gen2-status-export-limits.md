# Gen 2 status responses and complete restore limits

This follow-up uses C1000 Gen 2 A1763 MainMcu **1.1.4.9**, SHA-256
`21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9`.
It executes serializers in synthetic RAM only. No station commands were sent.

The later [full inventory](gen2-full-status-inventory.md) executes all 19 ordinary
callbacks and extends twelve saved-state collisions across the complete set.
The fifteen-callback limit below describes this earlier batch.

## The additional FA callback

The ordinary full-status descriptor table contains 19 fields. `FA` is type
`04`, handled at `0801af54`, and produces a 21-byte typed value. It does not
read the saved settings block. Its behavior is identical in descriptor modes
1, 2 and 3 in this replay.

Offsets below include the type byte at position zero:

| Typed offset | Replayed behavior |
| --- | --- |
| 0 | Type `04` |
| 1–4 | Constant `01` |
| 5, 8, 9 | Retain the caller's existing destination bytes |
| 6 | Constant `17` |
| 7 | Low six bits become `11`; high two bits retain their prior values |
| 10–20 | Zero |

These constants do not establish the meaning of feature bits. The preserved
bytes are not saved configuration or reliable telemetry. Actual allocation
initialization in the full-status caller remains a separate question; this
tool deliberately varies the destination contents. Capacity must exceed 21
bytes at the current offset or the callback writes no response.

## Distinct settings with identical responses

Twelve counterexamples independently change an automatic backup record's
maximum/start/end, the first clock-window enable bit, clock text, or reserved
window bits. The complete saved blocks differ, but actual `A4`, `D9`, `DA`
and `FA` responses remain identical. The serializers preserve those blocks.
The automatic records are disabled and in the future, so these examples do
not exercise an active plan.

These four responses cannot support complete backup/clock restoration.
This does **not** prove that every remaining status callback or indirect
diagnostic route lacks an export. Existing timer limitations and storage
findings remain in [backup queries](gen2-backup-query-investigation.md),
[clock preservation](gen2-clock-screen-preservation.md) and
[persistent plans](gen2-persistent-plan-followup.md). Do not use a matching
projected response as proof that an entire saved configuration was restored.

## Reproduction

```sh
PYTHONPATH=/path/to/unicorn python3 \
  tools/firmware_analysis/emulate_gen2_status_export_limits.py \
  --output-dir /tmp/solix-status-export
```

Compare both `gen2-status-export-limits-*.json` files against
`tools/firmware_analysis/expected_results/`. The 29 cases cover 12 destination
and mode combinations, five capacities and 12 saved-state collisions.
The manifest pins every loaded local analysis source and Unicorn/Python
versions. Memory helpers, calendar conversion, logging, persistence and
transport retain the declared substitutes of the inherited harnesses.
Fifteen other full-status callbacks are not executed by this tool. No claim
is made about C2000 Gen 2 firmware or physical electrical behavior.
