#!/usr/bin/env python3
"""Replay bounded HTTP buffer/callback blocks with synthetic Content-Length state.

Actual RISC-V task-registration descriptor, allocation decisions, read-loop branches, callback bridges,
asset chunk serialization and ACK callback run. HTTP setup, socket/TLS reads,
allocator/free, memset/memcpy, time, timers, transport and ACK delivery are
explicit substitutes. Entry
registers/header state are host-seeded; no worker startup, DNS/TLS, task engine,
controller, firmware boot or station runs. This is not an electrical test.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import struct

import unicorn
from unicorn import riscv_const as R

from emulate_radio_asset_download import Machine as AssetMachine, SHA256, STACK, validate_image

HTTP = 0x3fc874a0
BUFFER_POINTER = 0x3fc90330
CALLBACK = 0x42013ba8
FINISH = 0x42017544
MORE_ALLOW = ((0x4201763a, 0x42017cc0), (0x42013ba8, 0x42013c6c),
              (0x42013da8, 0x42013db2), (0x4203ba1a, 0x4203ba6a),
              (0x42016ef6, 0x42016f00), (0x4203c8fa, 0x4203c90a),
              (0x4203bcac, 0x4203bd0e))


class Machine(AssetMachine):
    def __init__(self, image: bytes, total: int, *, fallback: bool = False,
                 short_read: bool = False) -> None:
        super().__init__(image)
        assert 0 < total <= 8192
        self.content = bytes((i * 7 + i // 1024 + 11) & 255 for i in range(total))
        self.fallback, self.short_read = fallback, short_read
        self.buffer_capacity = self.buffer_address = self.position = 0
        self.socket_reads, self.callbacks, self.clears = [], [], []
        self.ack_callback_deliveries = 0
        self.registration_result = 0
        self.registration_descriptors = []
        self.worker_active = False

    def write_guard(self, uc, access, address, size, value, data) -> None:
        extra = ((0x3fc90314, 0x3fc90334), (0x3fc90594, 0x3fc90595), (0x3fc829b0, 0x3fc829b4))
        if any(lo <= address < address + size <= hi for lo, hi in extra):
            return
        super().write_guard(uc, access, address, size, value, data)

    def step(self, uc, address, size, data) -> None:
        args = [uc.reg_read(R.UC_RISCV_REG_A0 + i) for i in range(8)]
        a0, a1, a2 = args[:3]
        if address == FINISH:
            self.visited.add(address)
            self.stopped = True
            uc.emu_stop()
            return
        if address == 0x42013a40:
            assert (a0, a1) == (20, 2)
            descriptor = struct.unpack("<4I", self.read(a2, 16))
            assert descriptor == (2, 0x4203b98a, 0x4203ba6a, 0x4203ba1a)
            self.registration_descriptors.append({"task_id_raw": a0, "argument1_raw": a1,
                                                 "descriptor_word0_raw": descriptor[0],
                                                 "locator_callback": hex(descriptor[1]),
                                                 "completion_bridge": hex(descriptor[2]),
                                                 "progress_bridge": hex(descriptor[3])})
            result = self.registration_result
        elif (self.worker_active and address == 0x42017e00
                and uc.reg_read(R.UC_RISCV_REG_RA) == 0x42017664):
            assert a0 in (1024, 2048)
            self.allocations.append(a0)
            if self.fallback and a0 == 2048:
                result = 0
            else:
                result = self.alloc(b"\xa5" * a0)
                self.buffer_capacity, self.buffer_address = a0, result
        elif address == 0x40000354:
            assert a0 == self.buffer_address and a1 == 0 and a2 == self.buffer_capacity
            uc.mem_write(a0, bytes(a2))
            self.clears.append({"bytes": a2, "position": self.position})
            result = a0
        elif address == 0x420398ba:
            assert a0 == HTTP
            result = len(self.content) - self.position
        elif address == 0x420394f2:
            assert a0 == HTTP and a1 == self.buffer_address
            assert 0 < a2 <= self.buffer_capacity and args[3] == 10000
            returned = a2 - 1 if self.short_read else a2
            assert self.position + returned <= len(self.content)
            uc.mem_write(a1, self.content[self.position:self.position + returned])
            self.socket_reads.append({"requested": a2, "returned": returned, "position": self.position})
            self.position += returned
            self.input_region = a1, returned
            result = returned
        elif address in (0x42013db2, 0x42013dc8):
            assert a0 == (60 if address == 0x42013db2 else len(self.content) - self.position)
            result = 0  # Timer-service substitutions; no timer executes.
        elif address == 0x4202df9c:
            assert a0 == 3
            self.delays.append(a0)
            self.ack_callback_deliveries += 1
            # Synthetic delivery, actual five-instruction ACK callback. RA
            # already points to the progress loop after the substituted delay.
            uc.reg_write(R.UC_RISCV_REG_A1, 0)
            uc.reg_write(R.UC_RISCV_REG_PC, 0x4203c8fa)
            return
        else:
            if address == CALLBACK:
                assert a0 == self.buffer_address
                assert 0 <= a1 <= self.buffer_capacity
                tail = self.read(a0 + a1, self.buffer_capacity - a1)
                self.callbacks.append({"length": a1, "remaining": a2, "kind_raw": args[3],
                                       "padding_bytes": len(tail),
                                       "padding_sha256": hashlib.sha256(tail).hexdigest(),
                                       "padding_all_zero": not any(tail)})
            if address == 0x40000358 and self.buffer_address <= a1 < self.buffer_address + self.buffer_capacity:
                assert a1 + a2 <= self.buffer_address + self.buffer_capacity, "Copy exceeds HTTP allocation"
            if any(lo <= address < hi for lo, hi in MORE_ALLOW):
                self.visited.add(address)
                return
            super().step(uc, address, size, data)
            return
        self.visited.add(address)
        uc.reg_write(R.UC_RISCV_REG_A0, result & 0xffffffff)
        uc.reg_write(R.UC_RISCV_REG_PC, uc.reg_read(R.UC_RISCV_REG_RA))

    def exercise(self) -> dict:
        self.request()
        self.worker_active = True
        # Internal entry after synthetic successful response/header decisions.
        # The real worker's earlier network/header code is excluded.
        registers = {R.UC_RISCV_REG_S0: HTTP, R.UC_RISCV_REG_S3: 0x3c137000,
                     R.UC_RISCV_REG_S4: self.word(0x3fc90598),
                     R.UC_RISCV_REG_S5: 0x3fc87000, R.UC_RISCV_REG_S6: CALLBACK}
        for reg, value in registers.items():
            self.uc.reg_write(reg, value)
        self.uc.mem_write(HTTP + 0x7e8, struct.pack("<II", len(self.content), 0))
        self.uc.mem_write(0x3fc9031c, struct.pack("<I", len(self.content)))
        self.uc.mem_write(0x3fc902dc, struct.pack("<II", 0x4203ba1a, 1))
        self.uc.reg_write(R.UC_RISCV_REG_SP, STACK - 0x400)
        self.uc.mem_write(STACK - 0x400, bytes(0x110))
        self.run(0x4201763a)
        result = self.summary()
        assert self.clears == [{"bytes": self.buffer_capacity, "position": 0}]
        assert self.buffer_capacity == (1024 if self.fallback else 2048)
        if self.short_read:
            assert not self.callbacks and not result["chunk_frames"]
            assert len(self.socket_reads) == 1
        else:
            assert self.position == len(self.content)
            assert [r["length"] for r in self.callbacks] == [r["returned"] for r in self.socket_reads]
            assert result["stored_forward_offset"] == ((len(self.content) + 1023) // 1024) * 1024
            assert len(result["chunk_frames"]) == (len(self.content) + 1023) // 1024
            assert all(r["kind_raw"] == 0 for r in self.callbacks)
            buffer = bytearray(self.buffer_capacity)
            frames = iter(result["chunk_frames"])
            offset = 0
            for read in self.socket_reads:
                count, position = read["returned"], read["position"]
                buffer[:count] = self.content[position:position + count]
                for start in range(0, count, 1024):
                    expected = (b"\xa1\4" + struct.pack("<I", offset) + b"\xa2\4"
                                + struct.pack("<I", len(self.content)) + b"\xa3\0\4"
                                + buffer[start:start + 1024])
                    frame = next(frames)
                    assert frame["body_sha256"] == hashlib.sha256(expected).hexdigest()
                    assert frame["total_u32_le"] == len(self.content)
                    offset += 1024
            assert next(frames, None) is None
        return {"synthetic_content_length": len(self.content), "allocation_fallback": self.fallback,
                "short_read": self.short_read, "allocated_buffer_bytes": self.buffer_capacity,
                "socket_read_substitutes": self.socket_reads, "callback_arguments": self.callbacks,
                "buffer_clear_substitutes": self.clears, "input_copies_within_http_allocation": True,
                "synthetic_success_ack_callback_deliveries": self.ack_callback_deliveries,
                "stopped_before_http_cleanup": True, **result}


def suite(image: bytes) -> dict:
    validate_image(image)
    rows = [Machine(image, size, fallback=fallback).exercise()
            for fallback in (False, True) for size in (1, 1024, 1025, 2048, 2049, 4097)]
    rows += [Machine(image, size, short_read=True).exercise() for size in (1024, 2049)]
    for row in rows:
        if not row["short_read"]:
            last = row["callback_arguments"][-1]
            assert last["padding_all_zero"] == (row["synthetic_content_length"] <= row["allocated_buffer_bytes"]
                                                or last["padding_bytes"] == 0)
        assert row["synthetic_success_ack_callback_deliveries"] == len(row["chunk_frames"])
    ack_rows = []
    for argument in (0, 1, 0xffffffff):
        m = Machine(image, 1)
        m.run_entry(0x4203c8fa, 0, argument)
        flag = m.read(0x3fc90604, 1)[0]
        assert flag == (1 if argument == 0 else 2)
        ack_rows.append({"callback_argument1_raw": argument, "stored_ack_flag": flag})
    registration_rows = []
    for status in (0, 5):
        m = Machine(image, 1)
        m.registration_result = status
        returned = m.run_entry(0x4203bcac)
        assert returned == status and len(m.registration_descriptors) == 1
        registration_rows.append({"substituted_registration_result": status, "returned": returned,
                                  "descriptors": m.registration_descriptors})
    negatives = negative_guards(image)
    return {"model": "A1763", "radio_version": "0.3.3.0", "radio_sha256": SHA256,
            "cases": len(rows) + len(ack_rows) + len(registration_rows), "buffer_cases": len(rows),
            "results": rows, "ack_callback_cases": ack_rows, "instruction_limit_per_entry": 10000,
            "task_registration_cases": registration_rows,
            "negative_guards": negatives,
            "http_setup_executed": False, "physical_transport_verified": False,
            "station_commands_sent": 0, "limits": __doc__.strip()}


def negative_guards(image: bytes) -> list[str]:
    rejected = []
    for size in (0, 8193):
        try:
            Machine(image, size)
        except AssertionError:
            rejected.append(f"content_length_{size}_outside_fixture_bound")
        else:
            raise AssertionError("Accepted content length outside fixture bound")
    m = Machine(image, 1)
    try:
        m.run_entry(0x42016ff0)
    except AssertionError:
        rejected.append("http_setup_entry_excluded")
    else:
        raise AssertionError("Accepted excluded HTTP setup")
    m = Machine(image, 1)
    m.exercise()
    for index, value in enumerate((0x20010000, m.buffer_address + m.buffer_capacity - 10, 1024)):
        m.uc.reg_write(R.UC_RISCV_REG_A0 + index, value)
    try:
        m.step(m.uc, 0x40000358, 4, None)
    except AssertionError:
        rejected.append("copy_outside_http_allocation")
    else:
        raise AssertionError("Accepted copy outside HTTP allocation")
    return rejected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if not __debug__:
        raise RuntimeError("Assertions are required")
    os.umask(0o077)
    root = Path(__file__).resolve().parents[2]
    image = (root / "firmware/c1000_gen2/1.1.4.9/c1000-radio-validated.bin").read_bytes()
    result = suite(image)
    package = Path(__file__).resolve().parent
    names = (Path(__file__).name, "emulate_radio_asset_download.py", "emulate_radio_update_status.py")
    manifest = {"radio_sha256": SHA256, "python": platform.python_version(), "unicorn": unicorn.__version__,
                "sources": {n: hashlib.sha256((package / n).read_bytes()).hexdigest() for n in names}}
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    for suffix, value in (("results", result), ("manifest", manifest)):
        p = args.output_dir / f"radio-asset-buffer-{suffix}.json"
        p.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
        p.chmod(0o600)
    print(json.dumps({"synthetic_cases": result["cases"],
                      "negative_guards": len(result["negative_guards"]), "station_commands_sent": 0}))


if __name__ == "__main__":
    main()
