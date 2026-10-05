# Bluetooth inspection without a SOLIX login

`ble-inspect` checks visibility and optional GATT service metadata for the
original C1000 (`c1000`, A1761) and C1000 Gen 2 (`c1000_gen2`, A1763).
It requires no saved station profile or pairing identity.

```sh
# Advertisement discovery only; no station connection.
solix-link ble-inspect --model c1000_gen2 --timeout 10

# Connect only if exactly one matching station is advertising.
solix-link ble-inspect --model c1000 --connect --connect-timeout 15
```

Python callers can use
`await solix_link.ble_inspection.inspect_ble_features(Model.C1000, connect=True)`.
Discovery and connection bounds accept 1–30 seconds. Discovery has five seconds
of backend overhead; disconnect gets its own five-second deadline. There is no
retry loop. Cancellation propagates after attempted connection cleanup.

## What it inspects

Default discovery returns model-match counts and a separate count of unknown
advertisements carrying the SOLIX service UUID. Unknown devices are never
selected by that UUID alone. Multiple model matches refuse a connection.

With `--connect`, the probe reports service/characteristic UUIDs and standard
read/write/notify/indicate properties. Known UUIDs get roles such as
`solix_command`, `solix_telemetry`, `station_identifier` or `firmware_revision`.
It reads **no characteristic values**, including identifier/revision values;
it sends no SOLIX negotiation, subscription, diagnostic or setting packets.
It requests no pairing and does not change adapter power or configuration.
A connection temporarily occupies the station's Bluetooth link.

JSON excludes advertisement names, Bluetooth addresses, characteristic handles
and backend exception text. Failures expose only their exception class and
stage; disconnect failure is explicit. Exit zero means discovery or inventory
succeeded; absent/ambiguous targets and backend failures return one. C2000 and
C300 are excluded from this investigation command.

## Why normal monitoring needs a separate boundary

The existing `Session._negotiate()` sends **4022** timezone/conference fields
after key exchange for both legacy and Prime. Prime then sends **4027**
registration; a session without an owner ID generates one. Those steps are
outside a service-only inspection. A telemetry request described as read-only
does not make its connection setup free of configuration/registration traffic.
This is source inspection, not a new physical firmware test. Normal monitoring
behavior is unchanged; this command never constructs a `Session` or monitor.

GATT properties establish exposed interfaces, **not supported SOLIX commands**,
installed firmware versions, complete saved settings or electrical behavior.
Once advertising is available, future separately scoped checks can compare
normal A1761 `4040` and A1763 `4100` telemetry, saved settings, FE controller time
and the established A1763 radio RSSI route. A1761 F0 factory probes and radio
diagnostic reads that can consume/reset state are excluded.

## Server observation — 2026-10-05

One approved ten-second discovery and two later ten-second model scans saw
neither C1000 and no unknown SOLIX-service advertisements. The new code made
zero connection attempts and sent zero station commands. This demonstrates
adapter discovery access, not station availability, supported settings or an
electrical test. Synthetic tests cover GATT inventory; no live GATT inventory
was possible. User access to a short IoT-button press, station proximity and
release of another Bluetooth connection remain possible next prerequisites.
The user was away; no reset, recovery setter or live service change was used.
