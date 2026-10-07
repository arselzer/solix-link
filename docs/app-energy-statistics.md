# Where the app's kWh statistics come from

Offline review on **2026-10-07**. The Gen 2 stations already accumulate energy
on the device. The examined app chart path fetches energy totals from a cloud
service; it does not need the phone to collect every live power sample.
The precise mapping from each uploaded counter/group to the app's daily totals
remains unverified. No account, phone capture or station was accessed.

## Device-side accounting already exists

[C1000 Gen 2 main 1.1.4.9](gen2-energy-counter-investigation.md) adds the current
cached AC/DC port watts every tenth nominal one-second callback. Its general
report encodes `floor(sum / 360)`, giving **nominal Wh** for ten-second samples.
It keeps fractional-report remainders between snapshots. Thus the station is
itself performing discrete power integration; displaying Wh in kWh would
divide by 1000. Sensor accuracy and actual scheduling still affect the result.

These values are grouped by Standard, TOU and two incompletely understood
backup variants. The report is `charging_pps_series_c_0009`, uploaded through
`/equipment/logging/upload_pb_events`. An alternate app-facing `0490` producer
carries the same report as opaque bytes; it is not an established query.
[Transport](tariff-energy-followup.md), [app publication](gen2-backup-export-radio-app.md)

Earlier local captures already demonstrated this on both Gen 2 stations:

| Station | Saved evidence | Boundary |
| --- | --- | --- |
| C1000 Gen 2, main 1.1.4.9 / radio 0.3.3.0 | Standard AC input/output counters each rose by 19; covered reported power integrates to 18.38845 Wh | Compatible with nominal Wh and integer rounding; no independent meter calibration |
| C2000 Gen 2, main 2.1.6.4 | Standard AC counters each rose by 58 during a roughly nine-minute report interval | About 10% above the contemporaneous power estimate; model timing/scaling remains unresolved |

These are previously recorded observations, not new hardware tests. Do not
apply the inspected C1000 arithmetic to C2000 as if its firmware were available.
Counter reset, 32-bit wire wrap and physical retention require separate handling.
[Epochs](gen2-energy-epochs.md), [report lifecycle](energy-report-lifecycle.md)

## Actual app cloud-chart path

The retained ARM64 Flutter image has SHA-256
`8537b4f8a4da327f9bf298d99e90ad969b89f52ae7d6d68c454e5bf0ec25c070`.
Selected actual instructions and extracted object-pool constants establish:

| Function | Static finding |
| --- | --- |
| `ChargerChartHttpRepository._fetchFromServer`, `02990f58` | Calls `getChargeSessionChartModel` at `029910cc`, then `_processS1S2ChartData` at `02991190` |
| `S1S2DeviceCommand.getChargeSessionChartModel`, `02991c18` | Uses `power_service/v2/device/energy_analysis`, `device_sn`, date bounds and the `akiot.cloud_api` bridge |
| `_processS1S2ChartData`, `02991db0` | Calls the response model parser at `02991e24`, derives chart totals and loads the `kWh` label at `02991f30` |
| `S1S2ChargerSessionChartModel.fromJson`, `02993490` | Reads `pps_total`, `energy_unit`, `power_unit` and `data_trend`; calls `PpsTotal.fromJson` at `02993648` |
| `PpsTotal.fromJson`, `02993920` | Reads AC/DC consumed and charged totals from the response |

This selected path parses cloud energy history rather than integrating a phone
power stream. It does not establish which page every model/firmware uses, nor
prove the server's calculation or how it handles missing uploads. Generic app
support does not establish local counter support on the original C1000.

## Independent API evidence

The API library at commit **`e97113abaf18eeb7a3923260749f80c86562601c`** implements
`device_energy_analysis()` for `power_service/v2/device/energy_analysis`.
Its documented response includes `energy_unit: kWh`, daily `data_trend`, and
`pps_total` fields including `ac_consume_total`, `dc_consume_total`,
`ac_charging_total`, `dc_charging_total` and `pv_input_total`. Unsupported
models may fail; no request was made with any user's device identifiers.
[Source](https://github.com/thomluther/anker-solix-api/blob/e97113abaf18eeb7a3923260749f80c86562601c/src/anker_solix_api/energy.py),
[Endpoint](https://github.com/thomluther/anker-solix-api/blob/e97113abaf18eeb7a3923260749f80c86562601c/src/anker_solix_api/apitypes.py)

## Local implementation boundary

SOLIX Link already decodes these uploaded reports into private `energy_report`
records when opt-in AP reporting is enabled with `--energy-reports`. This is
an HTTP reporting path alongside native MQTT, not an incoming MQTT energy
query. Reporting is off by default; enabling it changes the station's analytics
flag when it next fetches the point-switch endpoint. Nothing was enabled here.

The SDK keeps recovered fields as `*_energy_raw` with `units_verified: false`.
Next, map report groups/channels to the app categories, check Wh-to-kWh scaling
per model, preserve counter epochs and persist validated deltas. Do not sum
backup groups or label AC input as battery-only charging without evidence.
Device counters could cover intervals the gateway did not observe **if their
continuity and retention are established**. Power-integrated history remains
a separate fallback. See [HA Energy requirements](home-assistant-energy-dashboard.md).

Verification: four exact ARM64 direct calls and seven selected pool constants
were checked against the hash-pinned app; API endpoint/response-unit source
assertions passed. This is static analysis, not app execution or CPU replay.
**41 existing energy decoder/analysis tests passed in 0.06 seconds** using
synthetic inputs. Vendor source, disassembly and result metadata remain private.

Pinned API source SHA-256: `energy.py`
`ac59ee0be8fc9fedbba785bb7c014569b14335210c899ae760e02ed1f9f1370a`;
`apitypes.py`
`92e562d319374c70b077b79792888909bd6b78fcc04da11c6359461f9c4fb119`.
