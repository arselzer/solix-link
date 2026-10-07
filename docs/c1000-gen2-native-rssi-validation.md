# C1000 Gen 2: native MQTT RSSI validation

## Result and versions

On **2026-10-02**, one dedicated native MQTT RSSI query on **A1763 C1000
Gen 2, main 1.1.4.9 / radio 0.3.3.0** returned **−42 dBm**. It used the
already working local identity and isolated AP. No Bluetooth session, cloud
request or settings write was needed.

This completes the native round-trip check missing from the
[71-case routing replay](radio-rssi-routing.md). The earlier
[Prime BLE check](c1000-radio-readback-validation.md) returned unavailable;
both transports now retain the same explicit failure semantics.

## Frame, decoder and correlation

The request is **`030010 / 0022 / empty body`**, within the established native
JSON-string envelope. It has no controller source tag or inner timestamp.
The response is **`030010 / 0822`** with raw body:

```text
00 a1 04 d6 ff ff ff
```

The four bytes encode signed −42. `decode_wifi_rssi()` accepts the firmware's
signed-byte range in a four-byte raw TLV, rejects zero/malformed observations,
and returns `None` for the exact failure body `01`. No typed prefix, cached
quality percentage or zero-success measurement is substituted.

Only an active serialized request on the configured station can admit a known
radio reply. Pattern and opcode must both match. Wrong identity, retained
messages, controller replies and the other radio query cannot acknowledge it.
The response does not update controller metrics or telemetry freshness. No
sequence/session echo is established; identical delayed replies remain a
correlation limit. The live trial used one query without retries.

## Protected-state confirmation

Complete fresh controller settings before/after matched apart from natural
display activity and leading D9 runtime bytes. AC/DC outputs and mains state
matched; countdowns did not increase. Three later fleet samples over about
15 seconds remained fresh with AC enabled and protected settings unchanged.

| Station | Bounded capture requests | Setting writes |
| --- | --- | --- |
| C1000 Gen 2 | Five `0100`; one `0022` | 0 |
| Original C1000 | Three `0040` | 0 |
| C2000 Gen 2 | Three `0100` | 0 |

Raw logs remain private. Reported output state is not an independent
electrical-continuity or calibrated RF measurement.

## Python and operator CLI

```sh
solix-link ap-service-wifi-rssi \
  --directory /path/to/private/ap-service --name c1000_gen2
```

`NativeMqttCommands.wifi_rssi()` builds the request;
`LocalMqttServer.wifi_rssi()` acquires fresh baseline/confirmation and returns
`wifi_rssi_dbm`, `rssi_available`, firmware/scope and observation time.
The same protected-state helper serves the existing wireless-flags query.

The Unix-socket command works with controls disabled, rejects extra fields
and requires exact fresh **1.1.4.9 / 0.3.3.0** versions before sending RSSI.
The initial validation used an explicit private query. The
[2026-10-07 presentation extension](reported-telemetry.md) adds cached HTTP/HA
data and opt-in five-minute gateway polling; RSSI remains excluded from
HTTP/HA setting controls. Other models remain unsupported. Focused framing, failure, freshness,
firmware and correlation tests passed; the full gate passed **2,451 Python/HA
tests**. Deployment and policy checks are recorded in
[HA runtime validation](ha-runtime-validation.md#native-rssi-runtime-update).
