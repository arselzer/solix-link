#!/usr/bin/env python3
"""Bounded MAIN asset-chunk consumer, serializer and chunk-reply replay.

Actual callback registration prefix, internal dispatch, parsed TLV lookup,
chunk staging, MAIN descriptors/serializers, CRC and radio ACK TLV construction
run in synthetic RAM. Parsing/session, queue execution, timer APIs, memory
helpers, transport, logging and application callbacks are substitutes. No file
backend, final application/asset commit, firmware boot or station executes.
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
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
                              UC_ARM_REG_SP, UC_ARM_REG_LR)

from emulate_gen2_asset_transfer_start import TransferMachine, ALLOW as START_ALLOW, FLAGS, SETTINGS, SIZE, TRANSFER, TIMERS
from emulate_clock_semantics import STOP, STACK
from emulate_uart_request_worker import crc16
from replay_io import FIRMWARE_SHA256, firmware_image

CONTEXT, PAYLOAD, REPLY, APP_CALLBACK = 0x20017000, 0x20017400, 0x20017c00, 0x08004000
REGISTRY = 0x2000549c
MORE_ALLOW = ((0x08013f90, 0x08013fc2), (0x08029254, 0x08029264),
              (0x0800b634, 0x0800b640), (0x08022614, 0x0802265a),
              (0x080192c0, 0x080193cc), (0x08022576, 0x080225a6),
              (0x0801c4c4, 0x0801c4fa), (0x080264d0, 0x08026554),
              (0x0801c1b8, 0x0801c25c), (0x08017ccc, 0x08017d02),
              (0x0802d874, 0x0802d880), (0x08022538, 0x08022576),
              (0x0801c2d8, 0x0801c308), (0x080264a4, 0x080264d0))


class Machine(TransferMachine):
    def __init__(self) -> None:
        super().__init__(busy=True)
        self.stop_at = STOP
        self.entry_instructions = self.maximum_entry_instructions = 0
        self.descriptors, self.app_calls, self.radio_acks = [], [], []
        self.uc.mem_write(TRANSFER, struct.pack("<I", APP_CALLBACK | 1))
        self.run_prefix()

    def run_prefix(self) -> None:
        self.stop_at = 0x08013fc2
        self.run(0x08013f90, 0)
        self.stop_at = STOP
        assert int.from_bytes(self.uc.mem_read(REGISTRY + 20, 4), "little") == 0x080192c1

    def run(self, address: int, argument: int) -> None:
        self.stopped = False
        self.entry_instructions = 0
        self.uc.reg_write(UC_ARM_REG_R0, argument)
        self.uc.reg_write(UC_ARM_REG_SP, STACK)
        self.uc.reg_write(UC_ARM_REG_LR, STOP | 1)
        # The bitwise CRC over 1,038 bytes takes roughly 70,000 instructions.
        self.uc.emu_start(address | 1, 0, count=100000)
        assert self.stopped, "Instruction bound exceeded before return"
        self.maximum_entry_instructions = max(self.maximum_entry_instructions, self.entry_instructions)

    def step(self, uc, address, size, data) -> None:
        self.entry_instructions += 1
        self.visited.add(address)
        r0, r1, r2, r3 = [uc.reg_read(r) for r in
                          (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)]
        if address == self.stop_at:
            self.stopped = True
            uc.emu_stop()
        elif address == 0x080052da:
            assert 0 < r1 <= 0x414
            self.write_hook(uc, 0, r0, r1, 0, None)
            uc.mem_write(r0, bytes(r1))
            self.back(r0)
        elif address == 0x080052a8:
            assert 0 < r2 <= 1024
            self.write_hook(uc, 0, r0, r2, 0, None)
            assert not (r1 < SETTINGS + SIZE and r1 + r2 > SETTINGS)
            self.reads.update(range(r1, r1 + r2))
            uc.mem_write(r0, bytes(uc.mem_read(r1, r2)))
            self.back(r0)
        elif address == 0x0800e59c:
            assert r1 == 2
            raw = bytes(uc.mem_read(r0, 20))
            callback, serializer, pointer, timeout, length = struct.unpack("<IIIIH", raw[:18])
            assert (callback, serializer, timeout, length) in (
                (0x0801c1b9, 0x080264d1, 1000, 19),
                (0x08017bd1, 0x080264a5, 2000, 20))
            self.descriptors.append({"callback": callback, "serializer": serializer, "pointer": pointer,
                                     "timeout_argument": timeout, "point_raw": length})
            self.back(1)
        elif address == 0x08008f2c:
            assert r0 == 0 and r2 in (16, 1040)
            frame = bytes(uc.mem_read(r1, r2))
            assert frame[:4] == b"MAIN" and crc16(frame) == 0
            self.transmitted.append(frame)
            self.back(1)
        elif address == 0x08020180:
            assert r1 == 16
            self.back()  # Packet logging only.
        elif address == 0x080273c8:
            assert r0 == CONTEXT and r2 == 1
            body = bytes(uc.mem_read(r1, r2))
            assert body == b"\1"
            self.responses.append(body)
            self.back()  # Error response delivery only.
        elif address == 0x08013f20:
            assert r0 == 16 and r1 == 0x83e and r3 == 3
            body = bytes(uc.mem_read(r2, r3))
            assert body[:2] == b"\xa1\1" and body[2] in (0, 1)
            self.radio_acks.append(body[2])
            self.back(1)  # Actual ACK body; outer framing/delivery excluded.
        elif address == APP_CALLBACK:
            assert r0 == 0 and r1 in (1, 4)
            self.app_calls.append({"success_argument": r0, "reason_argument": r1})
            self.back()
        elif any(lo <= address < hi for lo, hi in MORE_ALLOW):
            return
        elif address in (STOP, 0x0800d284, 0x08010998, 0x0801095c) or any(
                lo <= address < hi for lo, hi in START_ALLOW):
            super().step(uc, address, size, data)
        else:
            raise RuntimeError(f"Unexpected instruction {address:08x}")

    def chunk(self, *, offset: int = 0, total: int = 2048, reported_length: int = 1024,
              omit: int | None = None, inactive: bool = False, busy: bool = False) -> dict:
        assert 0 <= reported_length <= 65535 and 0 <= offset <= 0xffffffff and 0 <= total <= 0xffffffff
        self.uc.mem_write(TRANSFER + 8, bytes((int(not inactive),)))
        self.uc.mem_write(TRANSFER + 26, bytes((int(busy),)))
        self.uc.mem_write(CONTEXT, bytes(24 + 100 * 6))
        self.uc.mem_write(PAYLOAD, struct.pack("<IIH", offset, total, reported_length)
                          + bytes((i * 11 + 9) & 255 for i in range(1024)))
        # A3's low length byte is retained in the parsed table; the high byte
        # remains at the pointed-to address. This is a host-seeded parser shape.
        for index, (tag, length, relative) in enumerate(((0xa1, 4, 0), (0xa2, 4, 4),
                                                        (0xa3, reported_length & 255, 9))):
            if tag != omit:
                self.uc.mem_write(CONTEXT + 24 + 6 * index,
                                  struct.pack("<BBI", tag, length, PAYLOAD + relative))
        self.uc.reg_write(UC_ARM_REG_R1, CONTEXT)
        self.run(0x08022614, 0x3e)
        self.serialize_new(0)
        return self.summary()

    def serialize_new(self, start: int) -> None:
        # Explicit host delivery to serializers, not a running queue worker.
        for row in tuple(self.descriptors[start:]):
            self.run(row["serializer"] & ~1, row["pointer"])

    def reply(self, *, valid: bool = True, malformed: str | None = None, retry: int = 0) -> dict:
        frame = bytearray(16)
        frame[10:14] = b"\x13\0\xaa\xee"
        if malformed is not None:
            assert malformed in ("point", "status", "marker")
            frame[{"point": 10, "status": 11, "marker": 12}[malformed]] ^= 1
        self.uc.mem_write(REPLY, bytes(frame))
        self.uc.mem_write(TIMERS + 22, bytes((retry,)))
        self.uc.reg_write(UC_ARM_REG_R1, REPLY if valid else 0)
        start = len(self.descriptors)
        self.run(0x0801c1b8, int(valid))
        self.serialize_new(start)
        return self.summary()

    def summary(self) -> dict:
        assert bytes(self.uc.mem_read(SETTINGS, SIZE)) == self.baseline
        assert bytes(self.uc.mem_read(FLAGS, 8)) == self.outputs
        assert not self.reads.intersection(range(SETTINGS, SETTINGS + SIZE))
        state = bytes(self.uc.mem_read(TRANSFER + 12, 15))
        return {"transfer_active": self.uc.mem_read(TRANSFER + 8, 1)[0], "chunk_pending": state[14],
                "stored_total": int.from_bytes(state[4:8], "little"),
                "stored_offset": int.from_bytes(state[8:12], "little"),
                "stored_reported_length": int.from_bytes(state[12:14], "little"),
                "retry_byte": self.uc.mem_read(TIMERS + 22, 1)[0],
                "queued_points_raw": [r["point_raw"] for r in self.descriptors],
                "frames": [{"bytes": len(f), "header_words_le": list(struct.unpack_from("<4H", f, 4)),
                            "sha256": hashlib.sha256(f).hexdigest(),
                            "block_index_u16_le": int.from_bytes(f[12:14], "little") if len(f) == 1040 else None}
                           for f in self.transmitted],
                "radio_083e_ack_statuses": self.radio_acks, "application_callback_substitutes": self.app_calls,
                "immediate_error_response_statuses": [r[0] for r in self.responses],
                "saved_settings_bytes_read": 0, "saved_settings_and_output_flags_preserved": True,
                "maximum_entry_instructions": self.maximum_entry_instructions,
                "distinct_instruction_addresses": len(self.visited)}


def suite() -> dict:
    firmware_image()
    rows = []
    for name, kwargs, queued, error in (
        ("ordinary_chunk", {}, True, False), ("final_padded_chunk", {"total": 1}, True, False),
        ("second_block", {"offset": 1024}, True, False),
        ("reported_one", {"reported_length": 1}, True, False),
        ("reported_2048", {"reported_length": 2048}, True, False),
        ("maximum_total", {"total": 0x28000}, True, False),
        ("total_too_large", {"total": 0x28001}, False, True),
        ("zero_total", {"total": 0}, False, True),
        ("offset_at_end", {"offset": 2048}, False, True),
        ("missing_a1_retains_zero_offset", {"omit": 0xa1}, True, False),
        ("missing_a2_retains_zero_total", {"omit": 0xa2}, False, True),
        ("missing_a3", {"omit": 0xa3}, False, False),
        ("inactive", {"inactive": True}, False, False), ("pending", {"busy": True}, False, False),
    ):
        m = Machine()
        result = m.chunk(**kwargs)
        assert result["queued_points_raw"] == ([19] if queued else [])
        assert result["chunk_pending"] == int(queued or kwargs.get("busy", False))
        assert result["application_callback_substitutes"] == ([{"success_argument": 0, "reason_argument": 1}] if error else [])
        assert result["immediate_error_response_statuses"] == ([1] if error else [])
        if queued:
            assert len(m.transmitted) == 1 and m.transmitted[0][14:1038] == bytes((i * 11 + 9) & 255 for i in range(1024))
            assert result["frames"][0]["header_words_le"] == [16, 5, 1028, 19]
            assert result["frames"][0]["block_index_u16_le"] == kwargs.get("offset", 0) >> 10
        rows.append({"case": name, **result})
    for name, chunk_kwargs, reply_kwargs in (
        ("ack_more_data", {}, {}), ("ack_final", {"total": 1024}, {}),
        ("ack_final_padding", {"total": 1}, {}),
        ("ack_reported_one_more_data", {"reported_length": 1}, {}),
        ("retry_first", {}, {"valid": False}),
        ("retry_fourth", {}, {"valid": False, "retry": 3}),
        ("retry_byte_wrap", {}, {"valid": False, "retry": 255}),
        ("bad_point", {}, {"malformed": "point"}),
        ("bad_status", {}, {"malformed": "status"}),
        ("bad_marker", {}, {"malformed": "marker"}),
    ):
        m = Machine()
        m.chunk(**chunk_kwargs)
        result = m.reply(**reply_kwargs)
        if name.startswith("ack_"):
            assert result["radio_083e_ack_statuses"] == [0] and result["chunk_pending"] == 0
            assert result["queued_points_raw"] == ([19, 20] if name in ("ack_final", "ack_final_padding") else [19])
        elif name in ("retry_first", "retry_byte_wrap"):
            assert result["queued_points_raw"] == [19, 19]
            assert m.transmitted[0] == m.transmitted[1]
            assert not result["radio_083e_ack_statuses"]
        elif name == "retry_fourth":
            assert result["application_callback_substitutes"] == [{"success_argument": 0, "reason_argument": 4}]
            assert result["radio_083e_ack_statuses"] == [1] and result["transfer_active"] == 0
        else:
            assert result["chunk_pending"] == 1 and result["queued_points_raw"] == [19]
            assert not result["radio_083e_ack_statuses"]
        rows.append({"case": name, **result})
    negatives = negative_guards()
    return {"model": "A1763", "main_version": "1.1.4.9", "firmware_sha256": FIRMWARE_SHA256,
            "cases": len(rows), "results": rows, "instruction_limit_per_entry": 100000,
            "negative_guards": negatives,
            "complete_settings_export": False, "physical_transport_verified": False,
            "station_commands_sent": 0, "limits": __doc__.strip()}


def negative_guards() -> list[str]:
    rejected = []
    m = Machine()
    for name, action in (
        ("reported_length_outside_u16", lambda: m.chunk(reported_length=65536)),
        ("firmware_startup_excluded", lambda: m.run(0x080309c8, 0)),
        ("final_application_callback_excluded", lambda: m.run(0x08017bd0, 0)),
        ("saved_settings_write_excluded", lambda: m.write_hook(m.uc, 0, SETTINGS, 1, 0, None)),
    ):
        try:
            action()
        except (AssertionError, RuntimeError):
            rejected.append(name)
        else:
            raise AssertionError(f"Accepted {name}")
    return rejected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    result = suite()
    directory = Path(__file__).resolve().parent
    sources = sorted({Path(m.__file__).resolve() for m in tuple(sys.modules.values())
                      if getattr(m, "__file__", None) and Path(m.__file__).resolve().parent == directory})
    manifest = {"firmware_sha256": FIRMWARE_SHA256, "python": platform.python_version(), "unicorn": unicorn.__version__,
                "sources": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}}
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    for suffix, value in (("results", result), ("manifest", manifest)):
        p = args.output_dir / f"gen2-asset-chunk-consumer-{suffix}.json"
        p.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
        p.chmod(0o600)
    print(json.dumps({"synthetic_cases": result["cases"],
                      "negative_guards": len(result["negative_guards"]), "station_commands_sent": 0}))


if __name__ == "__main__":
    main()
