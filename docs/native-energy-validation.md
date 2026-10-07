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

### Extended passive sample and held-out intervals

A later read-only comparison retained the original frozen result and extended
the sample to **29 C2000 Gen 2 reports over 4.2 hours**. There are **27 fully
covered intervals per AC channel**. All radio intervals remain 540 seconds;
no source-clock reversal, multi-event batch or counter decrease appeared.
Incomplete power-history coverage is excluded, including the monitoring-upgrade
gap. No station command, reset, cloud call, load change or service action was
needed for this comparison.

| C2000 Standard AC channel | Uncorrected aggregate ratio | Ratio using 0.9 Wh/unit | Compatible with integer rounding alone |
| --- | --- | --- | --- |
| Input | 1.11198 | 1.00078 | 24 / 27 |
| Output | 1.11280 | 1.00152 | 24 / 27 |

The **19 covered held-out intervals** start after the original comparison
artifact's save time; that cohort was not used to choose the 0.9 hypothesis.
Its corrected aggregate ratios are **1.00115 input / 1.00216 output**,
with **16 / 19** intervals compatible with rounding alone on each channel.
No interval supports 1.0 Wh/unit within the rounding bound. This strengthens
the empirical Standard AC scale lead, but **three intervals still exceed the
rounding-only bound**. Sampling, queue timing and measurement error have not
been separated; no new cause is asserted.

C1000 Gen 2 provides a useful parallel reference: 24 reports over 3.83 hours,
22 covered intervals per channel, all compatible with its firmware-supported
1.0 Wh/unit interpretation. Its aggregate ratios are **1.00331 input /
0.99380 output**. These are still comparisons to reported watts, not independent
electrical measurements.

C2000 now exposes an explicitly labelled **assumed-unit estimate** with the
same persistent guards and 1 Wh/raw unit; enabling that did not restart the station.
The estimate was added separately from this passive audit. Verified native scaling still requires
independent meter evidence or equivalent firmware/timing proof. Integrating
its already-supported AC watt readings is the separate available estimate
path described in [HA consumption options](home-assistant-energy-dashboard.md).
No live conversion or HA setting was changed. Detailed readings and cohort
timestamps remain in the ignored private folder.

### The watt integral is not ground truth

The user's sampling objection is valid. A separate read-only history audit
found a median source-sample interval of **5.23 seconds**. The three
rounding-only exceptions have sampled peaks **2.71–3.31 times** their interval
mean. Their corrected residuals range from **−2.12% to +5.52%** across AC
channels. This is consistent with sampling/endpoint effects, but does not
identify their actual cause or reveal unseen spikes. The single source gap
over 15 seconds is excluded from fully covered comparisons.

`coverage_complete` establishes bracketed endpoints and acceptable gaps between
observations. It does **not** establish continuous measurement of the load.
Trapezoidal interpolation can miss pulses between samples or over-weight a
pulse observed at one point. Native C1000 accounting also uses discrete cached
power samples; C2000's method remains unproved. Neither trace is automatically
an independent reference meter.

Two synthetic analytic-waveform cases demonstrate this limit: 360 W base with
a one-second 1000 W pulse every ten seconds has **460 Wh** true energy over
one hour. Five-second point samples can integrate to **360 Wh or 860 Wh**,
depending on pulse phase, despite complete timestamp coverage. A hypothetical
correct 1 Wh/unit counter then fails the rounding-only comparison. This is
mathematical synthetic evidence, not either station's observed waveform.

The three exceptions therefore **do not disqualify an energy estimate**, and
the near-unity corrected aggregate **does not prove 0.9 is the native scale**.
A stable discrepancy can come from scale, timing or systematic sampling bias.
No automatic rescaling is justified by this integral alone. A C2000 estimate
may be exposed with a clearly stated assumption; verified conversion needs
independent meter evidence or a firmware/measurement-path proof.

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

## What remains for physical calibration

The [observed Standard AC meter](native-energy-meter.md) now implements
persistent ordering/duplicate guards, quarantine and separately named
Energy-compatible C1000 Gen 2 and C2000 Gen 2 estimates. It counts observed deltas from zero,
not complete device lifetime energy. The physical scaling/retention items below
remain open; C2000 statistics retain `assumed_wh`, not calibrated units. No remote ordinary
C1000 Gen 2 restart route is verified, and the user is unavailable for the
button test.

1. **Scaling:** compare against an independent plug-in energy meter at multiple
   steady loads. C1000 Gen 2 has consistent nominal Wh evidence; C2000 still
   needs an independent measurement to distinguish scale from sampling/timing,
   separately by intended channel. No 0.9 correction is enabled.
2. **Retention:** capture native reports before and after an ordinary restart
   of the noncritical C1000 Gen 2. Record exactly which restart was performed;
   a button restart does not establish full battery-disconnection retention.
   Do not power-cycle the server-backed C2000 for this investigation.
3. **Reset/order handling:** retain radio timestamps separately from host
   receipt. Deduplicate/reject out-of-order data, quarantine unknown decreases
   and multi-report batches, and preserve meter baselines across gateway/HA
   restarts. A decrease alone cannot identify a legitimate reset.
4. **HA accounting:** separately named AC estimates now have persistent
   continuity and statistics semantics. Do not sum overlapping
   backup groups or silently treat AC output as battery discharge.

HA accepts estimated energy sensors; laboratory calibration is not a platform
requirement. The remaining issue is an accurate declared conversion and defined
reset/accounting behavior, rather than the Diagnostics entity category.
See [HA consumption options](home-assistant-energy-dashboard.md).

Raw requests, cumulative readings, identities, absolute timestamps and full
comparisons stay in the ignored private folder. Public findings use ratios and
relative timing only. Live native conversions, HA entity classes and station
settings are unchanged by this analysis.
