# Additional controls and data — 2026-10-08

## C2000 Gen 2 screen timeout

The screen timeout is now exposed in the native MQTT worker, CLI/terminal,
browser and HA selector, with **30 and 60 seconds only**. HA also supports its
already established Prime BLE route. Power, cap, reserve and TOU controls remain
separate from output switching; C2000 AC/DC switching stays excluded.

Evidence is deliberately split:

- **Physical BLE:** the existing 30 → 60 → 30 trial verified the scalar write
  and A4 bytes 16–17 on main **2.1.6.4**.
- **Native implementation:** established Gen 2 `0103` envelope, A4 typed uint16,
  controller-source and timestamp fields. This particular native setter is
  **inferred from the BLE route and covered synthetically, not physically replayed**.
- **Guards:** exact main version; complete typed A4, D9 and output reports;
  inactive AC/DC countdowns; readback matching the requested scalar, all other
  A4 bytes, saved D9 state, AC/DC output states and mains presence. Unknown
  changes or a lost result fail without retry or automatic restoration.

HA attributes and the browser indicate the pending native hardware test. No
C2000 station-setting experiment was performed for this change. Its temperature,
idle timeout, Fast, memory and Smart setters still need model-specific command
and preservation evidence. Finite idle timeout and Smart can interrupt power or
remote access; they are not enabled by copying C1000 handlers. C2000 firmware
has not been recovered, so A1763 instruction replay cannot fill that gap.

## Expansion packs

Original C1000 reported expansion SOC and temperature now reach HA, terminal
and browser only with an explicit supported pack-present value. Removal makes
existing SOC/temperature entities unavailable. Absent packs do not create zero
SOC sensors. C2000 exposes **presence only**: its decoded flag does not establish
arbitrary pack counts or expansion SOC/temperature.

Unknown presence values are unavailable, not false absence. SOC accepts integer
0–100%, temperature −40–125°C. These are presentation bounds for reported data,
not calibration or expansion hardware validation. Generic telemetry age cannot
prove individual retained BLE fields were refreshed; native status packets
decode their own reported fields. Expansion health/state bytes remain raw and
are not relabeled as measured health.

## Original C1000 numeric reports

The AP now accepts original `charging_pps_series_c_0002` uploads separately from
Gen 2 `_0009`. Public snapshots contain `original_counters`: host receipt time,
radio construction time, firmware metadata and field 19's **eight numbered raw
counters**. Identifiers, unknown strings/blocks and unrelated metadata are omitted.
The upload never refreshes station telemetry or enters `NativeEnergyStore`.

The numeric layout is supported by the bounded actual encoder replay of public
**main 1.5.9**, SHA-256
`b295ee8613f5c96e70dcc905896df516621cab4dc590bb580eac6b84519911a6`.
[Encoder findings](unused-device-data.md#original-c1000-encoder-proof) and
[synthetic replay output](../tools/firmware_analysis/expected_results/c1000-energy-report.json)
describe the division and source offsets. The deployed **1.7.1** field shape
matches; its encoder instructions and electrical units have not been verified.

The API/SDK, terminal, browser and Prometheus expose numeric diagnostics; HA
discovers eight **disabled-by-default, unitless diagnostic sensors** when fields
arrive. They have no energy device class or statistics state class. Upload age
expires independently after two hours. Multi-event batches retain every event
but provide no fabricated latest value because their internal order is unknown.
Browser/Prometheus values beyond the exact integer range are omitted/unavailable.
Duplicate known fields and malformed types are rejected.

These are not kWh, calibrated port energy or lifetime totals. Port identities,
scaling, scheduler timing, reset/reboot behavior and retention still require
model-specific measurements. Existing AC kWh estimates remain unchanged.
C2000's additional report blocks 14/15/22/23/24 and backup field 9 remain
unmapped; unknown nested strings can contain private identifiers and are not
published as speculative sensors.
