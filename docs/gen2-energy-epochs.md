# Energy counter boundaries and offline comparison

## Actual instruction evidence

The new replay adds **13 synthetic cases** for **A1763 / C1000 Gen 2 main
1.1.4.9**, SHA-256
`21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9`.
It extends [energy arithmetic and timing](gen2-energy-counter-investigation.md).
Actual reset, accumulator and protobuf encoding instructions execute with
synthetic RAM. The inherited pre-update statistics, persistence-copy/flush,
diagnostic and dynamic protobuf callbacks remain substituted. No flash,
transport, whole firmware, physical clock or station runs.

Reset **`08029868`** clears exactly **216 (`d8`) accounting bytes** at
`200032c0`, then tail-calls persistence-copy helper `0802a9ac`. It preserves
the following 64 inspected bytes, energy sample phase at `2000024a`, report
sample counter at `20000260`, and the protected output word. Nine cases vary
the initial fill and sample phase 0/1/9. At phase 9, the next accumulator
callback immediately adds a sample after reset; reset does not restart the
ten-callback sampling phase. This is not a verified flash commit or reboot test.

Four additional cases cover every accounting group. An internal 64-bit sum
increases from `360*(2^32-1)` to `360*2^32`; actual encoded AC-input values
decrease from **4294967295 to 0** without any internal reset. A native reset
and report-width wrap can therefore both cause a decreasing wire counter.
One direct Thumb-BL candidate to the reset was found at `08029888`, in wrapper
`08029880`. The previously audited factory table registers that wrapper as
selector-0 property **DC**; see [the diagnostic inventory](gen2-diagnostic-getter-audit.md).
This links the reset to a factory operation, not an ordinary status field.
Physical reachability and hardware reset epochs remain unverified.
No reset request or new incoming counter query is implemented.

## Offline analysis helpers

`solix_link.energy_analysis.analyze_energy_epochs(reports)` accepts 1–1024
sanitized rows shaped like:

```json
{"timestamp":600,"groups":{"standard":{"ac_input_energy_raw":60,"ac_output_energy_raw":60}}}
```

Use `timestamp` consistently for radio event-construction time or another
explicitly identified clock. It is **not established MCU snapshot time**.
The helper uses the pinned A1763 32-bit report domain; it rejects booleans,
nonfinite values, unknown fields and larger counters with fixed
`InvalidEnergyAnalysis` text. It is not a decoder for arbitrary models.

Adjacent rows produce raw deltas only while all supplied counters are present,
nondecreasing and time increases within `max_report_gap` (default 3600 s).
A counter decrease, coverage change, clock reversal/duplicate or long gap starts
a new **analysis segment**. These labels do not identify actual firmware reset
epochs. No modular wrap correction, zero substitution, cross-gap total, physical
unit conversion or lifetime-energy sensor is inferred. Output omits absolute
timestamps and has `units_verified: false` and `lifetime_total_available: false`.

`compare_power_interval(raw_delta, start, end, samples)` accepts 2–4096
`{"timestamp":...,"power_w":...}` samples. It clips/interpolates bracketed
endpoints and estimates trapezoidal **AC energy**. Original sample gaps over
30 s are excluded; missing endpoints or incomplete coverage suppress the
raw-units/estimated-Wh ratio. Zero reference energy yields no ratio. Even a
ratio of one keeps `physical_units_verified: false` and
`mcu_snapshot_timing_verified: false`. AC energy includes bypass loads and is
not battery energy or conversion efficiency.

These pure functions do not read a profile, collect live samples, change
analytics reporting, write history or expose HA lifetime statistics.

## Remaining physical calibration

For each model/firmware separately:

1. Use an independent calibrated meter and a steady noncritical load; retain
   several consecutive raw reports and dense AC input/output samples.
2. Preserve radio event times, host receipt times and controller FE time.
   Resolve the MCU snapshot/queue delay before comparing intervals.
3. Check actual sample cadence and load response; discrete ten-second sampling
   and cached sensor power can differ from host interpolation.
4. Observe retention across an authorized noncritical restart, and identify
   legitimate reset events. A decreasing counter alone cannot distinguish reset,
   width wrap, reordered data or an acquisition gap.
5. Validate all intended accounting groups/channels and units. C2000 firmware
   remains unavailable; the existing C2000 discrepancy is not explained by
   substituting A1763 timing or arithmetic.

Do not use the server-backed C2000 for reset experiments. Existing private
SQLite history continues providing estimated AC energy with explicit coverage
and restart gaps; no history/HA statistics behavior changes in this batch.

## Reproduction

```sh
python3 tools/firmware_analysis/emulate_gen2_energy_epochs.py \
  --output-dir /private/output/energy-epochs
```

Compare `gen2-energy-epochs-{results,manifest}.json` with `expected_results/`.
The image is size/hash pinned. The manifest records sources, Python **3.14.4**
and Unicorn **2.1.4**. Raw analysis outputs remain ignored and private.
