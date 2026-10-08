"""BLE framing, negotiation and telemetry for supported SOLIX stations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from importlib import resources
from pathlib import Path
import re
import secrets
import time
from zoneinfo import TZPATH, ZoneInfo

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SERVICE_UUID = "8c850001-0302-41c5-b46e-cf057c562025"
COMMAND_UUID = "8c850002-0302-41c5-b46e-cf057c562025"
TELEMETRY_UUID = "8c850003-0302-41c5-b46e-cf057c562025"
IDENTIFIER_UUID = "0000ff09-0000-1000-8000-00805f9b34fb"
LEGACY_CLIENT_UUID = b"b2dc0b17-b75d-4abf-ba6e-ec7c997c23e7"
GCM_KEY = bytes.fromhex("b8ff7422955d4eb6d554a2c470280559")
GCM_NONCE = bytes.fromhex("6ba3e3f2f3a60f2971ce5d1f")
GCM_AAD = bytes.fromhex("3322110077665544bbaa9988ffeeddcc")
NEGOTIATION = bytes.fromhex("030001")
DATA_REQUEST = bytes.fromhex("03000f")
RADIO_REQUEST = bytes.fromhex("030010")
DATA_RESPONSE = bytes.fromhex("03010f")
C2000_SUBSCRIBE_EXTRA = bytes.fromhex("a20a040100e3fbfcfe000000")
DEVICE_TIMEOUT_MINUTES = (0, 30, 60, 120, 240, 360, 720, 1440)


def validate_device_timeout(minutes: int) -> None:
    if type(minutes) is not int or minutes not in DEVICE_TIMEOUT_MINUTES:
        raise ValueError(f"Device timeout must be minutes in {DEVICE_TIMEOUT_MINUTES}; 0 means Never")


def timezone_confer(timezone_name: str | None) -> tuple[bytes, bytes]:
    """Return the Prime 4022 UTC offset (seconds west) and POSIX timezone."""
    if timezone_name is None:
        return bytes(4), b"UTC0"
    zone = ZoneInfo(timezone_name)
    offset = datetime.now(zone).utcoffset()
    if offset is None:
        raise ValueError(f"No UTC offset for {timezone_name}")
    posix: bytes | None = None
    for root in TZPATH:
        path = Path(root) / timezone_name
        if path.is_file():
            lines = path.read_bytes().rsplit(b"\n", 2)
            posix = lines[-2] if len(lines) > 1 else None
            break
    if posix is None:
        try:
            lines = resources.files("tzdata.zoneinfo").joinpath(timezone_name).read_bytes().rsplit(b"\n", 2)
            posix = lines[-2] if len(lines) > 1 else None
        except (ImportError, FileNotFoundError, ModuleNotFoundError) as exc:
            raise ValueError(f"Cannot read POSIX rules for {timezone_name}") from exc
    if not posix or len(posix) > 255 or not posix.isascii():
        raise ValueError(f"Invalid POSIX rules for {timezone_name}")
    seconds_west = -int(offset.total_seconds())
    return seconds_west.to_bytes(4, "little", signed=True), posix


C1000_PRIME_SETTINGS = frozenset((
    "display_brightness", "ac_charging_power", "device_timeout", "display_timeout",
    "light_mode", "temperature_unit_fahrenheit",
    "dc_power_saving_mode_enabled",
    "fast_charge_enabled",
    "ac_output_enabled", "ac_power_saving_mode_enabled",
))


class Model(str, Enum):
    C300 = "c300"
    C1000 = "c1000"
    C1000_GEN2 = "c1000_gen2"
    C2000_GEN2 = "c2000_gen2"

    @classmethod
    def from_name(cls, name: str | None) -> Model:
        text = (name or "").lower()
        if ("c1000" in text and "gen 2" in text) or "a1763" in text:
            return cls.C1000_GEN2
        if ("c2000" in text and "gen 2" in text) or "a1783" in text:
            return cls.C2000_GEN2
        # C300/C300X AC share typed telemetry. DC variants have another map.
        words = text.replace("_", " ").replace("-", " ")
        if not re.search(r"\bdc\b", words) and not any(code in text for code in ("a1726", "a1728")):
            if re.search(r"\bc300x?\b", words) or "a1722" in text or "a1723" in text:
                return cls.C300
        if "a1761" in text or (re.search(r"\bc1000x?\b", words) and "gen" not in words):
            return cls.C1000
        raise ValueError(f"Unsupported SOLIX model: {name!r}")

    def resolve_protocol(self, protocol: str | None = None) -> str:
        """Choose the default; original C1000 firmware 1.7.1 can use Prime."""
        if protocol is None:
            protocol = "legacy" if self in (Model.C300, Model.C1000) else "prime"
        if protocol not in ("prime", "legacy"):
            raise ValueError("protocol must be prime or legacy")
        if self == Model.C2000_GEN2 and protocol != "prime":
            raise ValueError("C2000 Gen 2 requires Prime protocol")
        if self == Model.C300 and protocol != "legacy":
            raise ValueError("C300/C300X AC support requires legacy protocol")
        return protocol


@dataclass(frozen=True)
class Packet:
    pattern: bytes
    command: bytes
    payload: bytes


def build_packet(pattern: bytes, command: bytes, payload: bytes) -> bytes:
    body = b"\xff\x09" + (10 + len(payload)).to_bytes(2, "little") + pattern + command + payload
    checksum = 0
    for byte in body:
        checksum ^= byte
    return body + bytes([checksum])


def parse_packet(data: bytes) -> Packet:
    if len(data) < 10 or data[:2] != b"\xff\x09":
        raise ValueError("Invalid SOLIX packet header")
    if int.from_bytes(data[2:4], "little") != len(data):
        raise ValueError("Invalid SOLIX packet length")
    checksum = 0
    for byte in data:
        checksum ^= byte
    if checksum:
        raise ValueError("Invalid SOLIX packet checksum")
    return Packet(data[4:7], data[7:9], data[9:-1])


def tlv(tag: int, value: bytes) -> bytes:
    if len(value) > 255:
        raise ValueError("TLV value is too long")
    return bytes([tag, len(value)]) + value


def parse_tlvs(payload: bytes) -> dict[int, bytes]:
    values: dict[int, bytes] = {}
    pos = 1 if payload.startswith(b"\x00") else 0
    while pos + 2 <= len(payload):
        tag, length = payload[pos], payload[pos + 1]
        pos += 2
        if pos + length > len(payload):
            break
        values[tag] = payload[pos:pos + length]
        pos += length
    return values


def decode_telemetry(payload: bytes, model: Model | None = None) -> tuple[dict[str, int | str], dict[int, bytes]]:
    """Decode packed Gen 2 readings and preserve every raw TLV for inspection.

    C1000 Gen 2 offsets are documented by SolixBLE. C2000 Gen 2 appears to
    use the same telemetry command family; verify its offsets on hardware.
    """
    if model == Model.C300:
        from .c300 import decode_c300_telemetry
        return decode_c300_telemetry(payload)
    if model == Model.C1000:
        from .c1000 import decode_c1000_telemetry
        return decode_c1000_telemetry(payload)
    values = parse_tlvs(payload)
    metrics: dict[str, int | str] = {}

    def number(name: str, tag: int, start: int, end: int, signed: bool = False) -> None:
        value = values.get(tag, b"")
        if len(value) >= end:
            metrics[name] = int.from_bytes(value[start:end], "little", signed=signed)

    number("temperature_c", 0xA5, 1, 2, signed=True)
    number("battery_percentage", 0xA5, 3, 4)
    # A1763 main 1.1.4.9 returns literal 100 here, not measured BMS health.
    # Keep the byte available without assigning health semantics to either Gen 2.
    number("battery_health_raw", 0xA5, 4, 5)
    number("output_power_w", 0xA6, 1, 3)
    number("ac_input_power_w", 0xA6, 3, 5)
    number("ac_output_enabled", 0xA7, 1, 2)
    number("ac_output_power_w", 0xA7, 2, 4)
    # A7[4] reports mains presence even when input power is zero. Confirmed
    # by a C1000 unplug/replug capture and a read-only C2000 sample.
    mains = values.get(0xA7, b"")
    if len(mains) >= 5 and mains[4] in (0, 1):
        metrics["ac_input_connected"] = mains[4]
    # Work status and A6's 0.1-hour countdown follow the C2000 Gen 2 field
    # map. The C1000 outage capture changed work status idle -> discharging.
    status = values.get(0xA3, b"")
    if len(status) >= 2:
        work = {0: "idle", 1: "discharging", 2: "charging"}.get(status[1], "unknown")
        metrics["battery_status"] = work
        metrics["battery_discharging"] = int(work == "discharging")
        remaining = values.get(0xA6, b"")
        if len(remaining) >= 9:
            # Zero is used when idle or status is unknown. When active, this
            # means time to empty or full according to battery_status.
            metrics["time_remaining_minutes"] = (
                int.from_bytes(remaining[7:9], "little") * 6
                if work in ("charging", "discharging") else 0
            )
    if model in (Model.C1000_GEN2, Model.C2000_GEN2):
        number("ac_charging_power_limit_w", 0xA4, 5, 7)
        # Verified on both models by 30 -> 60 -> 30 second BLE writes.
        number("display_timeout_seconds", 0xA4, 16, 18)
        # Gen 2 D9 reports tariff/mode/reserve. C1000 1.1.4.9 firmware and
        # live baseline agree with the independently tested C2000 layout.
        mode = values.get(0xD9, b"")
        if len(mode) >= 4:
            metrics["active_tariff"] = {
                0: "none", 1: "peak", 2: "mid_peak", 3: "off_peak",
            }.get(mode[1], "unknown")
            metrics["usage_mode"] = {
                0: "standard", 1: "time_of_use", 2: "self_consumption", 3: "custom",
            }.get(mode[2], "unknown")
            metrics["backup_reserve_percentage"] = mode[3]
        if len(mode) >= 7 and mode[0] == 4:
            # Retained C2000 records and the recovered C1000 serializer agree:
            # count at 6, triplets at 7, then a separate 19-byte backup tail.
            # Require the complete block before exposing a control baseline.
            count = mode[6]
            if count <= 6 and len(mode) >= 26 + 3 * count:
                metrics["tou_schedule_slot_count"] = count
    if model == Model.C2000_GEN2:
        # Observed C2000 byte only: A1763 uses this offset for a saved output
        # setting. C2000 semantics are unproved; do not label it input Hz.
        settings = values.get(0xA4, b"")
        if len(settings) == 34 and settings[0] == 4:
            metrics["ac_frequency_raw"] = settings[7]
        # C2000 A4 settings layout follows the public Gen 2 field map. These
        # values were also checked against a live read-only 34-byte A4 block.
        number("ac_output_timer_remaining_seconds", 0xA4, 1, 5)
        number("ac_power_saving_mode_enabled", 0xA4, 8, 9)
        number("dc_output_timer_remaining_seconds", 0xA4, 9, 13)
        number("dc_power_saving_mode_enabled", 0xA4, 13, 14)
        number("device_timeout_minutes", 0xA4, 14, 16)
        number("ac_fast_charge_enabled", 0xA4, 21, 22)
        number("port_memory_enabled", 0xA4, 23, 24)
        versions = values.get(0xF9, b"")
        for name, slot in (
            ("software_version", 0),
            ("software_version_controller", 1),
            ("software_version_inverter", 3),
            ("software_version_bms", 4),
            ("software_version_module", 6),
        ):
            start = slot * 4
            if len(versions) >= start + 4:
                metrics[name] = ".".join(str(byte) for byte in reversed(versions[start:start + 4]))
        expansion = values.get(0xC0, b"")
        if expansion:
            tail = expansion[1 + expansion[0]:]
            if len(tail) >= 15 and tail[12] in (0, 1):
                # This is an observed presence flag, not an arbitrary count.
                metrics["expansion_battery_count"] = tail[12]
    if model == Model.C1000_GEN2:
        from .clock_screen import decode_clock_screen
        from .disaster_plan import decode_disaster_plan

        metrics.update(decode_clock_screen(values.get(0xDA, b""), model=model))
        metrics.update(decode_disaster_plan(values.get(0xD9, b"")))
        # A1763 main 1.1.4.9 full-status FE serializer: RTC + stored offset,
        # uint32 UTC seconds. Incremental reports can retain an earlier value;
        # this is reported device time, not host freshness or clock accuracy.
        controller_time = values.get(0xFE, b"")
        if len(controller_time) == 5 and controller_time[0] == 3:
            metrics["controller_utc_timestamp_seconds"] = int.from_bytes(controller_time[1:], "little")
        # Exact type/lengths come from A1763 main 1.1.4.9 serializers.
        # A8 incremental updates can retain an old power word, so use A6's
        # power field. Its watt scale still needs a nonzero physical PV check.
        pv = values.get(0xA8, b"")
        if len(pv) == 4 and pv[0] == 4 and pv[1] in (0, 1):
            metrics["dc_input_active"] = pv[1]
        power = values.get(0xA6, b"")
        if len(power) == 10 and power[0] == 4:
            metrics["dc_input_power_raw"] = int.from_bytes(power[5:7], "little")
        work = values.get(0xA3, b"")
        if len(work) == 14 and work[0] == 4:
            metrics["controller_error_code"] = work[2]
            # A1763 main 1.1.4.9: MPPT weak-light lock getter 08015494.
            # Firmware-derived state; physical PV behavior is not validated.
            if work[13] in (0, 1):
                metrics["pv_weak_light_locked"] = work[13]
        # Unlike C2000's F9, the observed C1000 block includes a type04 byte.
        versions = values.get(0xF9, b"")
        if len(versions) >= 29 and versions[0] == 4:
            for name, slot in (("software_version", 0), ("software_version_module", 6)):
                start = 1 + slot * 4
                metrics[name] = ".".join(str(byte) for byte in reversed(versions[start:start + 4]))
        number("ac_output_timeout_seconds", 0xA4, 1, 5)
        number("dc_output_timeout_seconds", 0xA4, 9, 13)
        number("device_timeout_minutes", 0xA4, 14, 16)
        # Firmware getter 0801a648: saved brightness, not an operating mode.
        number("display_brightness", 0xA4, 18, 19)
        number("temperature_unit_fahrenheit", 0xA4, 20, 21)
        number("ac_fast_charge_enabled", 0xA4, 21, 22)
        # Historical metric name: this is runtime display-timer activity,
        # not a saved display configuration. Fast-charge events can wake it.
        number("display_enabled", 0xA4, 22, 23)
        number("port_memory_enabled", 0xA4, 23, 24)
        settings = values.get(0xA4, b"")
        if len(settings) == 34 and settings[0] == 4:
            # A1763/main 1.1.4.9 saved getters 0801a580/5ac/5dc.
            # These are configuration readbacks, not measured frequency or
            # promises that an output will stay on under the Smart policy.
            metrics["ac_output_frequency_setting_hz"] = settings[7] if settings[7] in (50, 60) else "unknown"
            for name, offset in (("ac_power_saving_mode_enabled", 8), ("dc_power_saving_mode_enabled", 13)):
                metrics[name] = settings[offset] if settings[offset] in (0, 1) else "unknown"
        if len(settings) >= 33 and settings[0] == 4:
            # Recovered C1000 controller: alert getter -> A4[32], bit 1.
            metrics["ac_off_grid_alert_enabled"] = (settings[32] >> 1) & 1
    number("dc_output_enabled", 0xB2, 1, 2)
    number("dc_output_power_w", 0xB2, 2, 4)
    number("max_charge_percentage", 0xD9, 4, 5)
    number("min_charge_percentage", 0xD9, 5, 6)
    identity = values.get(0xA2, b"")
    if len(identity) >= 27:
        metrics["serial_number"] = identity[3:20].rstrip(b"\x00").decode("ascii", errors="replace")
        metrics["model_number"] = identity[22:27].rstrip(b"\x00").decode("ascii", errors="replace")
    return metrics, values


@dataclass
class ProtocolUpdate:
    outgoing: list[bytes] = field(default_factory=list)
    telemetry: dict[str, int | str] | None = None
    raw_tlvs: dict[int, bytes] | None = None
    response: tuple[str, bytes] | None = None
    ready: bool = False
    pairing_required: bool = False
    radio_response: tuple[str, bytes] | None = None


class Session:
    """Pure protocol state machine; feed notifications and send its output."""

    def __init__(self, model: Model, owner_user_id: str | None = None, protocol: str | None = None,
                 timezone_name: str | None = None):
        self.model = model
        protocol = model.resolve_protocol(protocol)
        self.protocol = protocol
        if protocol == "prime" and owner_user_id is None:
            owner_user_id = secrets.token_hex(20)
        if owner_user_id is not None and (len(owner_user_id) != 40 or any(c not in '0123456789abcdefABCDEF' for c in owner_user_id)):
            raise ValueError('owner_user_id must be 40 hexadecimal characters')
        self.owner_user_id = owner_user_id
        self._timezone_offset, self._timezone_posix = timezone_confer(timezone_name)
        self._private = ec.generate_private_key(ec.SECP256R1())
        self._public = self._private.public_key().public_bytes(
            encoding=Encoding.X962,
            format=PublicFormat.UncompressedPoint,
        )
        self._secret: bytes | None = None
        self.ready = False
        self.pairing_required = False
        self._fragments: dict[bytes, list[bytes]] = {}

    @staticmethod
    def _timestamp() -> bytes:
        return int(time.time()).to_bytes(4, "little")

    def _legacy_prefix(self) -> bytes:
        return tlv(0xA1, self._timestamp()) + tlv(0xA2, LEGACY_CLIENT_UUID)

    def _crypt(self, payload: bytes, encrypt: bool) -> bytes:
        if self.protocol == "prime":
            key = self._secret[:16] if self._secret else GCM_KEY
            nonce = self._secret[16:28] if self._secret else GCM_NONCE
            cipher = AESGCM(key)
            return cipher.encrypt(nonce, payload, GCM_AAD) if encrypt else cipher.decrypt(nonce, payload, GCM_AAD)
        if not self._secret:
            return payload
        cipher = Cipher(algorithms.AES(self._secret[:16]), modes.CBC(self._secret[16:32]))
        if encrypt:
            padder = padding.PKCS7(128).padder()
            padded = padder.update(payload) + padder.finalize()
            ctx = cipher.encryptor()
            return ctx.update(padded) + ctx.finalize()
        ctx = cipher.decryptor()
        padded = ctx.update(payload) + ctx.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return unpadder.update(padded) + unpadder.finalize()

    def _send(self, pattern: bytes, command: str, plaintext: bytes) -> bytes:
        command_bytes = bytes.fromhex(command)
        if len(command_bytes) != 2:
            raise ValueError("BLE commands must contain exactly two bytes")
        # The radio uses this bit to choose decryption versus direct forwarding.
        # Reject an inconsistent header before constructing an encrypted body.
        if (self.protocol == "prime" or self._secret is not None) and not command_bytes[0] & 0x40:
            raise ValueError("Encrypted BLE commands require the 0x4000 encryption flag")
        return build_packet(pattern, command_bytes, self._crypt(plaintext, True))

    def start(self) -> bytes:
        if self.protocol == "prime":
            return self._send(NEGOTIATION, "4001", tlv(0xA1, self._timestamp()))
        return self._send(NEGOTIATION, "0001", self._legacy_prefix())

    def _registration(self) -> bytes:
        if self.protocol != "prime" or self.owner_user_id is None:
            raise RuntimeError("Registration is only used by Prime devices")
        return self._send(NEGOTIATION, "4027", tlv(0xA1, self._timestamp()) + tlv(0xA2, self.owner_user_id.encode('ascii')))

    def retry_registration(self) -> bytes:
        """Retry Prime registration after the station's main button is pressed once."""
        if not self.pairing_required:
            raise RuntimeError("Station is not awaiting physical pairing confirmation")
        self.pairing_required = False
        return self._registration()

    def send_command(self, command: str, payload: bytes) -> bytes:
        if not self.ready:
            raise RuntimeError("BLE session has not been negotiated")
        if self.model == Model.C2000_GEN2 and (command != "4100" or payload != b"\xa1\x01\x21"):
            raise ValueError("C2000 Gen 2 supports telemetry subscription only")
        if self.model == Model.C300 and (command != "4040" or payload != b"\xa1\x01\x21"):
            raise ValueError("C300/C300X AC supports status requests only")
        if self.model == Model.C1000 and (command != "4040" or payload != b"\xa1\x01\x21"):
            raise ValueError("Use the dedicated original C1000 control methods")
        if self.protocol == "prime" and command == "4100" and payload == b"\xa1\x01\x21":
            payload += C2000_SUBSCRIBE_EXTRA
        extra = tlv(0xFE, self._timestamp() if self.protocol == "prime" else b"\x03" + self._timestamp())
        return self._send(DATA_REQUEST, command, payload + extra)

    def status_packet(self) -> bytes:
        """Build the model's read-only status request or subscription."""
        command = "4040" if self.model in (Model.C300, Model.C1000) else "4100"
        return self.send_command(command, b"\xa1\x01\x21")

    def c1000_control_packet(self, setting: str, value: int | bool) -> bytes:
        """Build a validated A1761 control; see the versioned hardware findings."""
        if self.model != Model.C1000 or not self.ready:
            raise RuntimeError("Controls require a connected original C1000 legacy or supported Prime session")
        if self.protocol == "prime" and setting not in C1000_PRIME_SETTINGS:
            raise RuntimeError("This original C1000 control is verified only with a legacy session")
        if self.protocol == "prime" and setting == "display_brightness" and (type(value) is not int or value not in (1, 2, 3)):
            raise ValueError("Original C1000 Prime brightness must be 1, 2 or 3")
        from .c1000 import c1000_setting
        command, payload, _expected = c1000_setting(setting, value)
        timestamp = tlv(0xFE, b"\x03" + self._timestamp())
        return self._send(DATA_REQUEST, command, payload + timestamp)

    def _require_c1000_prime_control(self) -> None:
        if self.model != Model.C1000_GEN2 or self.protocol != "prime" or not self.ready:
            raise RuntimeError("Setting controls require a connected C1000 Gen 2 Prime session")

    def network_diagnostics_packet(self) -> bytes:
        """Query radio errors; verified on C2000 and in C1000 radio firmware."""
        if self.protocol != "prime" or not self.ready:
            raise RuntimeError("Network diagnostics require a connected Prime session")
        return self._send(DATA_REQUEST, "4020", tlv(0xA1, self._timestamp()))

    def wifi_rssi_packet(self) -> bytes:
        """Build the separate function-10 RSSI query tested on A1763 Prime."""
        if self.model != Model.C1000_GEN2 or self.protocol != "prime":
            raise ValueError("Wi-Fi RSSI requires C1000 Gen 2 Prime")
        if not self.ready or self._secret is None:
            raise RuntimeError("Wi-Fi RSSI requires a connected Prime session")
        return self._send(RADIO_REQUEST, "4022", tlv(0xA1, b"\x21"))

    def charge_limits_packet(self, upper: int, lower: int) -> bytes:
        """Build the C1000 4103 charge/discharge limit write verified on 1.1.4.9."""
        self._require_c1000_prime_control()
        if (not 80 <= upper <= 100 or upper % 5
                or not 1 <= lower <= 20 or (lower != 1 and lower % 5) or lower >= upper):
            raise ValueError("upper must be 80–100% in 5% steps; lower must be 1, 5, 10, 15, or 20%")
        payload = b"\xa1\x01\x21" + tlv(0xAA, bytes((1, upper))) + tlv(0xAB, bytes((1, lower)))
        return self._send(DATA_REQUEST, "4103", payload)

    def charge_cap_packet(self, upper: int) -> bytes:
        """Build the C2000 4103 upper charge limit write; leave the lower limit untouched."""
        if self.model != Model.C2000_GEN2 or self.protocol != 'prime' or not self.ready:
            raise RuntimeError('Charge-cap control requires a connected C2000 Gen 2 Prime session')
        if not 80 <= upper <= 100 or upper % 5:
            raise ValueError('C2000 charge cap must be 80–100% in 5% steps')
        payload = b'\xa1\x01\x21' + tlv(0xAA, bytes((1, upper)))
        return self._send(DATA_REQUEST, '4103', payload)

    def ac_charging_power_packet(self, watts: int) -> bytes:
        """Build the model-specific AC charging-power limit write."""
        if self.model == Model.C1000:
            return self.c1000_control_packet("ac_charging_power", watts)
        if self.model == Model.C300:
            if type(watts) is not int or watts not in (100, 200, 300, 330):
                raise ValueError("C300 charging power must be 100, 200, 300, or 330 W")
            return self._c300_setting("4044", b"\x02" + watts.to_bytes(2, "little"))
        if self.model not in (Model.C1000_GEN2, Model.C2000_GEN2) or self.protocol != 'prime' or not self.ready:
            raise RuntimeError('Charging-power control requires a connected Gen 2 Prime session')
        maximum = 1800 if self.model == Model.C2000_GEN2 else 1200
        minimum = 300 if self.model == Model.C2000_GEN2 else 100
        if type(watts) is not int or not minimum <= watts <= maximum or watts % 100:
            raise ValueError(f'AC charging power must be {minimum}–{maximum} W in 100 W steps')
        milliseconds = str(int(time.time() * 1000)).encode("ascii")
        payload = (b"\xa1\x01\x21" + tlv(0xA4, b"\x02" + watts.to_bytes(2, "little"))
                   + tlv(0xAB, b"\x02\x00\x00") + tlv(0xFD, b"\x00" + milliseconds))
        return self._send(DATA_REQUEST, "4101", payload)

    def _c300_setting(self, command: str, value: bytes) -> bytes:
        if self.model != Model.C300 or self.protocol != "legacy" or not self.ready:
            raise RuntimeError("C300 control requires a connected legacy session")
        payload = (b"\xa1\x01\x21" + tlv(0xA2, value)
                   + tlv(0xFE, b"\x03" + self._timestamp()))
        return self._send(DATA_REQUEST, command, payload)

    def ac_output_packet(self, enabled: bool) -> bytes:
        """Set C300 or original C1000 AC output; other models are excluded."""
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        if self.model == Model.C1000:
            return self.c1000_control_packet("ac_output_enabled", enabled)
        return self._c300_setting("404a", bytes((1, int(enabled))))

    def light_mode_packet(self, mode: int) -> bytes:
        """Set off/low/medium/high light; original C1000 also supports SOS."""
        if type(mode) is not int or mode not in (0, 1, 2, 3, 4):
            raise ValueError("light mode must be 0, 1, 2, 3, or 4")
        if self.model == Model.C1000:
            return self.c1000_control_packet("light_mode", mode)
        if self.model == Model.C300 and mode == 4:
            raise ValueError("C300 light mode must be 0, 1, 2, or 3")
        return self._c300_setting("404f", bytes((1, mode)))

    def device_timeout_packet(self, minutes: int) -> bytes:
        """Set C1000 device timeout; zero disables this configured timeout.

        Independent sleep behavior may still affect remote access. This packet
        contains no output switch and does not cancel every pending sleep event.
        """
        validate_device_timeout(minutes)
        if self.model == Model.C1000:
            return self.c1000_control_packet("device_timeout", minutes)
        self._require_c1000_prime_control()
        milliseconds = str(int(time.time() * 1000)).encode("ascii")
        payload = (b"\xa1\x01\x21" + tlv(0xA6, b"\x02" + minutes.to_bytes(2, "little"))
                   + tlv(0xFD, b"\x00" + milliseconds))
        return self._send(DATA_REQUEST, "4103", payload)

    def display_timeout_packet(self, seconds: int) -> bytes:
        """Build a verified display timeout write for the selected model."""
        if self.model == Model.C1000:
            return self.c1000_control_packet("display_timeout", seconds)
        if self.model == Model.C300:
            if type(seconds) is not int or seconds not in (30, 60):
                raise ValueError("C300 display timeout is verified only at 30 or 60 seconds")
            return self._c300_setting("4046", b"\x02" + seconds.to_bytes(2, "little"))
        if self.model not in (Model.C1000_GEN2, Model.C2000_GEN2) or self.protocol != 'prime' or not self.ready:
            raise RuntimeError('Display timeout control requires a connected Gen 2 Prime session')
        if self.model == Model.C2000_GEN2 and seconds not in (30, 60):
            raise ValueError('C2000 display timeout is verified only at 30 or 60 seconds')
        if seconds not in (0, 10, 20, 30, 60, 300, 1800):
            raise ValueError('display timeout must be 0, 10, 20, 30, 60, 300, or 1800 seconds')
        milliseconds = str(int(time.time() * 1000)).encode('ascii')
        payload = (b'\xa1\x01\x21' + tlv(0xA4, b'\x02' + seconds.to_bytes(2, 'little'))
                   + tlv(0xFD, b'\x00' + milliseconds))
        return self._send(DATA_REQUEST, '4103', payload)

    def fast_charge_packet(self, enabled: bool) -> bytes:
        """Build original 405e or Gen 2 Prime 4101 fast-charge control."""
        if type(enabled) is not bool:
            raise ValueError('enabled must be a boolean')
        if self.model == Model.C1000:
            return self.c1000_control_packet("fast_charge_enabled", enabled)
        self._require_c1000_prime_control()
        milliseconds = str(int(time.time() * 1000)).encode('ascii')
        payload = (b'\xa1\x01\x21' + tlv(0xA7, bytes((1, int(enabled))))
                   + tlv(0xFD, b'\x00' + milliseconds))
        return self._send(DATA_REQUEST, '4101', payload)

    def temperature_unit_packet(self, fahrenheit: bool) -> bytes:
        return self.c1000_control_packet("temperature_unit_fahrenheit", fahrenheit)

    def power_saving_packet(self, enabled: bool, *, ac: bool) -> bytes:
        if type(ac) is not bool:
            raise ValueError("ac must be a boolean")
        return self.c1000_control_packet(
            "ac_power_saving_mode_enabled" if ac else "dc_power_saving_mode_enabled", enabled)

    def wifi_credentials_packet(self, ssid: str, passphrase: str, account_id: str) -> bytes:
        """Build model-specific 4024 Wi-Fi credentials over the negotiated session.

        This is an experimental packet builder. The Anker app's payload was
        decoded from a private HCI capture, and this builder joined a C1000
        Gen 2 to an isolated WPA2 AP on firmware 1.1.4.9, and the same shape
        joined the tested C2000 Gen 2. ``account_id`` is a 40-character ID;
        the generated local BLE ID also worked in this field.
        Original A1761 uses the same untyped TLVs over legacy CBC on main
        1.5.1/radio 0.1.3.0 and Prime GCM on main 1.7.1/radio 0.3.3.0.
        """
        self._require_wifi_session()
        if len(account_id) != 40 or any(c not in '0123456789abcdefABCDEF' for c in account_id):
            raise ValueError('account_id must be 40 hexadecimal characters')
        network = ssid.encode('utf-8')
        password = passphrase.encode('ascii')
        if not 1 <= len(network) <= 32:
            raise ValueError('SSID must be 1–32 UTF-8 bytes')
        if not 8 <= len(password) <= 63:
            raise ValueError('WPA2 passphrase must be 8–63 ASCII bytes')
        payload = (tlv(0xA1, self._timestamp()) + tlv(0xA2, account_id.encode('ascii'))
                   + tlv(0xA3, network) + tlv(0xA4, b'\x00')
                   + tlv(0xA5, b'\x00') + tlv(0xA6, password))
        return self._send(DATA_REQUEST, '4024', payload)

    def wifi_cloud_config_packet(
        self, account_id: str, api_url: str, posix_timezone: str,
        iana_timezone: str, *, c3_value: str = 'A2', allow_http: bool = False,
        country_code: str = 'US',
    ) -> bytes:
        """Build the model-specific 4025 endpoint and timezone write.

        The meaning of C3 and the 4825 response are not yet established.
        Tags must be ascending: the radio parser silently skips a field that
        follows a higher tag. The earlier reconstructed capture's C3 position
        hid the service/model/timezone fields; keep this opaque field last.
        Original A1761's app-derived layout includes A5 country and omits C3.
        This method does not configure or emulate the remote API.
        """
        self._require_wifi_session()
        if len(account_id) != 40 or any(c not in '0123456789abcdefABCDEF' for c in account_id):
            raise ValueError('account_id must be 40 hexadecimal characters')
        if not (api_url.startswith('https://') or
                (allow_http and api_url.startswith('http://'))) or not api_url.endswith('/'):
            raise ValueError('api_url must be an HTTPS base URL ending in /, unless allow_http=True')
        if self.model == Model.C1000:
            if not isinstance(country_code, str) or not re.fullmatch(r'[A-Z]{2}', country_code):
                raise ValueError('country_code must be two uppercase ASCII letters')
            country = tlv(0xA5, country_code.encode('ascii'))
            extra = b''
            product = b'A1761'
        else:
            if len(c3_value) != 2 or not c3_value.isascii():
                raise ValueError('c3_value must be two ASCII characters')
            country = b''
            extra = tlv(0xC3, c3_value.encode('ascii'))
            product = b'A1783' if self.model == Model.C2000_GEN2 else b'A1763'
        payload = (tlv(0xA1, self._timestamp()) + tlv(0xA2, account_id.encode('ascii'))
                   + tlv(0xA3, api_url.encode('ascii'))
                   + tlv(0xA4, posix_timezone.encode('ascii')) + country
                   + tlv(0xA6, b'anker_power')
                   + tlv(0xA7, product)
                   + tlv(0xA8, iana_timezone.encode('ascii'))
                   + extra)
        return self._send(DATA_REQUEST, '4025', payload)

    def _require_wifi_session(self) -> None:
        protocols = ('legacy', 'prime') if self.model == Model.C1000 else ('prime',)
        if (self.model not in (Model.C1000, Model.C1000_GEN2, Model.C2000_GEN2)
                or self.protocol not in protocols or not self.ready):
            raise RuntimeError('Wi-Fi provisioning requires a connected C1000 or Gen 2 session')

    def feed(self, data: bytes) -> ProtocolUpdate:
        packet = parse_packet(data)
        if packet.pattern == NEGOTIATION:
            return self._negotiate(packet)
        if (packet.pattern == RADIO_REQUEST and packet.command == b"\x48\x22"
                and self.model == Model.C1000_GEN2 and self.protocol == "prime"
                and self.ready and self._secret is not None):
            return ProtocolUpdate(radio_response=(
                packet.command.hex(), self._crypt(packet.payload, False)))
        # The C1000 app's 4824/4825 Wi-Fi acknowledgements use the request
        # pattern even though they arrive as GATT notifications.
        if packet.pattern == DATA_REQUEST and (packet.command[0] == 0x48 or packet.command.hex() in ("4901", "4903")):
            return self._receive_data(packet)
        if packet.pattern == DATA_RESPONSE:
            return self._receive_data(packet)
        return ProtocolUpdate()

    def _derive(self, peer_xy: bytes) -> None:
        if len(peer_xy) != 64:
            raise ValueError("Invalid device ECDH public key")
        peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b"\x04" + peer_xy)
        self._secret = self._private.exchange(ec.ECDH(), peer)

    def _negotiate(self, packet: Packet) -> ProtocolUpdate:
        command = packet.command.hex()
        plain = self._crypt(packet.payload, False)
        values = parse_tlvs(plain)
        outgoing: list[bytes] = []
        if self.protocol == "prime":
            ts = lambda: tlv(0xA1, self._timestamp())
            if command == "4801":
                outgoing.append(self._send(NEGOTIATION, "4003", ts() + tlv(0xA3, b"\x20") + tlv(0xA4, b"\x00\xf0")))
            elif command == "4803":
                outgoing.append(self._send(NEGOTIATION, "4029", ts()))
            elif command == "4829":
                outgoing.append(self._send(NEGOTIATION, "4005", ts() + tlv(0xA3, b"\x20") + tlv(0xA4, b"\x29\x01") + tlv(0xA5, b"\x44") + tlv(0xA6, b"\x02")))
            elif command == "4805":
                outgoing.append(self._send(NEGOTIATION, "4021", tlv(0xA1, self._public[1:])))
            elif command == "4821":
                self._derive(values[0xA1])
                outgoing.append(self._send(NEGOTIATION, "4022", ts() + tlv(0xA3, self._timezone_offset) + tlv(0xA5, self._timezone_posix)))
            elif command == "4822":
                outgoing.append(self._registration())
            elif command == "4827":
                if plain and plain[0] == 9:
                    self.pairing_required = True
                    return ProtocolUpdate(pairing_required=True)
                if not plain or plain[0] != 0:
                    raise RuntimeError(f'Prime registration failed: {plain[:1].hex()}')
                self.pairing_required = False
                self.ready = True
                outgoing.append(self.status_packet())
        else:
            prefix = lambda: self._legacy_prefix()
            if command == "0801":
                outgoing.append(self._send(NEGOTIATION, "0003", prefix() + tlv(0xA3, b"\x20") + tlv(0xA4, b"\x00\xf0")))
            elif command == "0803":
                outgoing.append(self._send(NEGOTIATION, "0029", prefix()))
            elif command == "0829":
                outgoing.append(self._send(NEGOTIATION, "0005", prefix() + tlv(0xA3, b"\x20") + tlv(0xA4, b"\x00\xf0") + tlv(0xA5, b"\x40")))
            elif command == "0805":
                outgoing.append(self._send(NEGOTIATION, "0021", tlv(0xA1, self._public[1:])))
            elif command == "0821":
                self._derive(values[0xA1])
                stage5 = prefix() + tlv(0xA3, b"\x20") + tlv(0xA4, bytes(4)) + tlv(0xA5, b"UTC0")
                outgoing.append(self._send(NEGOTIATION, "4022", stage5))
                self.ready = True
                outgoing.append(self.status_packet())
        return ProtocolUpdate(outgoing=outgoing, ready=self.ready)

    def _receive_data(self, packet: Packet) -> ProtocolUpdate:
        if not self._secret:
            return ProtocolUpdate()
        legacy_station = self.model in (Model.C300, Model.C1000)
        if (packet.command[0] == 0x48 or packet.command.hex() in ("4901", "4903")) and not (legacy_station and packet.command.hex() == "4840"):
            return ProtocolUpdate(response=(packet.command.hex(), self._crypt(packet.payload, False)))
        if legacy_station and packet.command.hex() not in ("c402", "c405", "c840", "0402", "0405", "4840"):
            return ProtocolUpdate()
        payload = packet.payload
        if packet.command[0] in (0xC4, 0xC8, 0xC9):
            if not payload:
                return ProtocolUpdate()
            index, total = payload[0] >> 4, payload[0] & 0x0F
            if index < 1 or total < 1 or index > total:
                return ProtocolUpdate()
            if index == 1:
                self._fragments[packet.command] = []
            fragments = self._fragments.get(packet.command)
            if fragments is None or len(fragments) != index - 1:
                return ProtocolUpdate()
            fragments.append(payload)
            if index != total:
                return ProtocolUpdate()
            self._fragments.pop(packet.command, None)
            candidates = [b"".join(p[1:] for p in fragments), b"".join(fragments)]
        else:
            candidates = [payload]
        for candidate in candidates:
            try:
                plain = self._crypt(candidate, False)
                metrics, values = decode_telemetry(plain, self.model)
                return ProtocolUpdate(telemetry=metrics, raw_tlvs=values)
            except (ValueError, KeyError):
                continue
        return ProtocolUpdate()
