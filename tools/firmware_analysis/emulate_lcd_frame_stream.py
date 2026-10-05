#!/usr/bin/env python3
"""Bounded LCD file callbacks, header probe and SPI transport contract replay.

Actual selected open/read/seek/close, filesystem dispatch, image-header probe,
address encoder and polling send/receive instructions execute. String helpers,
heap and storage reads are explicit host substitutes. The registry, runtime
index, clock, SPI status/data and register block are synthetic RAM fixtures.
No real MMIO, driver initialization, rendering, flash program/erase, reset,
boot or station runs. An accepted header or zero return is not applied pixels,
electrical behavior, physical persistence or a complete installation.
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
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                               UC_ARM_REG_R3, UC_ARM_REG_R11, UC_ARM_REG_LR,
                               UC_ARM_REG_SP, UC_ARM_REG_PRIMASK)

from emulate_lcd_asset_status import BASE, STACK, STOP, Machine as StatusMachine
from emulate_lcd_asset_transfer import (IMAGE_SHA256, CODE_SHA256, SPI_CONTEXT,
                                       SPI_CHANNEL, checked_image)
from emulate_lcd_resource_copy import frame, Machine as IndexMachine, READER, CATALOG

PATH, HANDLE, HEADER, NODE = 0x20018000, 0x20018200, 0x20018400, 0x20018600
INDEX, RUNTIME, DRIVER = 0x2001a000, 0x20019600, 0x2000071c
COUNT_OUT, BUFFER, REGISTERS = 0x20018800, 0x2001c000, 0x2001d000
HEAP, OWNER, USED = 0x20014750, 0x20019e00, 0x200148c4
RUNTIME_PTR, ENABLED = 0x20000710, 0x20000714
CLOCK, WAIT_FLAG = 0x20000628, 0x20000600
OPEN, CLOSE, READ, SEEK = 0x08029e6c, 0x08029e2c, 0x08029fcc, 0x0802a014
PROBE, ADDRESS, SEND, RECEIVE = 0x08017990, 0x080107f6, 0x080159f8, 0x080156dc
READ_WRAPPER = 0x080104e2
LIMIT = 10000
FRAME_ALLOW = ((OPEN, 0x08029fb0), (CLOSE, 0x08029e6a), (READ, 0x0802a044),
               (PROBE, 0x08017a32), (0x0801df34, 0x0801df6e),
               (0x0801df70, 0x0801e086), (0x0801e0a0, 0x0801e24a),
               (0x0801deaa, 0x0801df34), (0x0802bfd6, 0x0802c02c))
TRANSPORT_ALLOW = ((ADDRESS, 0x0801084e), (SEND, 0x08015bcc),
                   (RECEIVE, 0x080158bc), (READ_WRAPPER, 0x0801058e))


def within(address: int, size: int, regions: list[tuple[int, int]]) -> bool:
    return any(lo <= address < address + size <= hi for lo, hi in regions)


class BoundedMachine(StatusMachine):
    """Override inherited hooks completely; admit only this proof's slices."""

    def __init__(self, code: bytes) -> None:
        super().__init__(code)
        self.maximum = 0
        self.entries: list[dict] = []
        self.uc.hook_add(unicorn.UC_HOOK_MEM_READ, self.read_guard)

    def word(self, address: int) -> int:
        return int.from_bytes(self.uc.mem_read(address, 4), "little")

    def seed_word(self, address: int, value: int) -> None:
        self.uc.mem_write(address, struct.pack("<I", value))

    def read_guard(self, uc, access, address, size, value, data) -> None:
        assert within(address, size, [(BASE, BASE + 214016), (STACK - 0x1000, STACK)]), (
            f"Excluded read {address:08x}")

    def write_guard(self, uc, access, address, size, value, data) -> None:
        assert within(address, size, [(STACK - 0x1000, STACK)]), f"Excluded write {address:08x}"

    def stop(self, boundary: str) -> None:
        self.boundary, self.stopped = boundary, True
        self.uc.emu_stop()

    def run(self, entry: int, *arguments: int, fifth: int = 0,
            boundary: str = "returned") -> dict:
        assert len(arguments) <= 4
        before = self.instructions
        self.stopped, self.boundary = False, None
        self.active_entry = entry
        stack = STACK - 0x200
        self.seed_word(stack, fifth)
        for register, value in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3),
                                   (*arguments, *([0] * (4 - len(arguments))))):
            self.uc.reg_write(register, value)
        for register, value in ((UC_ARM_REG_SP, stack), (UC_ARM_REG_LR, STOP | 1),
                                (UC_ARM_REG_PRIMASK, 0), (UC_ARM_REG_R11, HEAP)):
            self.uc.reg_write(register, value)
        self.uc.emu_start(entry | 1, 0, count=LIMIT)
        assert self.stopped and self.boundary == boundary, "Instruction bound/boundary mismatch"
        assert self.uc.reg_read(UC_ARM_REG_PRIMASK) == 0
        assert self.uc.reg_read(UC_ARM_REG_SP) == stack or boundary != "returned"
        used = self.instructions - before
        self.maximum = max(self.maximum, used)
        result = {"entry_address": f"{entry:08x}", "instructions": used,
                  "return_r0_raw": self.uc.reg_read(UC_ARM_REG_R0), "boundary": self.boundary,
                  "interrupt_mask_preserved": True}
        self.entries.append(result)
        return result


class FrameMachine(BoundedMachine):
    def __init__(self, code: bytes, *, resource: int = 1, length: int = 18,
                 allocation_failure: bool = False) -> None:
        super().__init__(code)
        assert 0 <= resource < 14 and 0 <= length <= 0xffffffff
        self.resource, self.length = resource, length
        self.allocation_failure = allocation_failure
        self.live, self.allocations, self.frees, self.reads = False, 0, 0, []
        self.start = 0x97008
        # Deliberately seeded index: not an ingress or index-construction replay.
        self.seed_word(RUNTIME_PTR, RUNTIME)
        self.uc.mem_write(ENABLED, b"\1")
        self.uc.mem_write(RUNTIME, bytes(14 * 12))
        self.uc.mem_write(RUNTIME + resource * 12, struct.pack("<III", 2, 0x01020305, INDEX))
        self.uc.mem_write(INDEX, struct.pack("<4I", self.start, length, self.start + 30, 24))
        self.immutable_index = bytes(self.uc.mem_read(INDEX, 16))
        self.seed_word(HEAP + 0x170, OWNER)
        self.seed_word(USED, 0)
        self.seed_word(0x2000070c, SPI_CONTEXT)
        self.asset_base = self.start - 8
        self.asset = bytearray(struct.pack("<II", 2, 0x01020305) + frame() + frame(18, 3, 4))
        self.path = ""
        self.configure_registry()

    def configure_registry(self) -> None:
        driver = bytearray(52)
        driver[0] = ord("L")
        struct.pack_into("<7I", driver, 12, OPEN | 1, CLOSE | 1, READ | 1,
                         0x0802a041, SEEK | 1, 0x0802a03d, 0)
        self.uc.mem_write(DRIVER, bytes(driver))
        self.uc.mem_write(NODE, struct.pack("<II", DRIVER, 0))
        self.seed_word(HEAP + 0x188, 0)
        self.seed_word(HEAP + 0x18c, NODE)

    def read_guard(self, uc, access, address, size, value, data) -> None:
        regions = [(PATH, PATH + 64), (HANDLE - 4, HANDLE + 12), (HEADER, HEADER + 12),
                   (NODE, NODE + 8), (INDEX, INDEX + 16), (RUNTIME, RUNTIME + 168),
                   (DRIVER, DRIVER + 52), (COUNT_OUT, COUNT_OUT + 4), (BUFFER, BUFFER + 64),
                   (RUNTIME_PTR, ENABLED + 1), (HEAP + 0x170, HEAP + 0x178),
                   (HEAP + 0x188, HEAP + 0x190), (0x2000070c, 0x20000710)]
        if within(address, size, regions):
            return
        super().read_guard(uc, access, address, size, value, data)

    def write_guard(self, uc, access, address, size, value, data) -> None:
        if within(address, size, [(HANDLE, HANDLE + 12), (HEADER, HEADER + 12),
                                  (DRIVER, DRIVER + 52), (COUNT_OUT, COUNT_OUT + 4),
                                  (BUFFER, BUFFER + 64), (USED, USED + 4)]):
            return
        super().write_guard(uc, access, address, size, value, data)

    def cstring(self, pointer: int, limit: int = 64) -> bytes:
        result = bytearray()
        for offset in range(limit):
            self.read_guard(self.uc, 0, pointer + offset, 1, 0, None)
            byte = self.uc.mem_read(pointer + offset, 1)[0]
            if not byte:
                return bytes(result)
            result.append(byte)
        raise AssertionError("Unterminated synthetic string")

    def set_path(self, path: str) -> None:
        raw = path.encode("ascii")
        assert 0 < len(raw) < 64
        self.path = path
        self.uc.mem_write(PATH, raw + b"\0")

    def step(self, uc, address, size, data) -> None:
        self.instructions += 1
        r0, r1, r2, r3 = [uc.reg_read(r) for r in
                         (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)]
        if address == STOP:
            assert bytes(uc.mem_read(INDEX, 16)) == self.immutable_index
            assert self.word(USED) == (16 if self.live else 0)
            self.stop("returned")
        elif address == 0x08007154:
            self.stop("allocation_failure_handler_excluded")
        elif address == 0x0800914c:
            assert r0 == HEAP + 0x188
            self.stop("registry_insertion_excluded")
        elif address == 0x0802080c:
            assert r0 == 12 and not self.live
            self.allocations += 1
            if self.allocation_failure:
                self.back(0)
            else:
                self.live = True
                self.seed_word(HANDLE - 4, 16)
                self.seed_word(USED, 16)
                self.back(HANDLE)
        elif address == 0x0802606c:
            assert self.live and (r0, r1) == (OWNER, HANDLE)
            self.live = False
            self.frees += 1
            self.back()  # Actual caller decrements USED.
        elif address == 0x08006b06:
            assert 0 < r1 <= 88
            self.write_guard(uc, 0, r0, r1, 0, None)
            uc.mem_write(r0, bytes(r1))
            self.back(r0)
        elif address == 0x08006ab8:
            assert r2 == 12
            self.read_guard(uc, 0, r1, r2, 0, None)
            self.write_guard(uc, 0, r0, r2, 0, None)
            uc.mem_write(r0, bytes(uc.mem_read(r1, r2)))
            self.back(r0)
        elif address == 0x08031dfc:
            assert r1 == 32 and self.cstring(r2) == b"%s"
            raw = self.cstring(r3)
            assert len(raw) < r1  # Truncation/printf semantics intentionally excluded.
            self.write_guard(uc, 0, r0, len(raw) + 1, 0, None)
            uc.mem_write(r0, raw + b"\0")
            self.back(len(raw))
        elif address == 0x08006270:
            assert self.cstring(r1) == b"/"
            self.read_guard(uc, 0, r2, 4, 0, None)
            pointer = r0 or self.word(r2)
            assert pointer
            while self.cstring(pointer).startswith(b"/"):
                pointer += 1
            token = self.cstring(pointer)
            if not token:
                self.back(0)
            else:
                end = pointer + (token.find(b"/") if b"/" in token else len(token))
                next_pointer = end + int(b"/" in token)
                self.write_guard(uc, 0, end, 1, 0, None)
                uc.mem_write(end, b"\0")
                self.write_guard(uc, 0, r2, 4, 0, None)
                self.seed_word(r2, next_pointer)
                self.back(pointer)
        elif address == 0x080062b4:
            assert r1 == 0 and r2 == 10
            raw = self.cstring(r0).decode("ascii")
            digits = raw[:next((i for i, ch in enumerate(raw) if ch not in "0123456789"), len(raw))]
            assert digits and 0 <= int(digits) <= 0xffffffff
            self.back(int(digits))  # Only unsigned, bounded decimal fixtures.
        elif address == 0x08006218:
            self.back(len(self.cstring(r0)))
        elif address == 0x08006226:
            left, right = self.cstring(r0), self.cstring(r1)
            self.back(0 if left == right else 1)
        elif address in (0x0802cd4c, 0x0802cda0):
            self.back()  # Logs only.
        elif address == 0x080104e2:
            assert r0 == SPI_CONTEXT and 0 <= r3 <= 64
            offset = r1 - self.asset_base
            assert 0 <= offset <= offset + r3 <= len(self.asset), "Synthetic storage read extent"
            raw = bytes(self.asset[offset:offset + r3])
            if r3:
                self.write_guard(uc, 0, r2, r3, 0, None)
                uc.mem_write(r2, raw)
            self.reads.append({"source_raw": r1, "bytes": r3,
                               "data_sha256": hashlib.sha256(raw).hexdigest()})
            self.back(11)  # Deliberate substitute return; callback ignores it.
        elif not within(address, size, list(FRAME_ALLOW)):
            raise RuntimeError(f"Excluded frame instruction {address:08x}")

    def open(self, frame_index: int = 0, resource: int | None = None) -> dict:
        self.set_path(f"/{self.resource if resource is None else resource}/{frame_index}.bin")
        return self.run(OPEN, DRIVER, PATH, 2)

    def read(self, requested: int, *, handle: int = HANDLE, buffer: int = BUFFER) -> dict:
        self.seed_word(COUNT_OUT, 0xa5a5a5a5)
        result = self.run(READ, DRIVER, handle, buffer, requested, fifth=COUNT_OUT)
        return {**result, "reported_bytes_raw": self.word(COUNT_OUT),
                "cursor_after_raw": self.word(HANDLE), "storage_reads": list(self.reads)}


class TransportMachine(BoundedMachine):
    def __init__(self, code: bytes, *, width: int = 0, clocks: list[int] | None = None,
                 statuses: list[int] | None = None, received: list[int] | None = None) -> None:
        super().__init__(code)
        self.clocks, self.statuses, self.received = list(clocks or []), list(statuses or []), list(received or [])
        assert all(0 <= t <= 0xffffffffffffffff for t in self.clocks)
        assert all(0 <= s <= 3 for s in self.statuses)
        assert all(0 <= r <= 65535 for r in self.received)
        assert 0 <= width <= 255
        self.clock_reads = self.status_reads = self.data_reads = 0
        self.register_writes: list[int] = []
        self.control_writes: list[int] = []
        self.current_transfer: str | None = None
        self.address_calls: list[dict] = []
        self.address_returns: list[int] | None = None
        self.uc.mem_write(SPI_CHANNEL, bytes(32))
        self.uc.mem_write(SPI_CHANNEL + 4, bytes((width,)))
        self.uc.mem_write(SPI_CHANNEL + 6, b"\xff")  # Exclude real chip-select selection.
        self.seed_word(SPI_CHANNEL + 8, REGISTERS)
        self.seed_word(SPI_CONTEXT + 8, SPI_CHANNEL)
        self.uc.mem_write(BUFFER, bytes(range(64)))
        self.uc.mem_write(CLOCK, bytes(8))
        self.seed_word(REGISTERS + 0x80, 0xa0)

    def read_guard(self, uc, access, address, size, value, data) -> None:
        if address == CLOCK:
            assert size in (4, 8) and self.clocks, "Synthetic clock exhausted"
            uc.mem_write(CLOCK, struct.pack("<Q", self.clocks.pop(0)))
            self.clock_reads += 1
        elif address == REGISTERS + 8:
            assert size == 4 and self.statuses, "Synthetic status exhausted"
            self.seed_word(address, self.statuses.pop(0))
            self.status_reads += 1
        elif address == REGISTERS + 12:
            assert size == 4
            if self.current_transfer == "receive":
                assert self.received, "Synthetic receive data exhausted"
                self.seed_word(address, self.received.pop(0))
            self.data_reads += 1
        if within(address, size, [(CLOCK, CLOCK + 8), (SPI_CONTEXT, SPI_CONTEXT + 12),
                                  (SPI_CHANNEL, SPI_CHANNEL + 32), (BUFFER, BUFFER + 64),
                                  (REGISTERS + 8, REGISTERS + 16),
                                  (REGISTERS + 0x80, REGISTERS + 0x84)]):
            return
        super().read_guard(uc, access, address, size, value, data)

    def write_guard(self, uc, access, address, size, value, data) -> None:
        if address == REGISTERS + 12:
            assert size == 4
            self.register_writes.append(value & 0xffffffff)
            return
        if address == REGISTERS + 0x80:
            assert size == 4 and self.active_entry == READ_WRAPPER
            self.control_writes.append(value & 0xffffffff)
            return
        if within(address, size, [(SPI_CHANNEL, SPI_CHANNEL + 2), (WAIT_FLAG, WAIT_FLAG + 1),
                                  (BUFFER, BUFFER + 64)]):
            return
        super().write_guard(uc, access, address, size, value, data)

    def step(self, uc, address, size, data) -> None:
        self.instructions += 1
        if address == STOP:
            self.stop("returned")
        elif address == SEND and self.address_returns is not None:
            r0, r1, r2 = [uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2)]
            assert r0 == SPI_CHANNEL and r2 == 1 and self.address_returns
            self.read_guard(uc, 0, r1, 1, 0, None)
            returned = self.address_returns.pop(0)
            self.address_calls.append({"byte_raw": uc.mem_read(r1, 1)[0], "substitute_return_raw": returned})
            self.back(returned)
        elif address in (SEND, RECEIVE):
            self.current_transfer = "send" if address == SEND else "receive"
        elif not within(address, size, list(TRANSPORT_ALLOW)):
            raise RuntimeError(f"Excluded transport instruction {address:08x}")

    def finish(self, result: dict) -> dict:
        assert not self.clocks and not self.statuses and not self.received
        assert self.address_returns is None or not self.address_returns
        return {**result, "clock_reads": self.clock_reads, "status_reads": self.status_reads,
                "data_reads": self.data_reads, "synthetic_register_writes": self.register_writes,
                "synthetic_control_writes": self.control_writes,
                "channel_flags_after": list(self.uc.mem_read(SPI_CHANNEL, 2)),
                "address_send_substitutes": self.address_calls,
                "physical_spi_executed": False}


def suite(image: bytes) -> dict:
    code = checked_image(image)
    rows, maximum = [], 0

    def record(machine: BoundedMachine, name: str, result: dict, **metadata) -> None:
        nonlocal maximum
        maximum = max(maximum, machine.maximum)
        rows.append({"case": name, **metadata, **result})

    m = FrameMachine(code)
    result = m.run(0x0802bfd6, boundary="registry_insertion_excluded")
    raw_driver = bytes(m.uc.mem_read(DRIVER, 52))
    assert raw_driver[0] == ord("L") and m.word(DRIVER + 4) == 0
    assert list(struct.unpack_from("<6I", raw_driver, 12)) == [OPEN | 1, CLOSE | 1, READ | 1,
                                                            0x0802a041, SEEK | 1, 0x0802a03d]
    record(m, "actual_driver_registration_prefix", result,
           driver_bytes_sha256=hashlib.sha256(raw_driver).hexdigest(), registry_insertion_executed=False)
    indexed = IndexMachine(code)
    indexed.prepare(frame() + frame(18, 3, 4), count=2, standalone=True)
    indexed_result = indexed.result(indexed.run(READER, indexed.mutable_record, CATALOG + 12,
                                                boundary="returned"))
    m = FrameMachine(code)
    actual_index = bytes(indexed.uc.mem_read(INDEX, 16))
    assert actual_index == m.immutable_index
    assert bytes(indexed.asset[:len(m.asset)]) == bytes(m.asset)
    m.uc.mem_write(INDEX, actual_index)
    opened = m.open()
    m.read(12)
    result = m.read(18)
    assert result["reported_bytes_raw"] == 6 and opened["return_r0_raw"] == HANDLE
    maximum = max(maximum, indexed.maximum_entry_instructions)
    record(m, "actual_index_to_frame_stream", result, index_replay=indexed_result,
           separate_guests_with_exact_index_and_storage_transfer=True,
           available_pixel_bytes=6, supplied_pixel_bytes=18)
    for resource in range(14):
        m = FrameMachine(code, resource=resource)
        result = m.open(1)
        assert result["return_r0_raw"] == HANDLE and m.live
        assert list(struct.unpack("<3I", m.uc.mem_read(HANDLE, 12))) == [m.start + 30, m.start + 30, 24]
        record(m, "resource_frame_selection", result, resource_fixture=resource, frame_fixture=1,
               handle_words_raw=list(struct.unpack("<3I", m.uc.mem_read(HANDLE, 12))))
    for fixture in ("no_runtime", "no_index", "zero_count", "disabled", "resource_14", "frame_2", "frame_u32_max"):
        m = FrameMachine(code)
        if fixture == "no_runtime":
            m.seed_word(RUNTIME_PTR, 0)
        if fixture == "no_index":
            m.seed_word(RUNTIME + 20, 0)
        if fixture == "zero_count":
            m.seed_word(RUNTIME + 12, 0)
        if fixture == "disabled":
            m.uc.mem_write(ENABLED, b"\0")
        result = m.open(2 if fixture == "frame_2" else 0xffffffff if fixture == "frame_u32_max" else 0,
                        14 if fixture == "resource_14" else None)
        assert result["return_r0_raw"] == 0 and not m.live
        assert m.allocations == m.frees == int(fixture != "resource_14")
        record(m, "open_admission", result, fixture=fixture, allocations=m.allocations, frees=m.frees)
    m = FrameMachine(code, allocation_failure=True)
    m.set_path("/1/0.bin")
    result = m.run(OPEN, DRIVER, PATH, 2, boundary="allocation_failure_handler_excluded")
    assert not m.live and not m.reads
    record(m, "open_allocation_failure", result)
    for length, requested in ((18, 0), (18, 1), (18, 12), (18, 18), (18, 19), (18, 30),
                              (0, 1), (0x80000000, 1), (0xffffffff, 1)):
        m = FrameMachine(code, length=length)
        m.open()
        result = m.read(requested)
        expected = min(length, requested) if 0 < length <= 0x7fffffff else 0
        assert result["return_r0_raw"] == 0 and result["reported_bytes_raw"] == expected
        assert result["cursor_after_raw"] == m.start + expected
        assert sum(r["bytes"] for r in m.reads) == expected
        record(m, "read_extent", result, indexed_extent_fixture=length, requested_bytes=requested)
    m = FrameMachine(code)
    m.open()
    m.read(12)
    result = m.read(18)
    assert result["reported_bytes_raw"] == 6 and result["cursor_after_raw"] == m.start + 18
    eof = m.read(1)
    assert eof["reported_bytes_raw"] == 0 and eof["return_r0_raw"] == 0
    record(m, "header_consumes_indexed_extent", result, subsequent_eof=eof,
           available_pixel_bytes=6, claimed_indexed_pixel_extent=18, header_bytes=12)
    for origin, offset, expected in ((0, 2, 2), (1, 2, 2), (2, 2, 16), (0, 19, 19),
                                     (2, 20, -2), (0, 0xffffffff, -1)):
        m = FrameMachine(code)
        m.open()
        result = m.run(SEEK, DRIVER, HANDLE, offset, origin)
        assert result["return_r0_raw"] == 0 and m.word(HANDLE) == (m.start + expected) & 0xffffffff
        follow = m.read(1)
        available = min(1, max(0, 18 - expected))
        assert follow["reported_bytes_raw"] == available
        record(m, "seek_not_clamped", result, origin_fixture=origin, offset_fixture=offset,
               cursor_after_raw=(m.start + expected) & 0xffffffff, following_read=follow)
    m = FrameMachine(code)
    m.open()
    result = m.run(SEEK, DRIVER, HANDLE, 1, 3)
    assert result["return_r0_raw"] == 11 and m.word(HANDLE) == m.start
    record(m, "unsupported_seek_origin", result)
    for fixture in ("null_handle", "null_buffer"):
        m = FrameMachine(code)
        m.open()
        result = m.read(1, handle=0 if fixture == "null_handle" else HANDLE,
                        buffer=0 if fixture == "null_buffer" else BUFFER)
        assert result["return_r0_raw"] == 11 and result["reported_bytes_raw"] == 0xa5a5a5a5
        assert not m.reads
        record(m, "read_null_argument", result, fixture=fixture)
    for null in (False, True):
        m = FrameMachine(code)
        if not null:
            m.open()
        result = m.run(CLOSE, DRIVER, 0 if null else HANDLE)
        assert result["return_r0_raw"] == (11 if null else 0) and not m.live
        record(m, "close_accounting", result, null_handle_fixture=null, frees=m.frees)
    for length in (0, 1, 11, 12, 18):
        m = FrameMachine(code, length=length)
        m.set_path("L:/1/0.bin")
        result = m.run(PROBE, 0, PATH, HEADER)
        assert result["return_r0_raw"] == int(length >= 12)
        assert not m.live and m.allocations == m.frees == 1
        assert sum(r["bytes"] for r in m.reads) == min(length, 12)
        if length >= 12:
            expected_header = bytearray(frame()[:12])
            struct.pack_into("<H", expected_header, 2, 0x20)
            assert bytes(m.uc.mem_read(HEADER, 12)) == expected_header
        record(m, "actual_header_probe", result, indexed_extent_fixture=length,
               header_words_raw=list(struct.unpack("<3I", m.uc.mem_read(HEADER, 12))),
               storage_reads=m.reads, pixels_read=False, renderer_executed=False)
    m = FrameMachine(code)
    synthetic_header = struct.pack("<BB5H", 25, 15, 0x81, 2, 3, 7, 0x8765)
    m.asset[8:20] = synthetic_header
    m.set_path("L:/1/0.bin")
    result = m.run(PROBE, 0, PATH, HEADER)
    expected_header = bytearray(synthetic_header)
    struct.pack_into("<H", expected_header, 2, 0xa1)
    assert result["return_r0_raw"] == 1 and bytes(m.uc.mem_read(HEADER, 12)) == expected_header
    record(m, "header_fields_preserved_except_flag", result,
           synthetic_header_words_raw=list(struct.unpack("<3I", expected_header)), pixels_read=False)
    m = FrameMachine(code)
    m.asset[8] = 24  # Deliberate stale/corrupt index fixture; normal reader rejects this magic.
    m.set_path("L:/1/0.bin")
    result = m.run(PROBE, 0, PATH, HEADER)
    assert result["return_r0_raw"] == 1
    assert bytes(m.uc.mem_read(HEADER, 2)) == bytes((25, 24))
    assert int.from_bytes(m.uc.mem_read(HEADER + 2, 2), "little") == 0x20
    record(m, "header_probe_normalizes_stale_index_magic", result,
           ordinary_index_reader_would_reject=True, pixels_read=False, rendering_verified=False)
    for mode in (0, 1, 2, 255):
        for value in (0, 0x00c00000, 0x12345678, 0xffffffff):
            m = TransportMachine(code)
            m.uc.mem_write(SPI_CONTEXT, bytes((mode,)))
            sent = value.to_bytes(4, "big")[(1 if mode == 0 else 0):] if mode in (0, 1) else b""
            m.address_returns = [6, 11, 0, 11][:len(sent)]
            expected_return = m.address_returns[-1] if sent else mode
            result = m.finish(m.run(ADDRESS, SPI_CONTEXT, value))
            assert [call["byte_raw"] for call in m.address_calls] == list(sent)
            assert result["return_r0_raw"] == expected_return
            record(m, "actual_address_encoding", result, mode_fixture=mode, address_fixture=value)
    for operation, entry in (("send", SEND), ("receive", RECEIVE)):
        for width, length in ((0, 1), (0, 3), (1, 1), (1, 2), (1, 3), (1, 4), (2, 3)):
            transfers = length if width == 0 else length // 2 if width == 1 else 0
            supplied = [0x21 + i * 0x1111 for i in range(transfers)] if operation == "receive" else []
            m = TransportMachine(code, width=width, clocks=[0] * (transfers * 2),
                                 statuses=[3] * (transfers * 2), received=supplied)
            before = bytes(m.uc.mem_read(BUFFER, 64))
            result = m.finish(m.run(entry, SPI_CHANNEL, BUFFER, length))
            expected_writes = ([0xa5 if width == 0 else 0xa5a5] * transfers if operation == "receive"
                               else [i for i in range(length)] if width == 0 else
                               list(struct.unpack("<" + "H" * transfers, before[:transfers * 2])))
            assert result["return_r0_raw"] == 0 and result["synthetic_register_writes"] == expected_writes
            assert result["channel_flags_after"] == [0, 0]
            expected_buffer = bytearray(before)
            if operation == "receive":
                for index, value in enumerate(supplied):
                    if width == 0:
                        expected_buffer[index] = value & 255
                    else:
                        struct.pack_into("<H", expected_buffer, index * 2, value)
            assert bytes(m.uc.mem_read(BUFFER, 64)) == expected_buffer
            record(m, "actual_transport_ready", result, operation=operation, width_fixture=width,
                   requested_bytes=length, transferred_units=transfers,
                   output_buffer_sha256=hashlib.sha256(expected_buffer).hexdigest())
        for null_pointer, length in ((True, 1), (False, 0)):
            m = TransportMachine(code)
            result = m.finish(m.run(entry, SPI_CHANNEL, 0 if null_pointer else BUFFER, length))
            assert result["return_r0_raw"] == 6 and not m.register_writes
            assert result["channel_flags_after"] == [0, 0]
            record(m, "actual_transport_invalid_argument", result, operation=operation,
                   null_pointer_fixture=null_pointer, length_fixture=length)
        for width in (0, 1):
            for fixture, clocks, statuses, writes in (
                ("tx_timeout", [0, 101], [0], []),
                ("at_limit_still_polls", [0, 100, 101], [0, 0], []),
                ("rx_timeout_after_write", [0, 0, 101], [2, 2],
                 [(0 if width == 0 else 256) if operation == "send" else
                  (0xa5 if width == 0 else 0xa5a5)]),
            ):
                m = TransportMachine(code, width=width, clocks=clocks, statuses=statuses)
                result = m.finish(m.run(entry, SPI_CHANNEL, BUFFER, 1 if width == 0 else 2))
                assert result["return_r0_raw"] == 11 and result["synthetic_register_writes"] == writes
                assert result["channel_flags_after"] == ([0, 1] if operation == "send" else [1, 0])
                record(m, "actual_transport_timeout", result, operation=operation, fixture=fixture,
                       width_fixture=width, clock_fixture=clocks, status_fixture=statuses,
                       physical_time_units_verified=False)
        m = TransportMachine(code, clocks=[0, 0, 0, 101], statuses=[3, 3, 0],
                             received=[0x42] if operation == "receive" else [])
        result = m.finish(m.run(entry, SPI_CHANNEL, BUFFER, 3))
        assert result["return_r0_raw"] == 11 and result["data_reads"] == 1
        assert result["synthetic_register_writes"] == ([0] if operation == "send" else [0xa5])
        if operation == "receive":
            assert bytes(m.uc.mem_read(BUFFER, 3)) == b"\x42\x01\x02"
        record(m, "transport_partial_before_timeout", result, operation=operation,
               total_requested_bytes=3, complete_units_before_timeout=1)
    for fixture, length, clocks, statuses, received in (
        ("ready_one", 1, [0] * 18, [3] * 18, [0x42]),
        ("ready_three", 3, [0] * 22, [3] * 22, [0x42, 0x43, 0x44]),
        ("zero_length_receive_invalid", 0, [0] * 16, [3] * 16, []),
        ("receive_timeout", 1, [0] * 16 + [0, 101], [3] * 16 + [0], []),
        ("partial_receive_timeout", 3, [0] * 16 + [0, 0, 0, 101], [3] * 16 + [3, 3, 0], [0x42]),
    ):
        m = TransportMachine(code, clocks=clocks, statuses=statuses, received=received)
        result = m.finish(m.run(READ_WRAPPER, SPI_CONTEXT, 0x12345678, BUFFER, length))
        assert result["return_r0_raw"] == REGISTERS
        assert m.register_writes[:8] == [0x6b, 0x34, 0x56, 0x78, 0xa5, 0xa5, 0xa5, 0xa5]
        assert m.control_writes == [0xa1, 0xa3, 0xa2]
        expected = bytearray(range(64))
        expected[:len(received)] = bytes(received)
        assert bytes(m.uc.mem_read(BUFFER, 64)) == expected
        assert result["channel_flags_after"] == ([1, 0] if "timeout" in fixture else [0, 0])
        record(m, "actual_read_wrapper_return", result, fixture=fixture, requested_bytes=length,
               synthetic_spi_channel=255, received_complete_bytes=len(received),
               output_buffer_sha256=hashlib.sha256(expected).hexdigest())
    return {"model": "A1763", "lcd_manifest_version": "0.1.9.6", "lcd_sha256": IMAGE_SHA256,
            "application_sha256": CODE_SHA256, "cases": len(rows), "results": rows,
            "instruction_limit_per_entry": LIMIT, "maximum_entry_instructions": maximum,
            "negative_guards": negative_guards(code), "station_commands_sent": 0,
            "index_seeded": True, "library_strings_substituted": True,
            "real_mmio_executed": False, "rendering_verified": False,
            "physical_flash_verified": False, "limits": __doc__.strip()}


def negative_guards(code: bytes) -> list[str]:
    rejected = []
    for name, action in (
        ("boot_excluded", lambda m: m.step(m.uc, 0x08006144, 2, None)),
        ("reset_body_excluded", lambda m: m.step(m.uc, 0x08007156, 2, None)),
        ("renderer_excluded", lambda m: m.step(m.uc, 0x08017a34, 2, None)),
        ("flash_program_excluded", lambda m: m.step(m.uc, 0x0801058e, 2, None)),
        ("mmio_read_excluded", lambda m: m.read_guard(m.uc, 0, 0x40013008, 4, 0, None)),
        ("mmio_write_excluded", lambda m: m.write_guard(m.uc, 0, 0x4001300c, 4, 0, None)),
        ("runtime_mutation_excluded", lambda m: m.write_guard(m.uc, 0, RUNTIME, 4, 0, None)),
        ("index_mutation_excluded", lambda m: m.write_guard(m.uc, 0, INDEX, 4, 0, None)),
        ("unbounded_string_excluded", lambda m: m.cstring(PATH + 64)),
        ("storage_overread_excluded", lambda m: storage_overread(m)),
    ):
        try:
            action(FrameMachine(code))
        except (AssertionError, RuntimeError) as exc:
            if name == "storage_overread_excluded":
                assert str(exc) == "Synthetic storage read extent"
            rejected.append(name)
        else:
            raise AssertionError(f"Accepted {name}")
    for name, action in (
        ("transport_initialization_excluded", lambda m: m.step(m.uc, 0x08015624, 2, None)),
        ("transport_real_mmio_excluded", lambda m: m.read_guard(m.uc, 0, 0x40013008, 4, 0, None)),
        ("unsupplied_clock_excluded", lambda m: m.run(SEND, SPI_CHANNEL, BUFFER, 1)),
        ("unsupplied_status_excluded", lambda m: m.run(SEND, SPI_CHANNEL, BUFFER, 1)),
        ("unsupplied_receive_data_excluded", lambda m: m.run(RECEIVE, SPI_CHANNEL, BUFFER, 1)),
    ):
        try:
            action(TransportMachine(code, clocks=[0] if name == "unsupplied_status_excluded" else
                                    [0, 0] if name == "unsupplied_receive_data_excluded" else None,
                                    statuses=[3, 3] if name == "unsupplied_receive_data_excluded" else None))
        except (AssertionError, RuntimeError) as exc:
            if name.startswith("unsupplied_"):
                assert str(exc) == {"unsupplied_clock_excluded": "Synthetic clock exhausted",
                                    "unsupplied_status_excluded": "Synthetic status exhausted",
                                    "unsupplied_receive_data_excluded": "Synthetic receive data exhausted"}[name]
            rejected.append(name)
        else:
            raise AssertionError(f"Accepted {name}")
    return rejected


def storage_overread(machine: FrameMachine) -> None:
    machine.open()
    machine.run(SEEK, DRIVER, HANDLE, machine.start, 2)
    machine.read(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=Path(__file__).resolve().parents[2]
                        / "firmware/c1000_gen2/1.1.4.9/lcd-decoded.bin")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    result = suite(args.image.read_bytes())
    directory = Path(__file__).resolve().parent
    sources = sorted({Path(m.__file__).resolve() for m in tuple(sys.modules.values())
                      if getattr(m, "__file__", None) and Path(m.__file__).resolve().parent == directory})
    manifest = {"lcd_sha256": IMAGE_SHA256, "application_sha256": CODE_SHA256,
                "python": platform.python_version(), "unicorn": unicorn.__version__,
                "sources": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}}
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    for suffix, value in (("results", result), ("manifest", manifest)):
        p = args.output_dir / f"lcd-frame-stream-{suffix}.json"
        p.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
        p.chmod(0o600)
    print(json.dumps({"synthetic_cases": result["cases"], "negative_guards": len(result["negative_guards"]),
                      "maximum_entry_instructions": result["maximum_entry_instructions"],
                      "station_commands_sent": 0}))


if __name__ == "__main__":
    main()
