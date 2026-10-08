"""Native station MQTT request framing and telemetry decoding."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field, replace
import json
import re
import secrets
import time

from .protocol import DATA_REQUEST, DATA_RESPONSE, Model, build_packet, decode_telemetry, parse_packet, tlv, validate_device_timeout
from .tou import TouPeriod, validate_periods
from .c1000 import c1000_setting

RADIO_NATIVE_PATTERN = bytes.fromhex("030010")
RADIO_QUERY_RESPONSES = frozenset(("0803", "0822"))


def decode_native_wireless_state(reply: bytes) -> dict[str, int]:
    """Select application-state bytes; omit MAC, SSID and other private fields."""
    if not reply or reply[0] != 0:
        raise ValueError("Radio wireless-state request failed")
    fields = {}
    pos = 1
    while pos < len(reply):
        if pos + 2 > len(reply):
            raise ValueError("Truncated radio wireless-state fields")
        tag, size = reply[pos:pos + 2]
        end = pos + 2 + size
        if end > len(reply) or tag in fields:
            raise ValueError("Invalid radio wireless-state fields")
        fields[tag] = reply[pos + 2:end]
        pos = end
    if any(len(fields.get(tag, b"")) != 1 for tag in (0xA1, 0xA2)):
        raise ValueError("Missing radio application-state bytes")
    return {"bluetooth_application_state": fields[0xA1][0],
            "wifi_application_state": fields[0xA2][0]}


@dataclass(frozen=True)
class MqttTelemetry:
    """Power readings and original fields, which may contain device identifiers."""

    metrics: dict[str, int | str]
    raw_tlvs: dict[int, bytes]


@dataclass(frozen=True)
class NativeMqttRequest:
    """A nonretained publish; topic and payload contain private identifiers."""

    topic: str = field(repr=False)
    payload: str = field(repr=False)
    response_command: str
    response_aliases: tuple[str, ...] = ()
    response_pattern: bytes = DATA_RESPONSE


@dataclass
class NativeMqttCommands:
    """Build model-specific native requests; callers own confirmation.

    Requires a provisioned station and the configured account ID. These methods
    do not contact Anker, connect to a broker, or determine whether a write took
    effect. Publish with retain=False and confirm settings in fresh telemetry.
    """

    device_serial: str = field(repr=False)
    account_id: str = field(repr=False)
    model: Model = Model.C2000_GEN2
    _session_id: str = field(default_factory=lambda: secrets.token_hex(8), init=False, repr=False)
    _sequence: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        self.model = Model(self.model)
        if self.model not in (Model.C1000, Model.C1000_GEN2, Model.C2000_GEN2):
            raise ValueError("Native MQTT commands support original C1000 and C1000/C2000 Gen 2 only")
        if not isinstance(self.device_serial, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.device_serial):
            raise ValueError("Invalid native MQTT device serial")
        if not isinstance(self.account_id, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", self.account_id):
            raise ValueError("Native MQTT account ID must be 40 hexadecimal characters")

    def status(self) -> NativeMqttRequest:
        """Request full telemetry, allowing original C1000's deferred report.

        Original MCU 1.5.9 routes 0040 through a pending 0405 publication while
        network reporting is active; otherwise it replies directly with 0840.
        An alias is a fresh full status report, never an acknowledgement of a
        setting write. Callers must validate identity and match only after send.
        """
        request = self._request("0040" if self.model == Model.C1000 else "0100", b"", milliseconds=False)
        return replace(request, response_aliases=("0405",)) if self.model == Model.C1000 else request

    @property
    def product(self) -> str:
        return {Model.C1000: "A1761", Model.C1000_GEN2: "A1763", Model.C2000_GEN2: "A1783"}[self.model]

    def readiness(self) -> NativeMqttRequest:
        """Read controller readiness (0089/0889); opaque fields stay private."""
        self._require_gen2("Controller readiness")
        return self._request("0089", b"", milliseconds=False)

    def wireless_state(self) -> NativeMqttRequest:
        """Read A1763 radio state using its local namespace, without a setter."""
        if self.model != Model.C1000_GEN2:
            raise ValueError("Radio wireless state supports C1000 Gen 2 only")
        command = "0003"
        frame = build_packet(RADIO_NATIVE_PATTERN, bytes.fromhex(command), b"")
        return self._framed_request(command, frame, time.time(), response_pattern=RADIO_NATIVE_PATTERN)

    def wifi_rssi(self) -> NativeMqttRequest:
        """Query the A1763 radio's AP-info RSSI; failure is not cached quality."""
        if self.model != Model.C1000_GEN2:
            raise ValueError("Radio Wi-Fi RSSI supports C1000 Gen 2 only")
        command = "0022"
        frame = build_packet(RADIO_NATIVE_PATTERN, bytes.fromhex(command), b"")
        return self._framed_request(command, frame, time.time(), response_pattern=RADIO_NATIVE_PATTERN)

    def stream(self, seconds: int = 60) -> NativeMqttRequest:
        """Request a telemetry window; renew explicitly if needed.

        Original MCU 1.5.9 retains an existing timer's duration if it is already
        running. A renewal may therefore keep its prior expiry interval.
        """
        if type(seconds) is not int or not 1 <= seconds <= 120:
            raise ValueError("Stream duration must be an integer from 1 to 120 seconds")
        interval = (b"\x02" + seconds.to_bytes(2, "little") if self.model == Model.C1000
                    else b"\x03" + seconds.to_bytes(4, "little"))
        fields = tlv(0xA2, b"\x01\x01") + tlv(0xA3, interval)
        return self._request("0057", fields, milliseconds=False)

    def ac_charging_power(self, watts: int) -> NativeMqttRequest:
        """Set the charging-power limit; does not include an AC output switch."""
        if self.model == Model.C1000:
            if type(watts) is not int or not 100 <= watts <= 1000 or watts % 100:
                raise ValueError("Charging power must be 100–1000 W in 100 W steps")
            return self._request("0044", tlv(0xA2, b"\x02" + watts.to_bytes(2, "little")), milliseconds=False)
        maximum = 1200 if self.model == Model.C1000_GEN2 else 1800
        minimum = 100 if self.model == Model.C1000_GEN2 else 300
        if type(watts) is not int or not minimum <= watts <= maximum or watts % 100:
            raise ValueError(f"Charging power must be {minimum}–{maximum} W in 100 W steps")
        fields = tlv(0xA4, b"\x02" + watts.to_bytes(2, "little"))
        return self._request("0101", fields, milliseconds=True)

    def charge_cap(self, percentage: int) -> NativeMqttRequest:
        """Set only the upper charge limit; omit the lower-limit field entirely."""
        self._require_gen2("Charge cap")
        if type(percentage) is not int or percentage not in (80, 85, 90, 95, 100):
            raise ValueError("Charge cap must be 80–100 percent in 5 percent steps")
        return self._request("0103", tlv(0xAA, bytes((1, percentage))), milliseconds=True)

    def discharge_floor(self, lower: int) -> NativeMqttRequest:
        """Set only C1000's lower limit; callers must protect backup reserve.

        Native transport is verified on C1000 Gen 2 main 1.1.4.9. Raising the
        lower limit can also raise reserve; confirm both A4 and D9 readbacks.
        """
        if self.model != Model.C1000_GEN2:
            raise ValueError("Discharge floor supports C1000 Gen 2 only")
        if type(lower) is not int or lower not in (1, 5, 10, 15, 20):
            raise ValueError("Discharge floor must be 1, 5, 10, 15, or 20 percent")
        return self._request("0103", tlv(0xAB, bytes((1, lower))), milliseconds=True)

    def temperature_unit(self, fahrenheit: bool) -> NativeMqttRequest:
        """Set C1000 temperature units using its model-specific command."""
        if self.model == Model.C1000:
            return self._original_setting("temperature_unit_fahrenheit", fahrenheit)
        return self._c1000_boolean(0xA5, fahrenheit)

    def off_grid_alert(self, enabled: bool) -> NativeMqttRequest:
        """Set the off-grid alert setting; verified on C1000 Gen 2 main 1.1.4.9."""
        return self._c1000_boolean(0xB0, enabled)

    def display_brightness(self, level: int) -> NativeMqttRequest:
        """Set C1000 brightness; original accepts 0–3, Gen 2 accepts 1–3."""
        if self.model == Model.C1000:
            return self._original_setting("display_brightness", level)
        if self.model != Model.C1000_GEN2:
            raise ValueError("Display brightness supports C1000 Gen 2 only")
        if type(level) is not int or level not in (1, 2, 3):
            raise ValueError("Display brightness must be 1, 2, or 3")
        return self._request("0103", tlv(0xA3, bytes((1, level))), milliseconds=True)

    def display_timeout(self, seconds: int) -> NativeMqttRequest:
        """Set screen timeout with model-specific allowed durations.

        C2000's 30/60-second field is verified over BLE. Its native envelope
        uses the established Gen 2 controller route; hardware replay of that
        particular native setter is still pending.
        """
        if self.model == Model.C1000:
            return self._original_setting("display_timeout", seconds)
        allowed = (30, 60) if self.model == Model.C2000_GEN2 else (0, 10, 20, 30, 60, 300, 1800)
        self._require_gen2("Display timeout")
        if type(seconds) is not int or seconds not in allowed:
            raise ValueError(f"Display timeout must be one of {allowed} seconds")
        return self._request("0103", tlv(0xA4, b"\x02" + seconds.to_bytes(2, "little")), milliseconds=True)

    def clock_brightness(self, window: int, high: bool) -> NativeMqttRequest:
        """Set one inactive clock-window selector without changing its theme/assets."""
        if self.model != Model.C1000_GEN2:
            raise ValueError("Clock brightness supports C1000 Gen 2 only")
        if type(window) is not int or window not in (1, 2) or type(high) is not bool:
            raise ValueError("Use window 1 or 2 and an explicit high boolean")
        return self._request("0091", tlv(0xAC if window == 1 else 0xAD, bytes((1, int(high)))), milliseconds=True)

    def port_memory(self, enabled: bool) -> NativeMqttRequest:
        """Set C1000 port memory; disabling clears transient recovery bookkeeping."""
        return self._c1000_boolean(0xA8, enabled)

    def device_timeout(self, minutes: int) -> NativeMqttRequest:
        """Set C1000 idle timeout; zero does not disable every sleep path."""
        if self.model == Model.C1000:
            return self._original_setting("device_timeout", minutes)
        if self.model != Model.C1000_GEN2:
            raise ValueError("Device timeout supports C1000 Gen 2 only")
        validate_device_timeout(minutes)
        return self._request("0103", tlv(0xA6, b"\x02" + minutes.to_bytes(2, "little")), milliseconds=True)

    def light_mode(self, mode: int) -> NativeMqttRequest:
        """Set the original C1000 light mode using its validated 0–4 domain."""
        return self._original_setting("light_mode", mode)

    def dc_power_saving(self, enabled: bool) -> NativeMqttRequest:
        """Set C1000 DC Normal/Smart; callers must require DC off."""
        if self.model == Model.C1000_GEN2:
            if type(enabled) is not bool:
                raise ValueError("enabled must be a boolean")
            return self._request("0102", tlv(0xA4, bytes((1, int(enabled)))), milliseconds=True)
        return self._original_setting("dc_power_saving_mode_enabled", enabled)

    def ac_power_saving(self, enabled: bool) -> NativeMqttRequest:
        """Set C1000 AC Normal/Smart; callers must require AC off/no timer."""
        if self.model == Model.C1000_GEN2:
            if type(enabled) is not bool:
                raise ValueError("enabled must be a boolean")
            return self._request("0101", tlv(0xA6, bytes((1, int(enabled)))), milliseconds=True)
        return self._original_setting("ac_power_saving_mode_enabled", enabled)

    def ac_output(self, enabled: bool) -> NativeMqttRequest:
        """Build a C1000 Gen 2 output request for local operators, never C2000."""
        if self.model != Model.C1000_GEN2 or type(enabled) is not bool:
            raise ValueError("AC output requires C1000 Gen 2 and a boolean")
        return self._request("0101", tlv(0xA2, bytes((1, int(enabled)))), milliseconds=True)

    def ac_countdown(self, seconds: int) -> NativeMqttRequest:
        """Build a local C1000 Gen 2 expiry timer; zero cannot revoke queued stop.

        Positive timers deliberately stop AC later. The operator domain is
        10 minutes through 24 hours; firmware's complete domain is unverified.
        Callers must obtain a fresh output-on, inactive-timer baseline.
        """
        if self.model != Model.C1000_GEN2:
            raise ValueError("AC countdown supports C1000 Gen 2 only")
        if type(seconds) is not int or (seconds != 0 and not 600 <= seconds <= 86400):
            raise ValueError("AC countdown must be zero or 600–86400 integer seconds")
        return self._request("0101", tlv(0xA3, b"\x03" + seconds.to_bytes(4, "little")), milliseconds=True)

    def _original_setting(self, setting: str, value: int | bool) -> NativeMqttRequest:
        if self.model != Model.C1000:
            raise ValueError("This setting supports original C1000 only")
        command, body, _ = c1000_setting(setting, value)
        # The shared BLE builder includes source21; _request supplies source22
        # and the native UTC-seconds field. No output switch is added.
        return self._request(f"{int(command, 16) & 0x3fff:04x}", body[3:], milliseconds=False)

    def fast_charge(self, enabled: bool) -> NativeMqttRequest:
        """Set C1000 fast charge; callers must confirm retention and protect tariffs."""
        if self.model == Model.C1000:
            return self._original_setting("fast_charge_enabled", enabled)
        if self.model != Model.C1000_GEN2:
            raise ValueError("Fast charge supports original C1000 and C1000 Gen 2 only")
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        return self._request("0101", tlv(0xA7, bytes((1, int(enabled)))), milliseconds=True)

    def _c1000_boolean(self, tag: int, value: bool) -> NativeMqttRequest:
        if self.model != Model.C1000_GEN2:
            raise ValueError("This setting supports C1000 Gen 2 only")
        if type(value) is not bool:
            raise ValueError("Setting value must be a boolean")
        return self._request("0103", tlv(tag, bytes((1, int(value)))), milliseconds=True)

    def backup_reserve(self, percentage: int) -> NativeMqttRequest:
        """Set only backup reserve; callers must check upper/lower limits first."""
        self._require_gen2("Backup reserve")
        if type(percentage) is not int or not 5 <= percentage <= 100 or percentage % 5:
            raise ValueError("Backup reserve must be 5–100 percent in 5 percent steps")
        return self._request("0090", tlv(0xA5, bytes((1, percentage))), milliseconds=True)

    def tou_plan(self, periods: tuple[TouPeriod, ...], *, enabled: bool = False) -> NativeMqttRequest:
        """Store a plan in Standard by default, or explicitly activate Time-of-Use.

        A6 carries the count. A7 has type04 plus triplets, with no second count.
        This changes mode/schedule only; it never includes an output switch.
        """
        self._require_gen2("Time-of-Use")
        periods = validate_periods(periods)
        if type(enabled) is not bool or (enabled and not periods):
            raise ValueError("Enabled Time-of-Use requires a nonempty schedule")
        fields = (tlv(0xA2, bytes((1, int(enabled)))) + tlv(0xA3, b"\x01\x00")
                  + tlv(0xA4, b"\x01\x00") + tlv(0xA6, bytes((1, len(periods))))
                  + tlv(0xA7, b"\x04" + (b"".join(p.to_bytes() for p in periods) or b"\x00")))
        return self._request("0090", fields, milliseconds=True)

    def _require_gen2(self, setting: str) -> None:
        if self.model not in (Model.C1000_GEN2, Model.C2000_GEN2):
            raise ValueError(f"{setting} supports C1000 Gen 2 and C2000 Gen 2 only")

    def _request(self, command: str, fields: bytes, *, milliseconds: bool) -> NativeMqttRequest:
        now = time.time()
        timestamp = (tlv(0xFD, b"\x00" + str(int(now * 1000)).encode("ascii"))
                     if milliseconds else tlv(0xFE, b"\x03" + int(now).to_bytes(4, "little")))
        frame = build_packet(DATA_REQUEST, bytes.fromhex(command), tlv(0xA1, b"\x22") + fields + timestamp)
        return self._framed_request(command, frame, now)

    def _framed_request(self, command: str, frame: bytes, now: float,
                        *, response_pattern: bytes = DATA_RESPONSE) -> NativeMqttRequest:
        self._sequence += 1
        envelope = {
            "head": {
                "version": "1.0.0.1", "client_id": "solix-local-research",
                "sess_id": self._session_id, "msg_seq": self._sequence,
                "seed": 1, "timestamp": int(now), "cmd_status": 2, "cmd": 17,
                "sign_code": 1, "device_pn": self.product, "device_sn": self.device_serial,
            },
            "payload": json.dumps({
                "device_sn": self.device_serial, "account_id": self.account_id,
                "data": base64.b64encode(frame).decode("ascii"),
            }, separators=(",", ":")),
        }
        return NativeMqttRequest(
            topic=f"cmd/anker_power/{self.product}/{self.device_serial}/req",
            payload=json.dumps(envelope, separators=(",", ":")),
            response_command=f"{int(command, 16) | 0x0800:04x}",
            response_pattern=response_pattern,
        )


def decode_mqtt_telemetry(
    message: str | bytes,
    *,
    model: Model,
    expected_serial: str | None = None,
) -> MqttTelemetry | None:
    """Decode an unencrypted native MQTT ``param_info``/response envelope.

    The outer JSON contains a JSON string in ``payload``; its ``data`` field
    holds a Base64 SOLIX packet. This path was verified on C2000 main 2.1.6.4.
    Returns None for non-telemetry messages and other devices. Malformed data
    raises ValueError without including message contents. This does not connect
    to a broker, provision a station, or establish freshness/availability.
    """
    if len(message) > 131072:
        raise ValueError("MQTT message is too large")
    try:
        envelope = json.loads(message)
        if not isinstance(envelope, dict) or not isinstance(envelope.get("payload"), str):
            raise ValueError
        payload = json.loads(envelope["payload"])
        if not isinstance(payload, dict):
            raise ValueError
    except (ValueError, UnicodeError, TypeError):
        raise ValueError("Invalid native MQTT envelope") from None
    if "data" not in payload:
        return None
    if not isinstance(payload.get("sn"), str) or not isinstance(payload.get("pn"), str):
        raise ValueError("Missing native MQTT device identity")
    model = Model(model)
    products = {Model.C1000: "A1761", Model.C1000_GEN2: "A1763", Model.C2000_GEN2: "A1783"}
    if model not in products:
        raise ValueError("Unsupported native MQTT telemetry model")
    product = products[model]
    if payload["pn"] != product or (expected_serial is not None and payload["sn"] != expected_serial):
        return None
    if payload.get("encoding_type", 0) != 0:
        raise ValueError("Encrypted native MQTT payload is unsupported")
    if not isinstance(payload["data"], str):
        raise ValueError("Invalid native MQTT packet encoding")
    try:
        packet = parse_packet(base64.b64decode(payload["data"], validate=True))
    except (ValueError, binascii.Error):
        raise ValueError("Invalid native MQTT packet") from None
    if packet.pattern != DATA_RESPONSE:
        return None
    body = packet.payload
    response_command = "0840" if model == Model.C1000 else "0900"
    telemetry_command = "0405" if model == Model.C1000 else "0421"
    if packet.command == bytes.fromhex(response_command):
        if not body or body[0] != 0:
            raise ValueError("Native MQTT status request failed")
        body = body[1:]
    elif packet.command != bytes.fromhex(telemetry_command):
        return None
    metrics, raw_tlvs = decode_telemetry(body, model=model)
    return MqttTelemetry(metrics=metrics, raw_tlvs=raw_tlvs)
