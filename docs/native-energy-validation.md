# Native energy scaling and statistics readiness

## Passive comparison, 2026-10-07

This extends the [energy epoch analysis](gen2-energy-epochs.md) using retained
uploads and the existing private SQLite power history. No station commands,
power/charging changes, cloud requests or service restarts were needed.
These are comparisons against the station's **own reported watts**, not an
independent physical meter calibration.

The frozen comparison contains **8 C2000 Gen 2 reports** over 63 minutes and
**5 C1000 Gen 2 reports** over 40 minutes. All uploads have a single event;
no duplicate counter groups, decreases, coverage changes or nonincreasing
radio event timestamps appeared. This short sequence does not test reset,
wrap, reorder, long outages or reboot retention.

Intervals use the upload's **top-level `utc_ts`**, a radio event-construction
timestamp. Host receipt and radio time differed by 0.080–0.313 seconds on
C2000, and 0.287–0.310 seconds on C1000 Gen 2. Neither is established MCU
snapshot time. Power history brackets both endpoints with gaps at most
15 seconds and no recorded history-gap markers. No extrapolation is used.

| Model / main firmware | Covered intervals per AC channel | Radio interval | Ratio: raw delta / integrated Wh | Integer-rounding compatibility |
| --- | --- | --- | --- | --- |
| C1000 Gen 2 / 1.1.4.9 | 4 | 600 s | 0.99276 for both AC input/output | 1.0 Wh/unit fits all four intervals |
| C2000 Gen 2 / 2.1.6.4 | 7 | 540 s | 1.10847 input; 1.11208 output | 1.0 Wh/unit fits none; 0.9 Wh/unit fits all seven |

Ratios combine the covered intervals; individual ratios fluctuate with coarse
counter increments and sampling. AC-output duration increases by one each
interval on both models. C1000's 1.0 interpretation matches its
[hash-pinned arithmetic proof](gen2-energy-counter-investigation.md).

## C2000: a specific scaling hypothesis

Applying **0.9 Wh per raw unit** gives aggregate counter/power ratios
**0.99762 input** and **1.00087 output**. For every covered interval, its
difference from integrated power is less than 0.9 Wh. That is compatible with
two integer-counter endpoints rounded at this candidate scale. The earlier
six-interval comparison likewise gave about 1.111 before correction.

The timing ratio **540 / 600 = 0.9** is a plausible lead: C2000 may sample or
convert using a different timing basis. **Its firmware is unavailable**, so
this does not establish the implementation. Discrete sampling, cached sensor
values and MCU snapshot delay can also affect comparisons. A fitted scale is
not independent meter evidence, and cannot establish accuracy against real
electrical energy.

The public C2000 conversion remains explicitly **assumed/unverified**;
0.9 is **not applied automatically**. The comparison covers Standard AC
input/output only. DC/other-port energy, TOU and backup groups are unqualified.
AC values include bypass and do not isolate battery charge/discharge.

## Reusable offline hypothesis check

`compare_power_interval(..., candidate_wh_per_raw_unit=0.9)` now adds a
`candidate_scale` result: converted interval energy, difference from integrated
power and a strictly exclusive one-unit rounding bound. A complete, nonzero
reference is required for a compatibility decision. Gaps or zero reference
return `rounding_compatible: null`. Invalid scales use the existing fixed error
text. Omitting the argument preserves the original result contract.

The bound tests **only the integer-rounding hypothesis**. It does not compensate
for sampling or measurement error. `physical_units_verified` and
`mcu_snapshot_timing_verified` remain false even when a candidate fits.
Seventeen added synthetic cases exercise alternative scales, bound edges, gaps,
zero reference and invalid inputs; all **42 energy-analysis tests** pass.

## What remains before native HA statistics

The [observed Standard AC meter](native-energy-meter.md) now implements
persistent ordering/duplicate guards, quarantine and separately named
Energy-compatible C1000 Gen 2 estimates. It counts observed deltas from zero,
not complete device lifetime energy. The physical scaling/retention items below
remain open; C2000 is still excluded from native statistics. No remote ordinary
C1000 Gen 2 restart route is verified, and the user is unavailable for the
button test.

1. **Scaling:** compare against an independent plug-in energy meter at multiple
   steady loads. C1000 Gen 2 has consistent nominal Wh evidence; C2000 still
   needs confirmation of its 0.9 hypothesis, separately by intended channel.
2. **Retention:** capture native reports before and after an ordinary restart
   of the noncritical C1000 Gen 2. Record exactly which restart was performed;
   a button restart does not establish full battery-disconnection retention.
   Do not power-cycle the server-backed C2000 for this investigation.
3. **Reset/order handling:** retain radio timestamps separately from host
   receipt. Deduplicate/reject out-of-order data, quarantine unknown decreases
   and multi-report batches, and preserve meter baselines across gateway/HA
   restarts. A decrease alone cannot identify a legitimate reset.
4. **HA accounting:** expose independently named mode/channel sensors with
   tested persistent continuity and statistics semantics. Do not sum overlapping
   backup groups or silently treat AC output as battery discharge.

HA accepts estimated energy sensors; laboratory calibration is not a platform
requirement. The remaining issue is an accurate declared conversion and defined
reset/accounting behavior, rather than the Diagnostics entity category.
See [HA consumption options](home-assistant-energy-dashboard.md).

Raw requests, cumulative readings, identities, absolute timestamps and full
comparisons stay in the ignored private folder. Public findings use ratios and
relative timing only. Live native conversions, HA entity classes and station
settings are unchanged by this analysis.
