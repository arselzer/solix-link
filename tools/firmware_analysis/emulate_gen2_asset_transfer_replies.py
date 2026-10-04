#!/usr/bin/env python3
"""Bounded A1763 asset replies, timeout, radio request and serializer replay.

Actual main-controller instructions execute with synthetic RAM. Timer APIs,
queue insertion, libc, allocation, logging, application callbacks, radio
framing/delivery and UART delivery are explicit substitutes. No queue worker,
file backend, radio firmware, network, firmware boot or station runs. Asset
state changes are not passive readback; application callbacks may change state
outside this scope. This is not a generic file export or a transport test.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import struct
import sys

import unicorn
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1

from emulate_gen2_asset_transfer_start import (
    ALLOW as START_ALLOW, FLAGS, INPUT, SETTINGS, SIZE, TIMERS, TRANSFER, TransferMachine,
)
from emulate_clock_semantics import STOP
from emulate_uart_request_worker import crc16
from replay_io import FIRMWARE_SHA256, firmware_image

REPLY, REQUEST, PAYLOAD = 0x20018100, 0x20018200, 0x20018300
ALLOCATED, CALLBACK = 0x20019000, 0x08004000
MORE_ALLOW = (
    (0x08021f04, 0x08021f72),  # initial MAIN response
    (0x08017adc, 0x08017b1e),  # registered timeout, not a chunk worker
    (0x0801c168, 0x0801c198),  # second MAIN descriptor
    (0x080263ec, 0x08026408),  # fixed second MAIN frame
    (0x08021e30, 0x08021e9c),  # second MAIN response
    (0x08030c8c, 0x08030cc8),  # radio resource-request descriptor
    (0x080126a8, 0x0801275e),  # resource locator serializer
    (0x0803058c, 0x080305de),  # radio request response
    (0x0802d874, 0x0802d880),  # TLV wrapper
    (0x08022538, 0x08022576),  # TLV builder
)


class ReplyMachine(TransferMachine):
    def __init__(self, *, locator: bytes = b"synthetic-asset", callback: bool = True,
                 queue_result: int = 1) -> None:
        assert 0 < len(locator) < 128 and b"\0" not in locator
        super().__init__(input_bytes=locator + b"\0", queue_result=queue_result)
        self.uc.mem_write(TRANSFER, struct.pack("<II", CALLBACK | 1 if callback else 0, INPUT))
        self.uc.mem_write(TRANSFER + 8, b"\x01")
        self.descriptors, self.application_calls, self.radio_payloads = [], [], []
        self.substitute_reads: set[int] = set()
        self.locator = locator

    def text(self, address: int) -> bytes:
        if 0x20000000 <= address < 0x20020000:
            limit = min(128, 0x20020000 - address)
        else:
            assert 0x08005000 <= address < 0x08035800
            limit = min(128, 0x08035800 - address)
        raw = bytes(self.uc.mem_read(address, limit))
        assert b"\0" in raw, "Unterminated bounded libc input"
        value = raw.split(b"\0", 1)[0]
        self.substitute_reads.update(range(address, address + len(value) + 1))
        return value

    def step(self, uc: unicorn.Uc, address: int, size: int, data: object) -> None:
        self.visited.add(address)
        r0, r1, r2, r3 = (uc.reg_read(reg) for reg in
                          (unicorn.arm_const.UC_ARM_REG_R0, unicorn.arm_const.UC_ARM_REG_R1,
                           unicorn.arm_const.UC_ARM_REG_R2, unicorn.arm_const.UC_ARM_REG_R3))
        if address == CALLBACK:
            assert r0 == 0 and r1 in (1, 2, 3, 6, 7)
            self.application_calls.append({"success_argument": r0, "reason_argument": r1})
            self.back()
        elif address in (0x0800e59c, 0x08016a70):
            expected_lane = 2 if address == 0x0800e59c else 0
            assert r1 == expected_lane
            assert 0x20000000 <= r0 <= 0x20020000 - 20
            raw = bytes(uc.mem_read(r0, 20))
            callback, serializer, pointer, timeout, length = struct.unpack("<IIIIH", raw[:18])
            if address == 0x0800e59c:
                assert (callback, serializer, timeout, length) in (
                    (0x08021f05, 0x08026559, 600, 16),
                    (0x08021e31, 0x080263ed, 14000, 18),
                )
                if length == 18:
                    assert pointer == 0
            else:
                assert callback == 0x0803058d and timeout == 1000 and length == 0x3d
                assert raw[18] == 0x10
                assert (pointer == INPUT and serializer == 0x080126a9) or (pointer == serializer == 0)
            self.descriptors.append({"lane": expected_lane, "callback": f"{callback:08x}",
                                     "serializer": f"{serializer:08x}", "pointer": pointer,
                                     "timeout_argument": timeout, "length_or_command_raw": length})
            self.back(self.queue_result)
        elif address == 0x08008f2c:
            assert r0 == 0 and r2 in (16, 21)
            frame = bytes(uc.mem_read(r1, r2))
            assert frame[:4] == b"MAIN" and crc16(frame) == 0
            self.transmitted.append(frame)
            self.back(1)
        elif address == 0x0803124c:
            assert r0 == 512
            uc.mem_write(ALLOCATED, bytes(512))
            self.back(ALLOCATED)
        elif address == 0x08005340:
            assert r0 == INPUT
            self.back(len(self.text(r0)))
        elif address == 0x0800534e:
            left, right = self.text(r0), self.text(r1)
            self.back(0 if left == right else 1)
        elif address == 0x080052a8:
            assert 0 < r2 <= 128
            assert 0x20000000 <= r0 < r0 + r2 <= 0x20020000
            assert 0x20000000 <= r1 < r1 + r2 <= 0x20020000
            assert not (r0 < SETTINGS + SIZE and r0 + r2 > SETTINGS)
            assert not (r0 < FLAGS + 8 and r0 + r2 > FLAGS)
            self.substitute_reads.update(range(r1, r1 + r2))
            uc.mem_write(r0, bytes(uc.mem_read(r1, r2)))
            self.back(r0)
        elif address == 0x08013f20:
            assert r0 == 0x10 and r1 == 0x3d and r2 == ALLOCATED
            assert r3 == 13 + len(self.locator)
            body = bytes(uc.mem_read(r2, r3))
            expected = (b"\xa1\x04" + bytes(4) + b"\xa2\x02\x00\x04\xa3"
                        + len(self.locator).to_bytes(2, "little") + self.locator)
            assert body == expected
            self.radio_payloads.append(body)
            self.back(1)
        elif any(lo <= address < hi for lo, hi in MORE_ALLOW):
            return
        else:
            if address not in (STOP, 0x080052da, 0x08010998, 0x0801095c, 0x0800d284):
                if not any(lo <= address < hi for lo, hi in START_ALLOW):
                    raise RuntimeError(f"Unexpected instruction {address:08x}")
            super().step(uc, address, size, data)

    def response(self, *, stage: int, valid: bool = True, retry: int = 0,
                 malformed: str | None = None) -> None:
        assert stage in (0x10, 0x12)
        frame = bytearray(16)
        frame[10:14] = bytes((stage, 0, 0xaa, 0xee))
        if malformed == "marker":
            frame[12] = 0
        elif malformed == "point":
            frame[10] ^= 1
        elif malformed == "status":
            frame[11] = 1
        else:
            assert malformed is None
        self.uc.mem_write(REPLY, bytes(frame))
        self.uc.mem_write(TIMERS + (24 if stage == 0x10 else 23), bytes((retry,)))
        self.uc.reg_write(UC_ARM_REG_R1, REPLY if valid else 0)
        self.run(0x08021f04 if stage == 0x10 else 0x08021e30, int(valid))

    def timeout(self, text: bytes | None) -> None:
        pointer = 0
        if text is not None:
            assert len(text) < 128
            pointer = REPLY
            self.uc.mem_write(pointer, text + b"\0")
        self.uc.reg_write(UC_ARM_REG_R1, pointer)
        self.run(0x08017adc, 7)

    def radio_response(self, status: int | None) -> None:
        if status is not None:
            self.uc.mem_write(PAYLOAD, bytes((status,)))
            self.uc.mem_write(REQUEST + 20, struct.pack("<I", PAYLOAD))
        self.uc.reg_write(UC_ARM_REG_R1, REQUEST if status is not None else 0)
        self.run(0x0803058c, int(status is not None))

    def serialize_queued(self) -> None:
        # Explicit host entry, not an actual queue/timer execution.
        for row in tuple(self.descriptors):
            entry = int(row["serializer"], 16)
            if entry:
                self.run(entry & ~1, row["pointer"])

    def result(self) -> dict:
        assert bytes(self.uc.mem_read(SETTINGS, SIZE)) == self.baseline
        assert bytes(self.uc.mem_read(FLAGS, 8)) == self.outputs
        assert not (self.reads | self.substitute_reads).intersection(range(SETTINGS, SETTINGS + SIZE))
        return {"descriptors": self.descriptors,
                "application_callback_substitutes": self.application_calls,
                "transfer_active": self.uc.mem_read(TRANSFER + 8, 1)[0],
                "initial_retry_byte": self.uc.mem_read(TIMERS + 24, 1)[0],
                "second_retry_byte": self.uc.mem_read(TIMERS + 23, 1)[0],
                "timer_calls": self.timer_calls,
                "main_frame_lengths": [len(frame) for frame in self.transmitted],
                "main_frame_sha256": [hashlib.sha256(frame).hexdigest() for frame in self.transmitted],
                "radio_payload_lengths": [len(body) for body in self.radio_payloads],
                "radio_payload_sha256": [hashlib.sha256(body).hexdigest() for body in self.radio_payloads],
                "saved_settings_bytes_read": 0,
                "settings_and_output_flags_preserved_with_callbacks_substituted": True,
                "distinct_instruction_addresses": len(self.visited)}


def suite() -> dict:
    firmware_image()
    rows = []
    for stage in (0x10, 0x12):
        for label, valid, malformed, retry in (
            ("success", True, None, 0), ("no_response", False, None, 0),
            ("bad_marker", True, "marker", 0), ("wrong_point", True, "point", 0),
            ("error_status", True, "status", 0), ("fourth_failure", False, None, 3),
            ("retry_u8_wrap", False, None, 255),
        ):
            machine = ReplyMachine()
            machine.response(stage=stage, valid=valid, malformed=malformed, retry=retry)
            machine.serialize_queued()
            result = machine.result()
            if label == "success":
                assert len(result["descriptors"]) == 1
                assert result["descriptors"][0]["lane"] == (2 if stage == 0x10 else 0)
                assert not result["application_callback_substitutes"]
            elif label == "fourth_failure":
                assert result["transfer_active"] == 0 and not result["descriptors"]
                assert result["application_callback_substitutes"] == [
                    {"success_argument": 0, "reason_argument": 2 if stage == 0x10 else 3}]
            else:
                assert len(result["descriptors"]) == 1 and result["descriptors"][0]["lane"] == 2
                assert result["transfer_active"] == 1 and not result["application_callback_substitutes"]
            retry_key = "initial_retry_byte" if stage == 0x10 else "second_retry_byte"
            assert result[retry_key] == (0 if label in ("success", "fourth_failure", "retry_u8_wrap") else 1)
            rows.append({"stage_raw": stage, "case": label, **result})
        machine = ReplyMachine(callback=False)
        machine.response(stage=stage, valid=False, retry=3)
        result = machine.result()
        assert result["transfer_active"] == 0 and not result["application_callback_substitutes"]
        rows.append({"stage_raw": stage, "case": "fourth_failure_no_callback", **result})
    for locator, queue_result in ((b"synthetic-asset", 0), (b"sysPara", 1), (b"synthetic-other", 1)):
        machine = ReplyMachine(locator=locator, queue_result=queue_result)
        machine.response(stage=0x10)
        machine.response(stage=0x12)
        machine.serialize_queued()
        result = machine.result()
        assert result["transfer_active"] == 1 and len(result["radio_payload_lengths"]) == 1
        rows.append({"case": "two_host_delivered_successes", "synthetic_locator": locator.decode(),
                     "substituted_queue_result": queue_result, **result})
    for callback in (False, True):
        for label, text_value, reason in (("null_text", None, 7), ("matching_timeout_text", b"timeOut", 6),
                                          ("other_text", b"synthetic", 7)):
            machine = ReplyMachine(callback=callback)
            machine.timeout(text_value)
            result = machine.result()
            assert result["transfer_active"] == 0 and not result["descriptors"]
            expected = [{"success_argument": 0, "reason_argument": reason}] if callback else []
            assert result["application_callback_substitutes"] == expected
            rows.append({"case": label, "application_callback_present": callback, **result})
    for status in (None, 0, 1, 2, 255):
        machine = ReplyMachine()
        machine.radio_response(status)
        result = machine.result()
        if status in (None, 0):
            assert len(result["descriptors"]) == 1
            assert result["descriptors"][0]["pointer"] == 0
            assert result["descriptors"][0]["serializer"] == "00000000"
        elif status == 2:
            assert result["transfer_active"] == 1 and not result["descriptors"]
        else:
            assert result["transfer_active"] == 0
            assert result["application_callback_substitutes"] == [{"success_argument": 0, "reason_argument": 1}]
        rows.append({"case": "radio_reply", "status_raw": status, **result})
    assert len(rows) == 30
    rejected = []
    for label, operation in (
        ("empty_locator", lambda: ReplyMachine(locator=b"")),
        ("oversize_locator", lambda: ReplyMachine(locator=bytes(128))),
        ("unterminated_libc_input", unterminated_input),
        ("unlisted_instruction_entry", lambda: ReplyMachine().run(0x08017b4c, 0)),
    ):
        try:
            operation()
        except (AssertionError, RuntimeError):
            rejected.append(label)
        else:
            raise AssertionError(f"Negative guard failed: {label}")
    return {"model": "A1763", "main_version": "1.1.4.9", "firmware_sha256": FIRMWARE_SHA256,
            "cases": len(rows), "results": rows, "station_commands_sent": 0,
            "negative_cases_rejected": rejected,
            "complete_settings_export": False, "physical_transport_verified": False,
            "passive_query": False, "instruction_limit_per_entry": 3000,
            "radio_serializer": {"internal_function_raw": "0010", "internal_command_raw": "003d",
                                 "a1_value_u32_le": 0, "a2_value_u16_le": 1024,
                                 "a3_length_encoding": "u16_le", "source": "supplied locator string",
                                 "external_opcode_established": False},
            "limits": __doc__.strip()}


def unterminated_input() -> None:
    machine = ReplyMachine()
    machine.uc.mem_write(INPUT, b"x" * 128)
    machine.text(INPUT)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    result = suite()
    directory = Path(__file__).resolve().parent
    sources = sorted({Path(module.__file__).resolve() for module in tuple(sys.modules.values())
                      if getattr(module, "__file__", None) and Path(module.__file__).resolve().parent == directory})
    manifest = {"firmware_sha256": FIRMWARE_SHA256, "python": platform.python_version(),
                "unicorn": unicorn.__version__,
                "sources": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}}
    args.output_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    for suffix, value in (("results", result), ("manifest", manifest)):
        path = args.output_dir / f"gen2-asset-transfer-replies-{suffix}.json"
        path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
        path.chmod(0o600)
    print(json.dumps({"synthetic_cases": result["cases"], "station_commands_sent": 0,
                      "complete_settings_export": False}))


if __name__ == "__main__":
    main()
