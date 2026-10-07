# Native device energy values

SOLIX Link passively exposes Gen 2 energy uploads throughout the SDK, gateway,
CLI, terminal, browser and Home Assistant. No energy query, cloud call or
station command is added. Power-integrated [history](persistent-history.md)
remains a separate source with its own gaps and coverage.

## Collection and support

The device posts `charging_pps_series_c_0009` to the AP worker's
`/equipment/logging/upload_pb_events` endpoint. Existing uploads are decoded
passively. To request future reporting, an operator must explicitly run the
worker with `ap-service-run --energy-reports`; this option defaults off and
changes analytics point 20001 when the station next fetches it. It does not
immediately query or confirm the station's reporting flag. This implementation
does not enable reporting or restart a service automatically. The user-requested
[HA deployment](ha-runtime-validation.md#native-energy-deployment-2026-10-07)
enabled collection separately.

| Device/firmware | Interpretation |
| --- | --- |
| C1000 Gen 2 main 1.1.4.9 | Nominal Wh inferred from hash-pinned firmware; raw / 1000 gives nominal kWh |
| C2000 Gen 2, or other Gen 2 main version | Raw / 1000 is an **assumed**, uncalibrated display conversion; actual scaling remains unresolved |
| Original C1000 main 1.7.1 | `charging_pps_series_c_0002` uploads observed; [schema investigation](unused-device-data.md) is separate and no conversion is implemented |
| C300, BLE-only monitoring | No established native energy-report source; values remain unavailable |

Physical units are **unverified on both Gen 2 models**. AC input includes
charging and bypass; AC output describes delivered load energy. Neither
isolates battery charge/discharge or stored energy. The Standard, TOU and two
backup variants stay separate. They are not summed or mapped to cloud daily
categories. Raw duration fields are retained without a minutes/hours conversion.
See [source evidence](app-energy-statistics.md) and [counter epochs](gen2-energy-epochs.md).

## Public contract and persistence

Each native station snapshot has `native_energy`, or `null` when missing or
invalid. Its schema version is 1, with source `device_energy_report`:

- `groups.<mode>.raw`: recovered integer counters only; absent fields stay absent.
- `groups.<mode>.energy_kwh`: AC input/output, DC input and other output nominal
  conversions. These are reported mode counters, **not lifetime meters**.
- `units_verified: false`, `conversion_basis: nominal_wh | assumed_wh`, and the
  main firmware version if known when received.
- `reported_at`: host receipt time, **not** an MCU measurement timestamp.
  `available` requires a report younger than 1,800 seconds, allowing five seconds
  of clock skew; energy receipt never refreshes live power/MQTT freshness.
- `received_reports`, `batch_reports`, `counter_epoch` and
  `counter_epoch_started_at`: persisted observation metadata.
- `continuity`: first report, increasing/nondecreasing counters, a counter
  decrease, or an upload batch whose chronological ordering is unknown.

Each profile saves an atomic, owner-only `energy-state.json` alongside its
private AP files. Values survive worker restart. Decreases mark a new
**observation epoch**, without guessing reset, wrap or reorder. Batches retain
the last received member and mark ordering uncertainty; no deltas are credited.
Epochs are not verified device reset epochs. Corrupt, unsafe or wrong-model
state is preserved and further persistence is refused. Public validators strip
identities and unknown nested fields and recompute conversions/freshness.

## Interfaces

```sh
solix-link gateway-energy --gateway-url http://127.0.0.1:8765 \
  --gateway-token-file /private/http-token --name office
```

- **SDK:** `NativeEnergyStore`, `decode_energy_events`,
  `validate_native_energy` and `native_energy_rows` are exported from `solix_link`.
- **HTTP/SSE:** `/devices`, `/devices/{name}` and update events carry the envelope;
  `GET/HEAD /devices/{name}/energy` returns cached values using the same read scope.
- **Prometheus:** `solix_native_energy_raw` and
  `solix_native_energy_kwh_unverified` have group/channel labels; availability,
  receipt timestamp and observation epoch are also exposed. These are gauges,
  not monotonic `_total` counters; do not apply `rate()` as if resets were solved.
- **Terminal:** F7 opens Energy; line-based native status and gateway menu include
  counters and nominal kWh. No query is sent when opening the panel.
- **Browser:** Device energy counters panel shows raw values, nominal kWh,
  firmware basis and independent freshness, even when live power is stale.
- **HA:** Up to 16 optional mode/channel energy sensors are discovered after
  reports arrive. Enable the desired diagnostics in the entity registry, or use
  the integration's **Configure → Enable newly discovered native energy
  sensors** option. It defaults off and changes only new entity defaults;
  existing enable/disable choices remain in the entity registry. It does not
  request device reports, alter counters or enable statistics.
  Attributes include raw group counters, conversion basis, epochs and receipt
  time. Availability follows report freshness and successful gateway polling,
  independently of live telemetry. Missing values never become zero.

HA native sensors have energy/kWh units but **no statistics state class**.
They are not selectable as lifetime consumption in the Energy dashboard yet.
An [Integral helper](home-assistant-energy-dashboard.md) remains the existing
power-derived option. Native statistics need model-specific meter calibration,
reset/wrap/reorder and reboot-retention evidence, plus a tested persistent
statistics epoch strategy. No calibrated battery-energy claim is made here.
