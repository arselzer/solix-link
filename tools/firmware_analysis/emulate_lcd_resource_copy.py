#!/usr/bin/env python3
"""Bounded LCD ordinary-resource copy, index rebuild and failure replay.

Actual selected consumer, copy-loop, ownership accounting, frame-index reader,
format-size helper, final state and isolated busy-wait instructions execute.
Heap, logs, memory
clearing, SPI erase/read/write and parameter erase/program are explicit host
substitutes. Writes affect bounded synthetic byte arrays only; code/parameter
pages remain read-only. Stop before fatal handlers, reset, boot or real drivers.
Ingress/CRC, flash persistence, rendered images, electrical behavior and recovery
are not established. Nonzero substitute returns have no asserted driver meaning.
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
                               UC_ARM_REG_R3, UC_ARM_REG_LR, UC_ARM_REG_SP,
                               UC_ARM_REG_PRIMASK)

from emulate_lcd_pending_worker import (
    Machine as PendingMachine, CONSUMER, RUNTIME, BITMAP, event_address,
    VERSION_PAGE, CATALOG, CATALOG_BYTES, MARKER_FLASH,
)
from emulate_lcd_asset_status import BASE, STACK
from emulate_lcd_asset_transfer import (
    IMAGE_SHA256, CODE_SHA256, RESOURCE_HEADER, SPI_CONTEXT, STAGING_BASE,
    SPI_CHANNEL, checked_image, crc16,
)

BUFFER, INDEX, OLD_INDEX = 0x2001c000, 0x2001a000, 0x2001b000
OWNER, USED = 0x20019e00, 0x200148c4
LIMIT = 100000
READER = 0x08029b60
FORMAT = 0x08017fc4
FORMAT_ALLOW = ((FORMAT, 0x08017fd8), (0x08017fe8, 0x08018004))
BITS = {6: 8, 7: 1, 8: 2, 9: 4, 10: 8, 11: 1, 12: 2, 13: 4,
        14: 8, 15: 24, 16: 32, 17: 32, 18: 16, 19: 24, 20: 16}
EXTRA = {7, 8, 9, 10, 11, 12, 13, 14, 16, 20}
HEAP_REGIONS = ((BUFFER - 4, BUFFER + 4096), (INDEX - 4, INDEX + 2400),
                (OLD_INDEX - 4, OLD_INDEX + 16))
WAIT, CLOCK, WAIT_FLAG = 0x08010718, 0x20000628, 0x20000600


def extent(fmt: int, width: int, height: int) -> int:
    return (((BITS.get(fmt, 0) + 7) // 8 + int(fmt in EXTRA)) * width * height) & 0xffffffff


def frame(fmt: int = 15, width: int = 2, height: int = 3, *, magic: int = 25,
          include_pixels: bool = True) -> bytes:
    assert 0 <= fmt <= 255 and 0 <= width <= 65535 and 0 <= height <= 65535
    length = extent(fmt, width, height) if include_pixels else 0
    assert length <= 8192, "Synthetic pixel fixture too large"
    # Selected fields only; this is not a complete valid vendor image format.
    header = struct.pack("<BBHHHI", magic, fmt, 0, width, height, 0)
    return header + bytes((i * 37 + 11) & 255 for i in range(length))


class Machine(PendingMachine):
    def __init__(self, code: bytes, *, fail_allocation: str | None = None,
                 skip_program: bool = False, service_return: int = 0) -> None:
        assert fail_allocation in (None, "header", "buffer", "index")
        assert 0 <= service_return <= 0xffffffff
        self.fail_allocation = fail_allocation
        self.skip_program = skip_program
        self.service_return = service_return
        self.allocations, self.live, self.frees = [], {}, []
        self.erase_requests, self.write_requests, self.parameter_services = [], [], []
        self.asset_base, self.asset = 0, bytearray()
        self.mutable_record = None
        self.format_visits = []
        super().__init__(code)
        fixture_regions = [(RESOURCE_HEADER - 4, RESOURCE_HEADER + 1024),
                           (RUNTIME, RUNTIME + 168), *HEAP_REGIONS]
        for index, (lo, hi) in enumerate(fixture_regions):
            assert all(hi <= other_lo or other_hi <= lo
                       for other_lo, other_hi in fixture_regions[index + 1:])

    def read_guard(self, uc, access, address, size, value, data) -> None:
        if any(lo <= address < address + size <= hi for lo, hi in HEAP_REGIONS):
            return
        super().read_guard(uc, access, address, size, value, data)

    def write_guard(self, uc, access, address, size, value, data) -> None:
        regions = [(BUFFER, BUFFER + 4096), (INDEX, INDEX + 2400),
                   (0x20000228, 0x2000022c), (0x20000234, 0x20000238)]
        if self.mutable_record is not None:
            regions.append((self.mutable_record, self.mutable_record + 12))
        if any(lo <= address < address + size <= hi for lo, hi in regions):
            return
        super().write_guard(uc, access, address, size, value, data)

    def read_storage(self, address: int, size: int) -> bytes:
        if STAGING_BASE <= address:
            offset = address - STAGING_BASE
            assert 0 <= offset < offset + size <= len(self.storage), "Synthetic staging read extent"
            return bytes(self.storage[offset:offset + size])
        offset = address - self.asset_base
        assert 0 <= offset < offset + size <= len(self.asset), "Synthetic asset read extent"
        return bytes(self.asset[offset:offset + size])

    def step(self, uc, address, size, data) -> None:
        r0, r1, r2, r3 = [uc.reg_read(r) for r in (
            UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)]
        if address == 0x0802080c:
            self.instructions += 1
            caller = uc.reg_read(UC_ARM_REG_LR) & ~1
            role = {0x0802a4ee: "header", 0x0802a7a0: "buffer", 0x08029bcc: "index"}[caller]
            pointer = {"header": RESOURCE_HEADER, "buffer": BUFFER, "index": INDEX}[role]
            assert (role == "header" and r0 == 1024 or role == "buffer" and r0 == 4096 or
                    role == "index" and 8 <= r0 <= 2400 and r0 % 8 == 0)
            self.allocations.append({"role": role, "bytes": r0, "failed": role == self.fail_allocation})
            if role == self.fail_allocation:
                self.back(0)
            else:
                assert pointer not in self.live
                self.live[pointer] = (role, r0 + 4)
                self.seed_word(pointer - 4, r0 + 4)
                self.seed_word(USED, self.word(USED) + r0 + 4)
                self.header_live = RESOURCE_HEADER in self.live
                self.back(pointer)
        elif address == 0x0802606c:
            self.instructions += 1
            assert r0 == OWNER and r1 in self.live
            role, allocated = self.live.pop(r1)
            assert self.word(r1 - 4) & ~3 == allocated
            self.frees.append({"role": role, "bytes_from_synthetic_header": allocated})
            self.header_live = RESOURCE_HEADER in self.live
            self.back()  # Actual caller decrements the seeded used count.
        elif address == 0x08006b06:
            self.instructions += 1
            assert 0 < r1 <= 4096
            self.write_guard(uc, 0, r0, r1, 0, None)
            uc.mem_write(r0, bytes(r1))
            self.back(r0)
        elif address == 0x080102bc:
            self.instructions += 1
            assert r0 == SPI_CONTEXT and (r1 - self.asset_base) % 4096 == 0
            offset = r1 - self.asset_base
            assert 0 <= offset < offset + 4096 <= len(self.asset)
            self.asset[offset:offset + 4096] = b"\xff" * 4096
            self.erase_requests.append({"address_raw": r1, "bytes": 4096})
            self.back(self.service_return)
        elif address == 0x08010654:
            self.instructions += 1
            assert r0 == SPI_CONTEXT and 0 < r3 <= 4096
            offset = r1 - self.asset_base
            assert 0 <= offset < offset + r3 <= len(self.asset)
            self.read_guard(uc, 0, r2, r3, 0, None)
            value = bytes(uc.mem_read(r2, r3))
            if not self.skip_program:
                assert all((old & new) == new for old, new in zip(self.asset[offset:offset + r3], value))
                self.asset[offset:offset + r3] = value
            self.write_requests.append({"address_raw": r1, "bytes": r3,
                                        "data_sha256": hashlib.sha256(value).hexdigest(),
                                        "applied_to_host_array": not self.skip_program})
            self.back(self.service_return)
        elif address == 0x080104e2:
            self.instructions += 1
            assert r0 == SPI_CONTEXT and 0 < r3 <= 4096
            self.write_guard(uc, 0, r2, r3, 0, None)
            value = self.read_storage(r1, r3)
            uc.mem_write(r2, value)
            self.spi_reads.append({"source_raw": r1, "bytes": r3,
                                   "data_sha256": hashlib.sha256(value).hexdigest()})
            self.back(self.service_return)
        elif address in (0x08013f84, 0x08014140):
            self.instructions += 1
            assert (r0, r1) == (MARKER_FLASH, 20)
            operation = "erase" if address == 0x08013f84 else "program"
            event = {"operation": operation, "address_raw": r0, "bytes": r1}
            if operation == "program":
                self.read_guard(uc, 0, r2, r1, 0, None)
                candidate = bytes(uc.mem_read(r2, r1))
                assert candidate[:4] == bytes(4)
                event.update({"candidate_first_word_raw": 0,
                              "synthetic_candidate_sha256": hashlib.sha256(candidate).hexdigest()})
            self.parameter_services.append(event)
            self.back(self.service_return)  # Never mutate the emulated parameter page.
        elif address == READER:
            self.instructions += 1
            assert r0 == self.mutable_record
            assert any(r1 == CATALOG + index * 12 for index in range(14))
        elif 0x08029b60 < address < 0x08029d4a:
            self.instructions += 1
        elif any(lo <= address < hi for lo, hi in FORMAT_ALLOW):
            self.instructions += 1
            if address == FORMAT:
                assert 0 <= r0 <= 255
                self.format_visits.append(r0)
        else:
            super().step(uc, address, size, data)

    def run(self, entry: int, r0: int = 0, r1: int = 0, *, boundary: str | None = None) -> dict:
        from emulate_lcd_asset_status import STOP
        self.stopped, self.boundary, self.boundary_arguments = False, None, None
        before = self.instructions
        for register, value in ((UC_ARM_REG_R0, r0), (UC_ARM_REG_R1, r1),
                                (UC_ARM_REG_SP, STACK - 0x200), (UC_ARM_REG_LR, STOP | 1),
                                (UC_ARM_REG_PRIMASK, 0)):
            self.uc.reg_write(register, value)
        self.uc.emu_start(entry | 1, 0, count=LIMIT)
        assert self.stopped, "LCD copy instruction bound exceeded"
        assert self.uc.reg_read(UC_ARM_REG_PRIMASK) == 0
        assert bytes(self.uc.mem_read(MARKER_FLASH, 20)) == self.parameter_seed
        assert self.word(VERSION_PAGE) == 0x01020304
        if not self.layout_enabled:
            assert bytes(self.uc.mem_read(CATALOG, CATALOG_BYTES)) == self.catalog_before
        if boundary is not None:
            assert self.boundary == boundary, (self.boundary, boundary)
        used = self.instructions - before
        self.maximum_entry_instructions = max(self.maximum_entry_instructions, used)
        self.entry_instructions.append(used)
        return {"entry_address": f"{entry:08x}", "instructions": used,
                "stopped_boundary": self.boundary, "return_r0_raw": self.uc.reg_read(UC_ARM_REG_R0),
                "parameter_page_preserved": True, "catalog_preserved": not self.layout_enabled,
                "interrupt_mask_restored": True}

    def prepare(self, payload: bytes, *, index: int = 1, count: int = 1,
                old_index: bool = False, standalone: bool = False) -> None:
        assert 0 <= index < 14 and 0 <= count <= 0xffffffff
        self.mutable_record = RUNTIME + index * 12
        _, self.asset_base, capacity = struct.unpack("<3I", self.uc.mem_read(CATALOG + index * 12, 12))
        assert capacity % 4096 == 0
        self.asset = bytearray(capacity)  # Erase must replace the supplied old bytes.
        self.uc.mem_write(RUNTIME, bytes(168))
        self.uc.mem_write(self.mutable_record, struct.pack("<3I", 1, 0x01020304, OLD_INDEX if old_index else 0))
        if old_index:
            self.live[OLD_INDEX] = ("old_index", 20)
            self.seed_word(OLD_INDEX - 4, 20)
            self.uc.mem_write(OLD_INDEX, bytes(16))
            self.seed_word(USED, 20)
        else:
            self.seed_word(USED, 0)
        if standalone:
            assert len(payload) + 8 <= capacity
            self.asset[:8 + len(payload)] = struct.pack("<II", count, 0x01020305) + payload
        else:
            data = struct.pack("<I", count) + payload
            assert len(data) + 4 <= capacity
            name_pointer = self.word(CATALOG + index * 12)
            name = self.cstring(name_pointer)
            header = bytearray(b"\xff" * 1024)
            struct.pack_into("<I", header, 8, 0x05030201)  # Consumer byte-reverses it.
            struct.pack_into("<4I16s", header, 12, 1024, len(data), crc16(data), 0x05030201, name)
            self.storage = bytes(header) + data
            self.seed_parameter((0xa5a5a5a5, 0x11223344, 0x01000906, 0x19011d01, 0x01020304))

    def result(self, reply: dict) -> dict:
        count, version, pointer = struct.unpack("<3I", self.uc.mem_read(self.mutable_record, 12))
        assert pointer in (0, OLD_INDEX, INDEX)
        entries = []
        if pointer == INDEX:
            assert count <= 300
            entries = [{"source_raw": source, "extent_raw": size}
                       for source, size in struct.iter_unpack("<II", self.uc.mem_read(INDEX, count * 8))]
        assert self.word(USED) == sum(length for _, length in self.live.values())
        return {**reply, "runtime_count_raw": count, "runtime_version_raw": version,
                "index_pointer_present": pointer != 0, "index_entries": entries,
                "allocations": self.allocations, "frees": self.frees,
                "live_allocation_roles": sorted(role for role, _ in self.live.values()),
                "synthetic_heap_used_after": self.word(USED), "erase_requests": self.erase_requests,
                "write_requests": self.write_requests, "spi_read_substitutes": self.spi_reads,
                "parameter_service_substitutes": self.parameter_services,
                "post_update_timer_substitutes": [call for call in self.calls
                                                  if call["operation"] == "post_update_timer_substitute"],
                "format_helper_inputs": self.format_visits,
                "consumer_flag_mode_after": list(self.uc.mem_read(0x200000f8, 2)),
                "resource_application_verified": False, "rendering_verified": False}


class WaitMachine(PendingMachine):
    """Actual status-wait logic, with clock/status reads and channel 255 supplied."""

    def __init__(self, code: bytes, times: list[int], statuses: list[int]) -> None:
        assert times and all(0 <= value <= 0xffffffffffffffff for value in times)
        assert all(value in (0, 1) for value in statuses)
        self.times, self.statuses = list(times), list(statuses)
        self.clock_reads, self.status_reads, self.command_calls = 0, 0, 0
        super().__init__(code)
        self.seed_word(SPI_CONTEXT + 8, SPI_CHANNEL)
        self.uc.mem_write(SPI_CHANNEL + 6, b"\xff")  # Skip real channel/MMIO selection.
        self.uc.mem_write(CLOCK, bytes(8))

    def read_guard(self, uc, access, address, size, value, data) -> None:
        if address == CLOCK:
            assert self.times and size in (4, 8)
            self.uc.mem_write(CLOCK, struct.pack("<Q", self.times.pop(0)))
            self.clock_reads += 1
        if any(lo <= address < address + size <= hi for lo, hi in (
                (CLOCK, CLOCK + 8), (SPI_CONTEXT + 8, SPI_CONTEXT + 12),
                (SPI_CHANNEL + 6, SPI_CHANNEL + 7))):
            return
        super().read_guard(uc, access, address, size, value, data)

    def write_guard(self, uc, access, address, size, value, data) -> None:
        if address == WAIT_FLAG and size == 1:
            return
        super().write_guard(uc, access, address, size, value, data)

    def step(self, uc, address, size, data) -> None:
        r0, r1, r2 = [uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2)]
        if address == 0x080159f8:
            self.instructions += 1
            assert r0 == SPI_CHANNEL and r2 == 1
            self.read_guard(uc, 0, r1, 1, 0, None)
            assert bytes(uc.mem_read(r1, 1)) == b"\5"
            self.command_calls += 1
            self.back(1)  # Command send substitute, no hardware operation.
        elif address == 0x080156dc:
            self.instructions += 1
            assert r0 == SPI_CHANNEL and r2 == 1 and self.statuses
            self.write_guard(uc, 0, r1, 1, 0, None)
            uc.mem_write(r1, bytes((self.statuses.pop(0),)))
            self.status_reads += 1
            self.back(1)
        elif WAIT <= address < 0x080107f6:
            self.instructions += 1
        else:
            super().step(uc, address, size, data)


def suite(image: bytes) -> dict:
    code = checked_image(image)
    rows = []
    for fmt in (*range(22), 255):
        m = Machine(code)
        result = m.run(FORMAT, fmt, boundary="returned")
        assert result["return_r0_raw"] == BITS.get(fmt, 0)
        rows.append({"case": "format_size_helper", "format_fixture": fmt, **result})
    for fmt in range(6, 21):
        m = Machine(code)
        payload = frame(fmt)
        m.prepare(payload, standalone=True)
        result = m.result(m.run(READER, m.mutable_record, CATALOG + 12, boundary="returned"))
        assert result["runtime_count_raw"] == 1 and result["runtime_version_raw"] == 0x01020305
        assert result["index_entries"] == [{"source_raw": m.asset_base + 8, "extent_raw": extent(fmt, 2, 3)}]
        rows.append({"case": "index_format_extent", "format_fixture": fmt, "width": 2, "height": 3, **result})
    for name, payload, count, expected_count, sizes in (
        ("two_frames", frame(15, 2, 3) + frame(18, 3, 4), 2, 2, [18, 24]),
        ("bad_first_magic", frame(magic=24), 1, 0, []),
        ("bad_second_magic", frame() + frame(magic=24), 2, 1, [18]),
        ("zero_width", frame(width=0), 1, 1, [0]),
        ("zero_height", frame(height=0), 1, 1, [0]),
        ("unsupported_format_zero", frame(fmt=0), 1, 1, [0]),
        ("unsupported_format_255", frame(fmt=255), 1, 1, [0]),
        ("wrapped_extent", frame(16, 65535, 65535, include_pixels=False), 1, 1,
         [extent(16, 65535, 65535)]),
        ("maximum_count_zero_extent", frame(width=0) * 300, 300, 300, [0] * 300),
        ("zero_count", b"", 0, 0, []),
        ("sentinel_count", b"", 0xffffffff, 0, []),
        ("count_above_limit", b"", 301, 0, []),
    ):
        m = Machine(code)
        m.prepare(payload, count=count, standalone=True)
        result = m.result(m.run(READER, m.mutable_record, CATALOG + 12, boundary="returned"))
        assert result["runtime_count_raw"] == expected_count
        assert [e["extent_raw"] for e in result["index_entries"]] == sizes
        cursor = m.asset_base + 8
        for entry in result["index_entries"]:
            assert entry["source_raw"] == cursor
            cursor = (cursor + 12 + entry["extent_raw"]) & 0xffffffff
        rows.append({"case": "index_admission", "fixture": name, "seeded_count": count,
                     "synthetic_extent_exceeds_supplied_pixels": name == "wrapped_extent",
                     "pixel_data_read_by_index_decoder": False, **result})
    for size in (0, 1, 4095, 4096, 4097, 8192, 8193):
        payload = bytes((i * 37 + 11) & 255 for i in range(size))
        m = Machine(code)
        m.prepare(payload)
        result = m.result(m.run(CONSUMER, boundary="returned"))
        assert result["return_r0_raw"] == 0 and result["consumer_flag_mode_after"] == [0, 0]
        expected = struct.pack("<II", 1, 0x01020305) + payload
        assert bytes(m.asset[:len(expected)]) == expected
        assert m.asset[len(expected):] == b"\xff" * (len(m.asset) - len(expected))
        assert [w["bytes"] for w in m.write_requests] == ([8] + [4096] * (size // 4096)
                                                        + ([size % 4096] if size % 4096 else []))
        assert [w["address_raw"] for w in m.write_requests] == [m.asset_base] + [
            m.asset_base + 8 + offset for offset in range(0, size, 4096)]
        assert [e["address_raw"] for e in m.erase_requests] == list(
            range(m.asset_base, m.asset_base + len(m.asset), 4096))
        assert [e["operation"] for e in m.parameter_services] == ["erase", "program"]
        assert "header" not in result["live_allocation_roles"] and "buffer" not in result["live_allocation_roles"]
        rows.append({"case": "consumer_copy_chunk_boundary", "payload_tail_bytes": size,
                     "host_copied_prefix_sha256": hashlib.sha256(expected).hexdigest(), **result})
    for fixture, payload, count, expected_count in (
        ("valid_one", frame(), 1, 1),
        ("valid_two", frame() + frame(18, 3, 4), 2, 2),
        ("invalid_first", frame(magic=24), 1, 0),
        ("invalid_second", frame() + frame(magic=24), 2, 1),
        ("truncated_pixel_payload", frame(include_pixels=False), 1, 1),
    ):
        m = Machine(code)
        m.prepare(payload, count=count, old_index=True)
        result = m.result(m.run(CONSUMER, boundary="returned"))
        assert result["runtime_count_raw"] == expected_count and result["return_r0_raw"] == 0
        assert result["consumer_flag_mode_after"] == [0, 0]
        assert [f["role"] for f in m.frees] == ["old_index", "buffer", "header"]
        if fixture == "truncated_pixel_payload":
            assert result["index_entries"] == [{"source_raw": m.asset_base + 8, "extent_raw": 18}]
            assert m.asset[20:38] == b"\xff" * 18
        bitmap = m.run(0x0802a374, boundary="returned")
        assert m.word(BITMAP) == (0x3ffd if expected_count else 0x3fff)
        rows.append({"case": "consumer_reload_and_cleanup", "fixture": fixture,
                     "bitmap_after_separate_refresh": m.word(BITMAP), "bitmap_replay": bitmap, **result})
    for role in ("header", "buffer", "index"):
        m = Machine(code, fail_allocation=role)
        m.prepare(frame(), old_index=True)
        result = m.result(m.run(CONSUMER, boundary="allocation_failure_handler_excluded"))
        assert not m.parameter_services and result["consumer_flag_mode_after"] == [0, 2]
        assert len(m.write_requests) == {"header": 0, "buffer": 1, "index": 2}[role]
        rows.append({"case": "consumer_allocation_failure", "failed_role": role, **result})
    for valid_magic in (True, False):
        m = Machine(code)
        m.prepare(frame(magic=25 if valid_magic else 24), old_index=True)
        m.seed_word(event_address(11), 1)
        result = m.result(m.run(0x0802ab1c, boundary="returned"))
        assert result["consumer_flag_mode_after"] == [0, 0]
        assert result["runtime_count_raw"] == int(valid_magic)
        assert m.word(event_address(11)) == 0
        assert result["post_update_timer_substitutes"] == [
            {"operation": "post_update_timer_substitute", "index": 3, "enabled": 1}]
        rows.append({"case": "callback_after_consumer", "valid_magic_fixture": valid_magic, **result})
    for skip_program in (False, True):
        m = Machine(code, skip_program=skip_program, service_return=17)
        m.prepare(frame())
        result = m.result(m.run(CONSUMER, boundary="returned"))
        assert result["return_r0_raw"] == 0 and result["consumer_flag_mode_after"] == [0, 0]
        assert result["runtime_count_raw"] == (0 if skip_program else 1)
        assert [e["operation"] for e in m.parameter_services] == ["erase", "program"]
        rows.append({"case": "substitute_return_and_skipped_program", "skip_host_program": skip_program,
                     "service_return_fixture": 17, "real_driver_error_semantics_verified": False, **result})
    for fixture, limit, times, statuses, expected_reads in (
        ("already_ready", 2, [0, 0], [0], 1),
        ("busy_then_ready", 2, [0, 0, 1], [1, 0], 2),
        ("busy_past_deadline", 2, [0, 0, 1, 3], [1, 1], 2),
        ("at_deadline_still_polls", 2, [0, 2], [0], 1),
        ("zero_deadline_elapsed", 0, [0, 1], [], 0),
        ("zero_deadline_ready", 0, [0, 0], [0], 1),
        ("sentinel_skips_deadline", 0xffffffff, [0], [1, 1, 0], 3),
    ):
        m = WaitMachine(code, times, statuses)
        result = m.run(WAIT, SPI_CONTEXT, limit, boundary="returned")
        result["return_r0_raw"] = m.uc.reg_read(UC_ARM_REG_R0)
        result["instruction_limit_per_entry"] = 10000  # Inherited pending replay bound.
        assert not m.times and not m.statuses and m.status_reads == expected_reads
        assert m.command_calls == 1 and result["return_r0_raw"] == 255
        assert m.uc.mem_read(WAIT_FLAG, 1)[0] == 1
        rows.append({"case": "isolated_busy_wait", "fixture": fixture, "deadline_raw": limit,
                     "clock_values_fixture": times, "status_values_fixture": statuses,
                     "status_read_substitutes": m.status_reads, "clock_read_substitutes": m.clock_reads,
                     "spi_channel_fixture": 255, "physical_time_units_verified": False, **result})
    return {"model": "A1763", "lcd_manifest_version": "0.1.9.6", "lcd_sha256": IMAGE_SHA256,
            "application_sha256": CODE_SHA256, "cases": len(rows), "results": rows,
            "instruction_limit_per_entry": LIMIT,
            "maximum_entry_instructions": max(r["instructions"] for r in rows),
            "negative_guards": negative_guards(code), "station_commands_sent": 0,
            "static_failure_boundary": {"address": "08007154", "end_exclusive": "08007176",
                "body_sha256": hashlib.sha256(code[0x08007154 - BASE:0x08007176 - BASE]).hexdigest(),
                "reset_register_raw": 0xe000ed0c, "terminal_loop_address": "08007174",
                "stub_executed": False, "recovery_verified": False},
            "ingress_crc_replayed": False, "physical_flash_verified": False,
            "rendering_verified": False, "limits": __doc__.strip()}


def negative_guards(code: bytes) -> list[str]:
    def reject_next_frame_read(m: Machine) -> None:
        m.prepare(frame(16, 65535, 65535, include_pixels=False), count=2, standalone=True)
        m.run(READER, m.mutable_record, CATALOG + 12)

    rejected = []
    for name, action in (
        ("boot_excluded", lambda m: m.step(m.uc, 0x08006144, 2, None)),
        ("reset_excluded", lambda m: m.step(m.uc, 0x08006f80, 2, None)),
        ("real_spi_driver_excluded", lambda m: m.step(m.uc, 0x08010656, 2, None)),
        ("real_flash_driver_excluded", lambda m: m.step(m.uc, 0x08013f86, 2, None)),
        ("format_inline_table_not_code", lambda m: m.step(m.uc, 0x08017fd8, 2, None)),
        ("mmio_read_excluded", lambda m: m.read_guard(None, 0, 0x40013000, 4, 0, None)),
        ("mmio_write_excluded", lambda m: m.write_guard(None, 0, 0x40022004, 4, 0, None)),
        ("catalog_write_excluded", lambda m: m.write_guard(None, 0, CATALOG + 4, 4, 0, None)),
        ("inactive_runtime_write_excluded", lambda m: m.write_guard(None, 0, RUNTIME + 12, 4, 0, None)),
        ("synthetic_asset_overread", lambda m: m.read_storage(m.asset_base + len(m.asset), 1)),
        ("synthetic_staging_overread", lambda m: m.read_storage(STAGING_BASE + len(m.storage), 1)),
        ("index_next_frame_outside_host_storage", reject_next_frame_read),
        ("unbounded_name_excluded", lambda m: m.cstring(0x2001c000)),
    ):
        try:
            action(Machine(code))
        except (AssertionError, RuntimeError) as exc:
            if name == "index_next_frame_outside_host_storage":
                assert str(exc) in ("Synthetic asset read extent", "Synthetic staging read extent")
            rejected.append(name)
        else:
            raise AssertionError(f"Accepted {name}")
    return rejected


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
        p = args.output_dir / f"lcd-resource-copy-{suffix}.json"
        p.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
        p.chmod(0o600)
    print(json.dumps({"synthetic_cases": result["cases"], "negative_guards": len(result["negative_guards"]),
                      "station_commands_sent": 0}))


if __name__ == "__main__":
    main()
