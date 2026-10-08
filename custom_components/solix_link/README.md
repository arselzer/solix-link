# SOLIX Link for Home Assistant

Custom integration for a separately running **local SOLIX Link HTTP gateway**.
Home Assistant uses the gateway connection; it does not open a second Bluetooth
connection or contact Anker. This is an unofficial integration.

## Install and configure

1. Run the repository's gateway and verify its `/devices` endpoint. Enable its
   authenticated control option if you want to change station settings.
2. Copy this whole `solix_link` directory into
   `<Home Assistant config>/custom_components/solix_link/`, then restart HA.
3. In **Settings → Devices & services → Add integration**, select **SOLIX Link**.
4. Enter the gateway URL, such as `http://gateway.local:8765`, and its bearer
   token. The token is required for commands. An unauthenticated gateway can
   be monitored with an empty token.

Use the integration's **Reconfigure** action to change its address or token.
Authentication failures start a reauthentication flow. Use HTTPS with a trusted
certificate when the local network is not trusted; tokens travel in an HTTP
Authorization header. Treat HA configuration backups as containing credentials.

## Entities and actions

Optional [owned price charging](../../docs/owned-price-charging.md) adds a
preview-by-default service and disabled blueprint for C1000 Gen 2. Persistent
ownership, reserve guards and uncertainty latches prevent automatic takeover
of existing plans. C2000 and original C1000 remain excluded from this executor.

[Additional controls/data](../../docs/additional-controls-and-data.md) include
C2000 screen timeout (native hardware test pending), expansion presence-gated
SOC/temperature and disabled, unitless original counter diagnostics.

Reported USB/DC/solar power sensors, guarded remaining-time estimates, current
AC/DC countdowns and component firmware diagnostics are discovered only for
models that report them. Native C1000 Gen 2 Wi-Fi signal is optional: enable
`--wifi-rssi` on `ap-service-serve` for five-minute read-only queries with
independent ten-minute expiry. HA itself reads cached HTTP data only. See
[reported telemetry](../../docs/reported-telemetry.md) for scope and limits.

- **Blocked controls** diagnostic: cached permission/prerequisite reasons with
  a count for the configured model/transport; requires an updated gateway.
  The client supplies expected settings and a request ID when supported,
  sends one POST and never retries an uncertain write automatically. See
  [control readiness and coordination](../../docs/control-readiness-and-coordination.md).
- Binary sensors: mains present and AC output enabled, when explicit 0/1
  telemetry is available. Unknown/stale values become unavailable. The Gen 2
  mains transition was verified by unplug/replug on C1000; C2000 is checked
  against baseline/reference data without unplugging its server supply.
- C1000 Gen 2 read-only **Disaster preparation active** diagnostic, from D9.
  Unknown values remain unavailable; this is not a complete plan exporter.
  The prepared adaptive surplus blueprint requires inactive readback.
- Sensors: battery percentage/status, temperature, AC/DC/total output power,
  AC input power, usage mode, active tariff and observed AC power source, when
  reported by the gateway. Native Gen 2 Usage mode also exposes validated
  `saved_tou_plan` and independently checked `saved_tou_plan_fresh` attributes;
  missing readback remains unknown. Power sensors support HA statistics and can feed
  HA's Integral helper.
- Optional native Gen 2 energy diagnostics: separate mode/channel counters in
  nominal kWh, with raw values, firmware basis, receipt time and observation
  epochs in attributes. Discovered only after AP energy uploads arrive, disabled
  by default, and independently stale after 30 minutes. Units/reset behavior
  remain unverified; no lifetime statistics state class is supplied. See
  [native energy collection and limits](../../docs/native-energy-values.md).
- Optional history diagnostics: estimated AC input/output kWh, coverage and gaps.
  These currently have no energy statistics state class. See
  [Energy-dashboard options and remaining work](../../docs/home-assistant-energy-dashboard.md).
- Numbers: AC charging power limit, charge cap and backup reserve. A number is
  created only for a supported model when telemetry exists and the gateway
  advertises that command. Charging-power ranges are original C1000/A1761
  100–1000 W, C1000 Gen 2 100–1200 W and C2000 Gen 2 300–1800 W, in 100 W
  steps. Original C1000 charging-power changes/restoration passed library and
  HTTP tests on version code 151; other range values and physical enforcement
  remain untested.
  C1000 Gen 2/main 1.1.4.9 native MQTT accepted 100 W and charged from 95% to 96% over
  90 seconds with roughly 290–293 W input and 179–181 W output. A later 200 W
  trial charged 97→98% with 389–390 W input and 180–181 W output; both lower
  values also passed Prime readback/restoration. These are station readings,
  not calibrated wall-meter or battery-current measurements.
  C300 charging power has discrete choices (including 330 W); this integration
  does not expose a charging-power number for it. Reserve limits follow the
  current charge caps; native MQTT supports reserve on both Gen 2 models.
  Original C1000 support adds no charge-cap/reserve controls.
- Selects: original C1000 legacy/Prime BLE/native MQTT and C1000 Gen 2 native MQTT temperature
  display (Celsius/Fahrenheit); C1000 Gen 2 native MQTT lower discharge limit
  (1%, 5%, 10%, 15%, 20%). Available limits leave at least
  five percentage points below the current backup reserve; selecting a limit
  does not adjust the reserve. The temperature sensor continues to report Celsius.
- Optional C1000 Gen 2 native/main 1.1.4.9 **clock window brightness** selects:
  Normal/High for each saved window, disabled by default. Require Standard mode,
  clock disabled, transfer idle and inactive output countdowns. Both flags passed
  native changes/restoration; visible brightness is unverified. See
  [HA entity gates](../../docs/ha-clock-brightness.md).
- Original C1000 Prime/native MQTT/main 1.7.1 configuration selects also expose brightness,
  screen timeout (20/30/60/300/1800 seconds) and Light (Off/Low/Medium/High/SOS).
  Brightness 1/2, screen timeout 30/60 s, Off/Low and Celsius/Fahrenheit passed
  live readback/restoration with AC enabled and DC disabled. Other enum values
  have synthetic/range coverage. The gateway protects all eleven fresh settings
  and complete `F8` flags. Output switches remain absent from HA.
  Generated-ID pairing remains unverified.
  Original **BLE Prime/native MQTT** adds the DC power-saving configuration switch,
  with fresh DC output OFF required in both directions. Prototype and public
  SDK each passed two writes/nine samples with AC on, DC off and complete
  baseline restoration. Only its F8 mode byte may change. Smart may inherit an
  inactivity counter and later turn DC output off at low load; enabling does
  not guarantee a new grace period. Prime and native MQTT independently expose
  nine gateway preferences, including the Fast flag and both Smart modes.
  The native DC Smart prototype also passed two writes/fifteen fresh samples
  with complete settings/F8 restoration, AC on and DC off. C1000 Gen 2 native
  MQTT has a separate main-1.1.4.9 guard requiring DC off and both countdowns
  inactive; its actual HA switch passed OFF→ON→OFF with complete restoration.
  C2000 DC Smart remains unsupported.
  A public SDK repeat passed two writes/eleven explicit fresh snapshots plus
  the setter's internal fresh baseline/confirmation reads, with the same restoration.
  Native MQTT/radio 0.3.3.0 initially confirmed six preferences with 12 writes,
  restoration and three matching final samples. Its gateway requires fresh
  telemetry; a packaged AP service/SDK repeat passed 14 writes and restoration.
  Only validated commands are advertised. Missing mains, battery activity
  and supply-source fields remain unknown. The
  [HA 2026.7.4 deployment trial](../../docs/ha-runtime-validation.md) also verified
  an original-C1000 screen-timeout change/restoration through HA itself.
- Display configuration selects: C1000 Gen 2 native MQTT brightness
  (Low/Medium/High) and screen timeout (Never, 10/20/30/60/300/1800 seconds).
  Brightness 1→2→3→1 and screen timeout 30→60→30 passed fresh readback and
  baseline restoration on main 1.1.4.9, with AC output enabled. Other timeout
  choices remain covered by firmware/range tests; luminance was not measured.
- Output port memory configuration switch: C1000 Gen 2 native MQTT, with
  fresh binary readback. Off clears output-recovery bookkeeping; turning On
  does not restore that transient state. The 1→0→1 stored-setting trial passed
  on main 1.1.4.9 while AC output stayed enabled.
- Device Timeout select: original C1000 legacy/Prime BLE/native MQTT and C1000 Gen 2 Prime/native
  MQTT, when advertised with fresh integer readback. Choices are Never and
  30/60/120/240/360/720/1440 minutes. Never disables this timeout; independent
  sleep behavior can still interrupt access. Finite choices may turn the
  station off when idle. BLE writes/readback passed, as did original C1000's
  native timeout checks above; Gen 2 native timeout has synthetic tests only.
  See [versioned findings](../../docs/device-timeout-behavior.md).
- Disabled diagnostic entities: raw DC/PV input activity/power and controller
  error code for C1000 Gen 2, plus the raw Gen 2 battery compatibility byte.
  That byte is hardcoded 100 on A1763/main 1.1.4.9; it is not measured health.
  PV power units remain unverified, so no power class/statistics are assigned.
  C1000 Gen 2 also exposes a disabled PV weak-light-lock binary diagnostic
  from the strict firmware-derived A3 flag. Its physical PV behavior is untested.
  Additional read-only diagnostics show C1000 Gen 2's saved AC output frequency
  (50/60 Hz), Gen 2 AC/DC Smart flags and C2000's unproven raw frequency byte.
  These diagnostics do not expose frequency controls or measure frequency
  or predict output state. See [the readback audit](../../docs/gen2-preference-readback.md).
- Configuration switch: C1000 Gen 2 native MQTT off-grid alert preference.
  Setting storage and readback were verified; actual alert delivery is untested.
- Enabled diagnostic **Last telemetry** timestamp on every station uses the
  gateway report time, rather than HA state-change time. C2000 native MQTT also
  provides a read-only **Fast charging enabled** flag. See
  [freshness and recovery](../../docs/ha-recovery-contract.md).
- Optional [price/solar charging blueprint](../../docs/home-assistant-charging-automation.md)
  requires those freshness/Fast entities and starts disabled. It changes saved
  watts and may raise reserve; it does not pause charging or enforce an AC input
  budget. The blueprint passed actual HA 2026.7.4 schema validation.
- Fast-charging configuration switch: original C1000 legacy/Prime BLE/native MQTT, or C1000 Gen 2
  Prime BLE/native MQTT. Enabling on Gen 2 requires reported Standard mode with
  no active tariff; native MQTT also requires connected mains. Turning it off
  remains possible while a tariff is active. The gateway confirms the stored
  flag; that alone does not establish the actual charging rate.
  Original main 1.7.1 Prime OFF→ON, at least twelve seconds of flag retention,
  OFF restoration and all protected settings/F8 passed at 100% SOC with AC on.
  Input removal cleared its flag. Use adequate AC supply; charging speed and
  reboot persistence remain unverified. Original native `005e` OFF→ON→OFF
  independently passed two writes/twenty explicit fresh snapshots, including
  four held samples over at least twelve seconds at 100% SOC; AC on/DC off and
  all eleven protected preferences/F8 were restored. Original native Fast
  does not require unavailable Gen 2 mains/mode/tariff telemetry.
  Its public SDK repeat passed two writes/sixteen explicit full fresh snapshots,
  plus internal baseline/confirmation reads, with the same restoration.
  See [native Fast validation](../../docs/c1000-native-fast-validation.md).
  Native MQTT flag change/restoration passed on C1000 Gen 2 firmware 1.1.4.9
  at 100% SOC; actual charging power remains unverified.
- AC/DC power-saving configuration switches: original C1000 legacy BLE;
  Prime BLE/native MQTT AC Smart additionally requires fresh AC output off and
  a strictly zero inactive AC countdown in both directions. DC Smart requires
  DC output off on either transport. The Prime AC prototype and
  public SDK each passed four writes/twenty explicit complete fresh snapshots,
  full eleven-setting/F8 restoration and unchanged upstream baseline. Smart
  may inherit an inactivity counter; enabling does not guarantee a grace period.
  Native AC Smart passed two prototype writes/23 complete snapshots. Its public
  SDK repeat passed two mode writes/35 explicit complete snapshots, with two
  separate private `004a` output setup/restoration writes. All eleven original
  preferences/F8 and the upstream baseline were restored. See
  [native AC Smart validation](../../docs/c1000-native-ac-smart-validation.md).
  The HTTP/HA integration provides no output-switch API.
  C1000 Gen 2 native/main 1.1.4.9 also exposes AC Smart with fresh AC off and both
  AC/DC countdowns inactive. Its SDK trial confirmed OFF→ON→OFF and restored
  AC output through the separate private operator path. HA discovers the switch
  in its safe AC-off state; it becomes unavailable while AC is on. C2000 is excluded.
  See [versioned trial](../../docs/c1000-gen2-clock-ac-smart-validation.md).
  **Power saving may automatically turn the output off at low load.** Review
  connected loads before enabling these switches or using them in automations.
  The integration sends semantic enabled/disabled values and waits for readback;
  stored settings do not prove the station's automatic low-load behavior.
  Original C1000 temperature, fast-charge and both power-saving setters passed
  readback and baseline restoration on firmware version code 151.
- **Return to grid**: C1000 Gen 2 and C2000 Gen 2 native MQTT. The gateway checks actual
  power flow; changing the displayed mode alone is insufficient confirmation.
- Action **`solix_link.set_tou_plan`**: select the HA device and supply `enabled`
  plus up to six whole-hour, non-overlapping periods. Both Gen 2 models are
  supported through native MQTT:

  ```yaml
  action: solix_link.set_tou_plan
  data:
    device_id: YOUR_HOME_ASSISTANT_DEVICE_ID
    enabled: true
    periods:
      - tariff: off_peak
        start_hour: 0
        end_hour: 24
  ```

Tariffs are `peak`, `mid_peak`, `off_peak`. Split overnight intervals at midnight.
Peak may discharge the battery with mains connected. The action replaces the
whole plan. `enabled: false` stores a Standard-mode plan but does not independently
confirm grid supply; use **Return to grid** when that is the intent. Fast charge
must already be off. Only single all-day plans have been exercised on C2000
hardware; clock interpretation and multi-period operation need further validation.

## Availability and failure behavior

Use **Download diagnostics** in the integration's menu for cached model,
firmware, freshness, supported controls and allowlisted telemetry. Station names,
gateway URL, entry/device IDs, credentials and raw captures are omitted from
the integration report. It sends no device request or command. HA adds its
standard system/integration metadata to the download; review that wrapper before
sharing. Diagnostics export and credential omission passed the actual HA 2026.7.4
HTTP endpoint and twenty-one focused privacy/staleness tests, including failed
setup without a coordinator. See
[HA's diagnostics documentation](https://developers.home-assistant.io/docs/core/integration/diagnostics/).

One coordinator polls every five seconds while idle; a command pauses polling.
Stale/disconnected stations and
missing metrics become unavailable. Commands re-read the station and require
telemetry no older than 30 seconds, a token and an advertised capability.
Controls are serialized with polling, range-checked and never retried
automatically. A failed or timed-out command triggers a status refresh because
settings may already have changed; a timeout does not cancel or roll back a
device operation. HTTP deadlines include the gateway's worker budget, its
five-second RPC margin and ten seconds for HTTP overhead: Return to grid uses
175 seconds for its 30-second per-phase confirmation setting, schedule changes
135 seconds, and other commands 60 seconds. Connection establishment remains
limited to ten seconds. No AC-output switch, output-countdown or firmware control is
exposed. Existing gateway safety checks remain authoritative.

Identity uses the original configured gateway endpoint plus its saved station
name, without serial numbers or account IDs. Keep names stable; renaming a
gateway station creates new entities. Reconfiguring the same gateway address
preserves its existing identities. Do not point an existing entry at a different
gateway. New stations/capabilities are discovered during polling.

## Development and verification

Contract tests use synthetic data and an ephemeral loopback HTTP server:

```sh
python3 -m pip install pytest aiohttp PyYAML Jinja2
python3 -m pytest home_assistant_tests -q
python3 -m compileall -q custom_components/solix_link
```

The standalone tests verify HTTP contracts, parsing, privacy filtering, command
guards and translations. Separately, a
[live HA 2026.7.4 deployment](../../docs/ha-runtime-validation.md) passed normal
config-flow setup, three physical stations, automatic entity discovery,
a restored setting change, integration reload and AP/gateway recovery.
Reauthentication, reconfiguration, other releases and long-term operation remain
unverified; run HA integration tests/hassfest for the target release.
The component uses `ConfigEntry.runtime_data` and current coordinator APIs;
target current Home Assistant releases.

The implementation follows official guidance for [config flows](https://developers.home-assistant.io/docs/core/integration/config_flow/),
[coordinated fetching](https://developers.home-assistant.io/docs/integration_fetching_data/),
[shared HTTP sessions](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/inject-websession/),
and [runtime data](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/runtime-data/).
