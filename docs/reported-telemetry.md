# Reported ports, runtime and component firmware

Added **2026-10-07** to HA, the terminal and the optional gateway browser.
Existing decoded values already crossed the SDK/HTTP boundary; this addition
provides model-scoped presentation and separately timed native Wi-Fi signal.

| Data | Models / source | Presentation contract |
| --- | --- | --- |
| USB-C1/C2, USB-A1/A2 and DC-input watts | Original C1000 typed TLVs | Reported power sensors; zero is valid, absent channels remain absent. No loaded-port calibration is claimed. |
| USB-C1/C2/C3, USB-A1, solar and total-input watts | C300 AC typed TLVs | Same reported-power contract; no C300 DC support or guessed Gen 2 PV-watt conversion. |
| Remaining minutes | Original C1000, C300 AC and both Gen 2 decoders | Device estimate without statistics state class. Gen 2/C300 require charging or discharging activity; labels/HA attributes distinguish time to full/empty. Original C1000 remains a generic estimate when direction is unreported. Idle, zero, unknown, negative and sentinel estimates are unavailable. |
| Current AC/DC countdown seconds | Original C1000, C300 AC, C2000 Gen 2 | No active countdown is zero. Max-uint32/invalid values are unavailable. C1000 Gen 2 saved output timeouts are not relabeled as current countdowns. No new timer controls. |
| Controller/inverter/BMS/module versions | C2000 Gen 2 F9 slots; C1000 Gen 2 module slot | Validated dotted text diagnostics. Main firmware remains in HA device information. No inference about update availability. |
| Wi-Fi RSSI dBm | C1000 Gen 2 native radio AP-info | Main **1.1.4.9**, module **0.3.3.0** only; no C2000 or original-model query. Signed integer −128…−1; zero/failure/unknown is unavailable. |

## Optional Wi-Fi observation

```sh
solix-link ap-service-serve --directory /path/to/private/ap-service \
  --wifi-rssi --web-ui
```

`--wifi-rssi` enables one guarded read-only query per eligible station every
**300 seconds**. It defaults off, works with controls disabled and has no
immediate failure retry or recovery action. The worker checks complete fresh
controller settings before/after the established radio request. Queries write
no station settings. The existing explicit `ap-service-wifi-rssi` CLI also
updates the worker's cache.

A separate sanitized `wifi_signal` object is available in HTTP/SSE and the
cached Python gateway client. It carries its own `observed_at`, firmware,
source and **600-second** expiry. Power traffic does not refresh this time.
A failed query invalidates the old observation. GET requests and HA entities
read cached data only. The signal is a HA diagnostic sensor; Prometheus emits
`solix_wifi_rssi_dbm` only with fresh station telemetry and an available
observation, plus availability and query-time gauges.

## Validation limits

Synthetic tests cover model gates, malformed values, direction/sentinels,
radio freshness, control-disabled operation, no-write request routes and
private-field removal. Browser fixtures include valid zero port power,
unknown original-model runtime, countdowns, component versions and stale RSSI.
The screenshot is [synthetic data](images/web-dashboard-reported-telemetry.png).
Portable HA and package validators are byte-compared by a contract test.

Live checks use cached decoded telemetry plus the already validated C1000
Gen 2 RSSI request. They do not calibrate watts, estimate accuracy, nonzero
countdown transitions or electrical continuity. Incrementally retained values
are reported values, not proof every field refreshed with the latest packet.
Raw snapshots and deployment data stay in the ignored private directory.
