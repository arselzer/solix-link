#!/usr/bin/env python3
"""Bounded A1763 asset-transfer startup and internal metadata-frame replay.

Actual starter, transfer reset, readiness, descriptor builder, serializer and
CRC execute with synthetic RAM. Timer APIs, queue insertion, logger, memset
and UART delivery are explicit substitutes. No worker, file backend, transport,
radio, firmware boot or station runs. This route changes transfer state.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
from pathlib import Path
import platform
import struct
import sys

import unicorn
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3
from emulate_clock_semantics import ClockMachine
from emulate_uart_request_worker import crc16
from replay_io import FIRMWARE_SHA256, firmware_image

SETTINGS, SIZE = 0x20001d48, 415
FLAGS, TRANSFER, TIMERS, INPUT = 0x20000164, 0x20002a44, 0x20000214, 0x20018000
ALLOW = ((0x0802c264, 0x0802c2c4), (0x08029978, 0x0802999c),
         (0x0802c43c, 0x0802c44a), (0x0800d1d8, 0x0800d1f2),
         (0x0801c504, 0x0801c53e), (0x08026558, 0x080265dc),
         (0x0800d300, 0x0800d330))


class TransferMachine(ClockMachine):
    def __init__(self, *, ready: bool = True, busy: bool = False, timer: int = 0,
                 queue_result: int = 1, input_bytes: bytes = b"synthetic") -> None:
        super().__init__()
        self.uc.mem_write(SETTINGS, bytes((i * 17 + 3) & 255 for i in range(SIZE)))
        self.uc.mem_write(FLAGS, b"\x31\x00\x00\x00\x5a\xa5\x5a\xa5")
        self.uc.mem_write(INPUT, input_bytes + bytes(128 - len(input_bytes)))
        self.uc.mem_write(0x200007af, bytes((int(ready),)))
        self.uc.mem_write(0x200007b6, b"\x02")
        self.uc.mem_write(TRANSFER + 8, bytes((int(busy),)))
        self.uc.mem_write(TIMERS + 3, bytes((timer,)))
        self.uc.mem_write(TIMERS + 4, b"\x09")
        self.uc.mem_write(0x20002a44, struct.pack("<II", 0x08029e19, INPUT))
        self.uc.mem_write(0x20002a50, bytes(16))
        self.queue_result = queue_result
        self.queue, self.timer_calls, self.transmitted = [], [], []
        self.reads, self.writes, self.visited = set(), set(), set()
        self.baseline = bytes(self.uc.mem_read(SETTINGS, SIZE))
        self.outputs = bytes(self.uc.mem_read(FLAGS, 8))
        self.uc.mem_protect(0x08000000, 0x40000, unicorn.UC_PROT_READ | unicorn.UC_PROT_EXEC)
        self.uc.mem_protect(0x40002000, 0x1000, unicorn.UC_PROT_READ)
        self.uc.hook_add(unicorn.UC_HOOK_MEM_READ, self.read_hook)
        self.uc.hook_add(unicorn.UC_HOOK_MEM_WRITE, self.write_hook)

    def read_hook(self, _uc: unicorn.Uc, _access: int, address: int, size: int,
                  _value: int, _data: object) -> None:
        self.reads.update(range(address, address + size))

    def write_hook(self, _uc: unicorn.Uc, _access: int, address: int, size: int,
                   _value: int, _data: object) -> None:
        assert 0x20000000 <= address < address + size <= 0x20020000
        assert not (address < SETTINGS + SIZE and address + size > SETTINGS)
        assert not (address < FLAGS + 8 and address + size > FLAGS)
        self.writes.update(range(address, address + size))

    def step(self, uc: unicorn.Uc, address: int, size: int, data: object) -> None:
        self.visited.add(address)
        r0, r1, r2, r3 = (uc.reg_read(r) for r in
                          (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3))
        if address == 0x080052da:
            assert 0 < r1 <= 32
            uc.mem_write(r0, bytes(r1))
            self.back(r0)
        elif address in (0x08010998, 0x0801095c):
            self.timer_calls.append({"operation": "stop" if address == 0x08010998 else "start", "id": r0})
            self.back(1)
        elif address == 0x080105cc:
            # Registration only; no timer callback runs. The ID is synthetic.
            assert r0 == 180000 and r1 == 0x08017add and r3 == TIMERS + 3
            uc.mem_write(r3, b"\x07")
            self.timer_calls.append({"operation": "create", "period_argument": r0, "callback": f"{r1:08x}"})
            self.back(1)
        elif address == 0x0800e59c:
            assert r1 == 2
            descriptor = bytes(uc.mem_read(r0, 20))
            callback, serializer, payload, timeout, length = struct.unpack("<IIIIH", descriptor[:18])
            assert callback == 0x08021f05 and serializer == 0x08026559
            assert timeout == 600 and length == 16
            value = bytes(uc.mem_read(payload, 7))
            assert value == b"\x01\x98\x00\x00\x00\x00\x00"
            self.queue.append({"callback": callback, "serializer": serializer, "payload_address": payload,
                               "payload": value, "timeout": timeout, "length": length})
            self.back(self.queue_result)
        elif address == 0x08008f2c:
            assert r0 == 0 and r2 == 21
            frame = bytes(uc.mem_read(r1, r2))
            assert frame[:4] == b"MAIN" and crc16(frame) == 0
            self.transmitted.append(frame)
            self.back(1)
        elif any(lo <= address < hi for lo, hi in ALLOW):
            return
        else:
            super().step(uc, address, size, data)

    def exercise(self, non_null: bool = True) -> dict:
        self.uc.reg_write(UC_ARM_REG_R1, 0x08029e19)
        self.run(0x0802c264, INPUT if non_null else 0)
        returned = self.uc.reg_read(UC_ARM_REG_R0)
        state = bytes(self.uc.mem_read(TRANSFER, 9))
        if self.queue:
            self.run(0x08026558, self.queue[0]["payload_address"])
            assert len(self.transmitted) == 1
            assert self.transmitted[0][12:19] == self.queue[0]["payload"]
        assert bytes(self.uc.mem_read(SETTINGS, SIZE)) == self.baseline
        assert bytes(self.uc.mem_read(FLAGS, 8)) == self.outputs
        assert not self.reads.intersection(range(SETTINGS, SETTINGS + SIZE))
        assert not self.reads.intersection(range(INPUT, INPUT + 128))
        return {"returned": returned, "queue_requests": len(self.queue),
                "transfer_active": state[8], "timer_calls": self.timer_calls,
                "frame_bytes": len(self.transmitted[0]) if self.transmitted else 0,
                "frame_sha256": hashlib.sha256(self.transmitted[0]).hexdigest() if self.transmitted else None,
                "saved_settings_bytes_read": 0, "input_contents_bytes_read": 0,
                "settings_and_output_flags_preserved": True,
                "distinct_instruction_addresses": len(self.visited)}


def suite() -> dict:
    firmware_image()
    rows, hashes = [], set()
    for queue_result, timer, variant in itertools.product((0, 1), (0, 7), (0, 1)):
        machine = TransferMachine(queue_result=queue_result, timer=timer,
                                  input_bytes=b"sysPara\0" if variant else b"clock_asset\0")
        result = machine.exercise()
        assert result["returned"] == queue_result
        assert result["queue_requests"] == result["transfer_active"] == 1
        hashes.add(result["frame_sha256"])
        rows.append({"queue_result": queue_result, "existing_timer": bool(timer),
                     "synthetic_input_variant": variant, **result})
    assert len(hashes) == 1
    for label, options, non_null in (("null_pointer", {}, False),
                                      ("busy", {"busy": True}, True),
                                      ("not_ready", {"ready": False}, True)):
        result = TransferMachine(**options).exercise(non_null)
        assert result["returned"] == result["queue_requests"] == result["frame_bytes"] == 0
        assert not result["timer_calls"]
        rows.append({"case": label, **result})
    return {"model": "A1763", "main_version": "1.1.4.9", "firmware_sha256": FIRMWARE_SHA256,
            "cases": len(rows), "results": rows, "complete_settings_export": False,
            "physical_transport_verified": False, "passive_query": False,
            "station_commands_sent": 0, "instruction_limit_per_entry": 3000,
            "internal_frame": {"header_words_le": ["0010", "0005", "0009", "0010"],
                               "payload_bytes": 7, "request_marker_raw": 1,
                               "point_raw": "0098", "argument_raw": 0,
                               "filename_field_established": False},
            "limits": __doc__.strip()}


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
        path = args.output_dir / f"gen2-asset-transfer-start-{suffix}.json"
        path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
        path.chmod(0o600)
    print(json.dumps({"synthetic_cases": result["cases"], "station_commands_sent": 0,
                      "complete_settings_export": False}))


if __name__ == "__main__":
    main()
