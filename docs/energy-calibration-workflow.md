# Guided counter/reference comparisons

`solix-link energy-calibration` records independent **cumulative AC-meter kWh**
beside one fresh cached native counter upload. It sends no station command,
does not restart devices, and never applies a fitted conversion to HA statistics.
The pure Python API is portable; CLI session writes use POSIX file locking.

## Record a private session

Create an owner-only session directory automatically:

```sh
solix-link energy-calibration create \
  --session .solix-private/calibration/c1000-gen2.json \
  --model c1000_gen2 --counter standard.ac_input_energy_raw --reference ac_input
```

Use an independent meter at the selected **AC input** or **AC output**, not the
station's watts, an HA integration of those watts, or battery SOC. Request the
cached station snapshot and enter the meter reading when prompted:

```sh
solix-link energy-calibration record \
  --session .solix-private/calibration/c1000-gen2.json \
  --gateway-url http://127.0.0.1:8765 --gateway-token-file /private/http-token \
  --name office --guided
```

Repeat after a stable-load interval, then compare:

```sh
solix-link energy-calibration report \
  --session .solix-private/calibration/c1000-gen2.json --candidate-wh-per-unit 1
```

For scripted/offline records, use `--snapshot-file snapshot.json`, `--meter-kwh`
and `--meter-timestamp` (UNIX seconds). Guided input requires a terminal. Only
cached HTTP GET is used; no BLE request or forced upload is available. If an
upload is stale or already recorded, wait for the next passive report. Meter
readings and the device report must be close in time; a long prompt can expire
the snapshot.

## Supported evidence

| Model | Counter selection |
| --- | --- |
| Original C1000 | `counter_1_raw` through `counter_8_raw`; units/channel meanings remain unknown |
| C1000 Gen 2 / C2000 Gen 2 | `standard` or `time_of_use`, followed by one of the four decoded `*_energy_raw` channels |

One session selects one counter and reference location. A reference location is
an operator declaration, not proof the selected counter measures that channel.
Input/output AC meters include bypass power and do not measure battery energy.
Original counters may include durations; a ratio under one load does not prove
an energy unit. Expansion/backup-mode totals are not inferred.

Sessions retain only model, validated version, selected counter, numeric raw
values, upload/event/reference times, reported mode, observation epoch and
explicit boundary labels. Names, accounts, serials, credentials and arbitrary
snapshot strings are omitted. Files and locks are owner-only; create refuses to
overwrite, unsafe/symlink destinations are rejected and writes are atomic.
Each session is bounded to 256 observations.

## Boundaries and interpretation

Capture requires fresh connected native telemetry and a **new, single upload
less than 30 seconds old**. Comparisons exclude intervals crossing observed
counter decreases, epoch/version/mode changes, explicit reset markers, reversed
clocks, missing event times, endpoint timing mismatches or report gaps. No wrap
correction or missing-energy interpolation is performed. Use `--boundary
device_restart`, `meter_reset`, `mode_change` or `unknown` to label a physical
event you actually observed; that option performs no action itself.

Default limits are a one-hour report gap and 30-second endpoint offset. A report
provides interval deltas, candidate Wh per raw unit and candidate-scale residuals.
Neither a median nor a plausible residual marks units, channel mapping, MCU
measurement timing, reboot retention or battery/lifetime energy as verified.
The existing runtime conversions are untouched.

Physical qualification still needs repeated independent input/output intervals
at different stable powers, meter resolution/accuracy evidence, known modes,
and before/after ordinary restarts on noncritical equipment. Gateway restarts
do not establish station-counter retention. Keep the server-backed C2000 output
and settings unchanged during calibration work.
