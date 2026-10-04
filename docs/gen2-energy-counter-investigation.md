# Gen 2 energy counters: arithmetic, timing and reporting

## Result and scope

Offline instruction replay on **2026-09-30** strengthens the interpretation
of the C1000 Gen 2 general energy report as **nominal Wh accounting**, but does
not establish calibrated physical Wh or lifetime-counter guarantees. Keep
SDK fields named `*_energy_raw` and `units_verified=False`.

The later [epoch follow-up](gen2-energy-epochs.md) executes explicit reset and
report-width wrap boundaries and adds pure offline comparison helpers. It does
not resolve physical scaling, actual reset events or MCU snapshot timestamps.

The executed input is **A1763 main 1.1.4.9**. Radio **0.3.3.0** was also
inspected statically. The C2000 **2.1.6.4** timing comparison below uses an
already retained capture; its firmware remains unavailable. The later C1000
read-only capture is distinguished below. This offline analysis itself made no
station connections, commands, cloud calls or firmware changes.

This extends [report format](tariff-energy-followup.md) and
[report lifecycle](energy-report-lifecycle.md), rather than identifying a
new incoming query. No `0401` query or `0503/state_info` conversion is proposed.

## The accumulated power is the telemetry power

General accumulator **`0802df3c`** uses a four-group accounting block at
**`200032c0`**, with 48 bytes per group. Each group contains four unsigned
64-bit energy sums, then four unsigned 32-bit consolidated durations.

| Sum offset | Source getter | Corresponding report field |
| --- | --- | --- |
| `+00` | `0801a724(5)`, input port 5 | AC input, nested tag 3 |
| `+08` | `0801a724(6)`, input port 6 | DC input, nested tag 7 |
| `+10` | `0801a738(5)`, output port 5 | AC output, nested tag 4 |
| `+18` | `max(total_output, ac_output) - ac_output` | Other output, nested tag 8 |

The offsets are hexadecimal. Getters read uint16 values directly from the
seven-port table at **`20002198`**. Input and output setters are `0802b778`
and `0802b788`; total output getter `0801a76c` sums the seven output slots.
The full A7 serializer **`08017ee0`** calls the **same** input/output getters
for AC power. Twelve scenarios execute the actual stores, getters, A7 builder
and accumulator, including maximum uint16 values and all four accounting
groups. There is no additional scale factor between these paths in this image.

The main controller's AC producer `0800ce98` reads signed DSP status words
at `20003f00+12` and `+1e`. Selected branches apply low-power suppression,
charging minima or replace input with output in bypass. These producer
branches were inspected, not replayed here. This investigation does not
calibrate the DSP sensors. DC-input and other-port producers likewise require
separate physical validation.

## Sampling is discrete, without elapsed-time compensation

The accumulator increments byte **`2000024a`** on every callback. On the tenth
callback it clears that byte and adds the **current cached port power once**
to each selected sum. It does not average the preceding nine callbacks,
check a power-value age, or multiply by an elapsed-time measurement.

Actual-instruction examples:

- Nine callbacks at 1000 followed by one at 0 add **0**.
- Nine callbacks at 0 followed by one at 1000 add **1000**.
- At constant power, repeated report construction leaves the sums intact;
  3600 callbacks produce 360 samples and preserve all sub-unit remainders.

Thus dense host telemetry averaging and the station's discrete sampling can
differ for varying loads. A stale port-table value can continue contributing
until a producer changes it; this accumulator alone supplies no freshness gate.

### Scheduler semantics

Initialization **`080175fc`** registers the repeating accumulator callback
with period **1000** through `080107f4`. Timer polling **`0801089c`** compares
unsigned `now - last_fire` with the period, calls a due callback **once**, and
sets `last_fire = now`. It does not execute multiple missed callbacks to catch up.

Four replay sequences run the actual timer poller, actual tick getter
`08010eb0`, and actual energy callback using synthetic timer records and ticks:

- Ten callbacks over 10000 ticks at regular intervals produce one sample.
- Ten callbacks over 25000 ticks at 2500-tick intervals also produce one sample.
- A large gap produces one callback at the next poll, not one per missed period.

The tick getter reads **`200008d0`**. SysTick vector 15 points to `08010561`;
handler `08010560` increments that word once per interrupt. Configuration
**`0802d048`** loads the core-frequency variable at **`200008cc`**, writes
`frequency // 1000 - 1` to SysTick LOAD, clears VAL, and writes CTRL=`7`
(core clock, interrupt and counter enabled). Four additional scenarios execute
this configuration and ISR arithmetic against synthetic registers, substituting
interrupt-priority setup and housekeeping.

This establishes an explicit **nominal 1 ms tick and ten-second energy sample**
in the firmware design. Real oscillator calibration, correctness of the
core-frequency variable, sleep behavior and event-loop latency were not
measured. Late scheduling would undercount elapsed energy under a constant
positive load; it does not itself establish an overcount explanation.

## Exact report conversion and duration carry

Builder **`0802db30`** divides each 64-bit sum by **360** using the actual
unsigned-division routine `0800516c`, then stores the quotient's low 32 bits
into the protobuf object. The original descriptor and encoder **`08022a14`**
run in the replay. Fifteen boundary scenarios cover values around 360,
2^32 and the encoded counter's eventual 32-bit wrap.

For sum `S`, the encoded energy value is:

```text
floor(S / 360) modulo 2^32
```

There is no per-sample Wh rounding. The integer sum retains fractional-report
units between snapshots, and constructing a report does not reset it. If each
sample represents ten seconds and port values represent watts, `S / 360` has
units of Wh. Those timing and calibration premises still need physical checking.

Before a reset or width wrap, the difference of two encoded snapshots differs
from `(S2 - S1) / 360` by **strictly less than one report unit**. All 360 possible
starting remainders and five increments satisfy this bound in 1800 mathematical
checks. These checks use the independently executed division rule; they are
not additional firmware executions.

Durations follow a separate path: enable flags add one sample to working
counters beyond the persistent block, and every global 60-sample boundary
consolidates `working // 60`, retaining `working % 60`. Six boundary scenarios
verify that remainder carry. Nominal duration units are ten minutes. These are
**enable durations**, not proof that power was nonzero throughout the interval.
Reporting cadence, duration consolidation and energy addition are distinct.

## Radio timestamps and the retained C2000 discrepancy

Radio route `420462b4..420464f2` extracts the MCU binary payload and base64
encodes it through `4201b404`. Logging function `420211f6` passes that string
to JSON builder **`42022304`**. The latter adds `params.payload`, creates
`local_time`/`utc_ts` at **`4202246e..420224a4`**, and sends the logging event.
It does not decode or rescale the energy fields. This continuation is static
evidence; the new suite does not emulate radio JSON or HTTP. The earlier route
replay is documented in the report-format investigation.

Those timestamps describe **radio event construction**, not necessarily the
instant at which the MCU counters were read. In the retained C2000 pair:

| Observation | Difference |
| --- | ---: |
| HTTP receipt times | 540.0046 seconds |
| Body `utc_ts` values | 540 seconds |
| Standard AC input and output counters | 58 raw units each |
| Estimate from intervening host power samples | About 52.6 Wh |

Varying HTTP delivery delay therefore does not explain this pair. If C2000
uses the recovered C1000 division, integer-report truncation alone cannot
explain the roughly 5.4-unit difference either. MCU queue delay, snapshot
timing, discrete sample alignment, load variation and model differences are
still unresolved. A nominal ten-minute calculation matching 58 does not prove
that the report measures ten minutes when both observed timestamps span nine.
Raw requests, identities and absolute timestamps remain private.

## C1000 Gen 2 read-only observation: one 370-second interval

A separate local capture on **2026-09-30**, using C1000 Gen 2 **main 1.1.4.9 /
module 0.3.3.0**, adds a bounded live comparison. The station reconnected to its
saved isolated-AP profile in **13.52 seconds**, without BLE provisioning or a
button press. The service requested energy reporting and read status; this
capture issued no actuator commands. It produced two cumulative energy reports
and 140 requested status rows during the nominal 720-second capture.

The analysis decodes the two original API bodies privately, checks that each
matches its separately logged `energy_report`, and uses the bodies' radio
`utc_ts` values as the integration boundaries. **Only the difference between
matching counters is interval data.** The first report's existing cumulative
values are excluded from the published result and are not interpreted as
energy consumed during this connection.

### Sample coverage and integration

The requested status rows span **702.363 seconds**. Their first row is
**7.770 seconds after the first radio energy timestamp**, so those rows alone
cannot bracket the whole report interval. Retained native MQTT publications
provide the missing observation: a power reading arrived **7.532 seconds before**
the first radio timestamp. The full log contains 281 power-bearing publications
across 717.665 seconds, from one device, with no retained publications used.

For the 370-second report interval, 144 interior readings plus two boundary
brackets supply the calculation. Power is linearly interpolated at the two
radio timestamps, then integrated by the trapezoidal rule between those
boundaries only. There is **no extrapolation**, inclusion of power outside that
interval, or substitution of a nominal ten-minute reporting period. The widest
bracketing gap is **15.187 seconds**, spanning the start; subsequent packets
provide denser observations. Packet arrival timestamps do not establish the
precise MCU sample age.

| Observation | AC input | AC output |
| --- | ---: | ---: |
| Standard counter increase | 19 raw units | 19 raw units |
| Radio `utc_ts` difference | 370 seconds | 370 seconds |
| Power range in boundary brackets | 178–180 W | 178–180 W |
| Weighted mean reported power | 178.9147 W | 178.9147 W |
| Trapezoid integral, assuming reported watts | 18.38845 Wh | 18.38845 Wh |
| Raw delta minus nominal Wh estimate | +0.61155 | +0.61155 |

HTTP receipt timestamps differ by **370.00318 seconds**. The two receipt times
are respectively 0.87750 and 0.88068 seconds after their radio timestamps.
Repeating the interpolation over those receipt boundaries gives **18.38841 Wh**
for each direction. Delivery timing therefore has a negligible effect on this
particular comparison.

All power observations within the radio interval show battery 100%, idle,
Standard mode, no active tariff, mains present and AC output enabled. The
Standard AC output-duration counter increases by one; all other duration and
energy counters are unchanged apart from the two AC energy increments. That
duration increment can reflect an existing consolidation remainder; it does
not establish a ten-minute observation interval.

### Interpretation

The **0.612-unit difference is compatible with the independently proven
less-than-one-unit difference-of-rounded-reports bound** under nominal Wh
accounting. Discrete ten-second sampling and unknown MCU snapshot timing are
additional limitations. This single interval supports the nominal Wh reading
for this model; it does not independently calibrate the DSP sensors, verify
physical energy accuracy, or resolve the earlier C2000 discrepancy. No scale
factor is fitted and **`units_verified=False` remains required**.

The [sanitized observed result](../tools/firmware_analysis/observed_results/c1000-gen2-energy-20260930.json)
contains deltas, relative timing, sampling coverage and the integration method.
It is a live observation, separate from the synthetic replay fixtures. Raw
requests, payloads, identities, credentials, absolute timestamps and initial
cumulative counters remain private.

## Reproduction and limitations

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/emulate_energy_counters.py \
  --output /tmp/gen2-energy-counter-results.json
```

The tool uses the published exact-hash image, or `SOLIX_FIRMWARE_DIR`.
**49 instruction scenarios and 1800 mathematical checks pass**, using
Python **3.12.3**, Unicorn **2.1.4**. The
[synthetic result](../tools/firmware_analysis/expected_results/gen2-energy-counters.json)
and [reproduction manifest](../tools/firmware_analysis/expected_results/gen2-energy-counters-manifest.json)
contain no captured station data.

Substitutes are ancillary pre-update statistics, persistence, backup-state
pointer, selected diagnostics and dynamic protobuf callbacks. Port values,
RTC, mains GPIO, timer records, SysTick registers and scheduler ticks are
synthetic. SysTick interrupt-priority setup and housekeeping are substituted.
Main power
getters/stores, A7 serialization, accumulator arithmetic, duration carry,
timer polling, SysTick configuration/ISR increment, integer division and
protobuf counter encoding execute from the firmware. Peripheral writes are
rejected except the three explicitly captured synthetic SysTick registers.
This does not emulate analog power,
radio authentication, persistence hardware or complete device operation.

## Next useful passive check

On each model separately, retain several consecutive reports during a steady
load and compare their **radio `utc_ts` deltas**, raw counter deltas and densely
sampled input/output power. Keep report receipt timestamps too. A report's
first interval can straddle existing accounting and pending-queue state; later
steady intervals are more informative. Investigate counter persistence and
reset epochs before defining HA `total_increasing` energy sensors. Do not
silently fill telemetry gaps or equate an enabled duration with energy use.
