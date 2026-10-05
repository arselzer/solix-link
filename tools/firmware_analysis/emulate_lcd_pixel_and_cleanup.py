#!/usr/bin/env python3
"""Bounded LCD pixel-row requests, image-size math and transfer cleanup replay.

Actual selected pixel-acquisition, format, seek/read dispatch, image-size,
full-transfer, polling transport and DMA-disable/release instructions execute.
Decoder state, row buffer and file index are supplied; heap/string/storage
helpers remain host substitutes. Clock, status, receive data and SPI/DMA blocks
are synthetic RAM, and callback returns/flag changes are explicit substitutes.
No actual MMIO, DMA, interrupts, rendering, flash program/erase, reset, boot,
station commands or successful physical installation is established.
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
                               UC_ARM_REG_SP, UC_ARM_REG_PC, UC_ARM_REG_PRIMASK)

from emulate_lcd_asset_status import STACK, STOP
from emulate_lcd_asset_transfer import IMAGE_SHA256, CODE_SHA256, checked_image
from emulate_lcd_resource_copy import BITS, FORMAT_ALLOW, extent
from emulate_lcd_frame_stream import (
    BoundedMachine, FrameMachine, TransportMachine, within, HEAP, BUFFER,
    HANDLE, DRIVER, INDEX, RUNTIME, SPI_CHANNEL, REGISTERS,
    CLOCK, SEND, RECEIVE, CLOSE,
)

DECODER, SCRATCH, FILE, DRAW, PIXELS = (0x20019000, 0x20019200, 0x20019400,
                                      0x20019800, 0x2001b000)
AREA, ROW = 0x20018a00, 0x20018b00
ACQUIRE, READ_AT, SIZE = 0x08017604, 0x0800fb14, 0x080076d8
FULL, RELEASE = 0x080158bc, 0x080155ec
RX_DISABLE, TX_DISABLE = 0x08015698, 0x080159b4
STRIDE_CALLBACK, BEGIN_CALLBACK, END_CALLBACK = 0x08004004, 0x08004008, 0x0800400c
RX_DESC, TX_DESC = 0x20018900, 0x20018920
RX_REGS, TX_REGS = 0x2001b800, 0x2001b900
SIZE_ALLOW = ((SIZE, 0x0800772c),)
# Exclude palette conversion and inline branch-table bytes; this proof selects
# formats 15..20 and rejects unsupported formats before those branches.
PIXEL_ALLOW = ((ACQUIRE, 0x08017700), (0x08017794, 0x080177d2),
               (0x080177e0, 0x08017840), (0x08017906, 0x08017918),
               (READ_AT, 0x0800fb76), *FORMAT_ALLOW)
CLEANUP_ALLOW = ((FULL, 0x0801599e), (RELEASE, 0x08015624),
                 (RX_DISABLE, 0x080156dc), (TX_DISABLE, 0x080159f8))


class PixelMachine(FrameMachine):
    def __init__(self, code: bytes, *, fmt: int = 15, width: int = 2,
                 height: int = 3, include_header_in_extent: bool = False,
                 indexed_extent: int | None = None, row_stride: int | None = None,
                 column: int = 0) -> None:
        assert fmt in (15, 16, 17, 18, 19, 20, 21)
        assert 0 < width <= 16 and 0 < height <= 8 and 0 <= column < width
        size = extent(fmt, width, height)
        recorded = size + (12 if include_header_in_extent else 0)
        if indexed_extent is not None:
            recorded = indexed_extent
        super().__init__(code, length=recorded)
        source_stride = ((BITS.get(fmt, 0) * width + 7) // 8)
        selected_stride = source_stride if row_stride is None else row_stride
        assert 0 <= selected_stride <= 64
        self.uc.mem_write(DECODER, bytes(64))
        self.uc.mem_write(SCRATCH, bytes(64))
        self.uc.mem_write(DRAW, bytes(24))
        self.uc.mem_write(PIXELS, b"\xcc" * 64)
        header = struct.pack("<BB5H", 25, fmt, 0x20, width, height, source_stride, 0)
        payload = bytes((i * 37 + 11) & 255 for i in range(size))
        # Final padding permits admitted synthetic cross-frame/header reads;
        # it does not represent valid pixel data or a capacity validation.
        self.asset = bytearray(struct.pack("<II", 2, 0x01020305) + header + payload + bytes(64))
        self.uc.mem_write(DECODER + 0x10, header)
        self.uc.mem_write(DECODER + 0x0c, b"\1")  # File source, so actual offset adds twelve.
        self.seed_word(DECODER + 0x38, SCRATCH)
        self.seed_word(SCRATCH, FILE)
        self.seed_word(SCRATCH + 0x3c, DRAW)
        self.uc.mem_write(DRAW, struct.pack("<BB5HIII", 25, fmt, 0x30,
                                           width - column, 1, selected_stride, 0,
                                           selected_stride, PIXELS, PIXELS))
        self.uc.mem_write(AREA, struct.pack("<4i", column, 0, width - 1, height - 1))
        # Supplied existing row buffer avoids the allocation-sentinel branch.
        self.uc.mem_write(ROW, struct.pack("<4i", column, -1, width - 1, -1))
        opened = self.open()
        assert opened["return_r0_raw"] == HANDLE
        self.uc.mem_write(FILE, struct.pack("<3I", HANDLE, DRIVER, 0))
        self.pixel_read_counts: list[dict] = []
        self.fmt, self.width, self.height, self.column = fmt, width, height, column
        self.source_stride, self.row_stride = source_stride, selected_stride
        self.expected_pixels = bytearray(b"\xcc" * 64)

    def read_guard(self, uc, access, address, size, value, data) -> None:
        if within(address, size, [(DECODER, DECODER + 64), (SCRATCH, SCRATCH + 64),
                                  (FILE, FILE + 12), (DRAW, DRAW + 24), (PIXELS, PIXELS + 64),
                                  (AREA, AREA + 16), (ROW, ROW + 16)]):
            return
        super().read_guard(uc, access, address, size, value, data)

    def write_guard(self, uc, access, address, size, value, data) -> None:
        if within(address, size, [(DECODER + 0x1c, DECODER + 0x20), (PIXELS, PIXELS + 64),
                                  (ROW, ROW + 16)]):
            return
        super().write_guard(uc, access, address, size, value, data)

    def step(self, uc, address, size, data) -> None:
        if address == 0x0801e23a:
            stack = uc.reg_read(unicorn.arm_const.UC_ARM_REG_SP)
            self.pixel_read_counts.append({"internal_count_raw": self.word(stack + 0x14),
                "caller_requested_count_pointer": uc.reg_read(unicorn.arm_const.UC_ARM_REG_R8) != 0})
        if within(address, size, list(PIXEL_ALLOW)):
            self.instructions += 1
        else:
            super().step(uc, address, size, data)

    def row(self) -> dict:
        first_reads, first_counts = len(self.reads), len(self.pixel_read_counts)
        result = self.run(ACQUIRE, 0, DECODER, AREA, ROW)
        coordinates = list(struct.unpack("<4i", self.uc.mem_read(ROW, 16)))
        row_index = coordinates[1]
        if result["return_r0_raw"] == 1:
            bits = BITS[self.fmt]
            source = 12 + row_index * self.source_stride + self.column * bits // 8
            requested = ((self.width - self.column) * bits // 8 if self.fmt != 20 else self.row_stride)
            requests = [(source, requested, 0)]
            if self.fmt == 20:
                alpha_source = (12 + self.source_stride * self.height +
                                (self.source_stride // 2) * row_index + self.column)
                requests.append((alpha_source, self.width - self.column, self.row_stride))
            expected_counts = []
            for source, requested, destination in requests:
                count = min(requested, max(0, self.length - source))
                expected_counts.append(count)
                self.expected_pixels[destination:destination + count] = self.asset[8 + source:8 + source + count]
            assert [c["internal_count_raw"] for c in self.pixel_read_counts[first_counts:]] == expected_counts
        assert bytes(self.uc.mem_read(PIXELS, 64)) == bytes(self.expected_pixels)
        return {**result, "row_coordinates_raw": list(struct.unpack("<4i", self.uc.mem_read(ROW, 16))),
                "row_buffer_sha256": hashlib.sha256(bytes(self.uc.mem_read(PIXELS, 64))).hexdigest(),
                "storage_reads": self.reads[first_reads:],
                "filesystem_read_counts": self.pixel_read_counts[first_counts:],
                "decoded_pointer_present": self.word(DECODER + 0x1c) == DRAW,
                "exact_host_buffer_and_unwritten_suffix_verified": True,
                "physical_rendering_verified": False}


class SizeMachine(BoundedMachine):
    def __init__(self, code: bytes, *, callback_stride: int | None = None) -> None:
        super().__init__(code)
        assert callback_stride is None or 0 <= callback_stride <= 0xffffffff
        self.callback_stride = callback_stride
        self.callback_calls: list[dict] = []
        self.seed_word(HEAP + 0xd8, STRIDE_CALLBACK | 1 if callback_stride is not None else 0)

    def read_guard(self, uc, access, address, size, value, data) -> None:
        if within(address, size, [(HEAP + 0xd8, HEAP + 0xdc)]):
            return
        super().read_guard(uc, access, address, size, value, data)

    def step(self, uc, address, size, data) -> None:
        self.instructions += 1
        if address == STOP:
            self.stop("returned")
        elif address == STRIDE_CALLBACK:
            assert self.callback_stride is not None
            self.callback_calls.append({"width_raw": uc.reg_read(UC_ARM_REG_R0),
                                        "format_raw": uc.reg_read(UC_ARM_REG_R1),
                                        "supplied_stride_raw": self.callback_stride})
            self.back(self.callback_stride)
        elif not within(address, size, list(SIZE_ALLOW)):
            raise RuntimeError(f"Excluded image-size instruction {address:08x}")

    def run(self, entry: int, r0: int, r1: int, r2: int, r3: int) -> dict:
        assert entry == SIZE
        stack = STACK - 0x200
        for register, value in ((UC_ARM_REG_R0, r0), (UC_ARM_REG_R1, r1),
                                (UC_ARM_REG_R2, r2), (UC_ARM_REG_R3, r3),
                                (UC_ARM_REG_SP, stack), (UC_ARM_REG_LR, STOP | 1),
                                (UC_ARM_REG_R11, HEAP), (UC_ARM_REG_PRIMASK, 0)):
            self.uc.reg_write(register, value)
        before = self.instructions
        # A host PC boundary avoids Unicorn's code-hook stop behavior following
        # this function's conditional POP. No sentinel instruction or CPU-state
        # normalization executes; all firmware returns target this supplied LR.
        self.uc.emu_start(entry | 1, STOP, count=10000)
        assert self.uc.reg_read(UC_ARM_REG_PC) == STOP, "Image-size instruction bound exceeded"
        assert self.uc.reg_read(UC_ARM_REG_SP) == stack
        assert self.uc.reg_read(UC_ARM_REG_PRIMASK) == 0
        used = self.instructions - before
        self.maximum = max(self.maximum, used)
        return {"entry_address": f"{entry:08x}", "instructions": used,
                "return_r0_raw": self.uc.reg_read(UC_ARM_REG_R0), "boundary": "returned",
                "termination": "host_pc_before_return_sentinel", "interrupt_mask_preserved": True}


class CleanupMachine(TransportMachine):
    def __init__(self, code: bytes, *, flags: tuple[int, int] = (0, 0),
                 dma: bool = False, clear_flag_at_poll: int | None = None,
                 callback: bool = False, **kwargs) -> None:
        super().__init__(code, **kwargs)
        assert len(flags) == 2 and all(f in (0, 1) for f in flags)
        assert clear_flag_at_poll in (None, 0, 1)
        self.uc.mem_write(SPI_CHANNEL, bytes(flags))
        self.callback_calls, self.disable_calls, self.phase_returns = [], [], []
        self.dma_writes: list[dict] = []
        self.clear_flag_at_poll = clear_flag_at_poll
        self.flag_changes: list[dict] = []
        self.register_control_writes: list[int] = []
        self.dma = dma
        self.seed_word(REGISTERS + 4, 0x33)
        if dma:
            for descriptor, registers, offset in ((RX_DESC, RX_REGS, 0x18), (TX_DESC, TX_REGS, 0x1c)):
                self.uc.mem_write(descriptor, bytes(8))
                self.seed_word(descriptor + 4, registers)
                self.seed_word(registers + 8, 0xa1)
                self.seed_word(SPI_CHANNEL + offset, descriptor)
        if callback:
            self.seed_word(SPI_CHANNEL + 0x0c, 0x13579bdf)  # Synthetic caller argument.
            self.seed_word(SPI_CHANNEL + 0x10, BEGIN_CALLBACK | 1)
            self.seed_word(SPI_CHANNEL + 0x14, END_CALLBACK | 1)

    def read_guard(self, uc, access, address, size, value, data) -> None:
        if address == CLOCK and self.clock_reads == 1 and self.clear_flag_at_poll is not None:
            offset = self.clear_flag_at_poll
            uc.mem_write(SPI_CHANNEL + offset, b"\0")
            self.flag_changes.append({"clock_read_index": self.clock_reads,
                                      "channel_byte_offset": offset, "host_supplied_value": 0})
        if within(address, size, [(REGISTERS + 4, REGISTERS + 8), (RX_DESC, RX_DESC + 8),
                                  (TX_DESC, TX_DESC + 8), (RX_REGS + 8, RX_REGS + 12),
                                  (TX_REGS + 8, TX_REGS + 12)]):
            return
        super().read_guard(uc, access, address, size, value, data)

    def write_guard(self, uc, access, address, size, value, data) -> None:
        if address == REGISTERS + 4:
            assert size == 4
            self.register_control_writes.append(value & 0xffffffff)
            return
        if address in (RX_REGS + 8, TX_REGS + 8):
            assert size == 4 and self.dma
            self.dma_writes.append({"direction": "receive" if address == RX_REGS + 8 else "send",
                                    "value_raw": value & 0xffffffff})
            return
        super().write_guard(uc, access, address, size, value, data)

    def step(self, uc, address, size, data) -> None:
        if address in (0x0801597e, 0x0801598c):
            self.phase_returns.append({"phase": "send" if address == 0x0801597e else "after_optional_receive",
                                       "return_r0_raw": uc.reg_read(UC_ARM_REG_R0)})
        if address in (RX_DISABLE, TX_DISABLE):
            assert uc.reg_read(UC_ARM_REG_R0) == SPI_CHANNEL
            self.disable_calls.append("receive" if address == RX_DISABLE else "send")
        if address in (BEGIN_CALLBACK, END_CALLBACK):
            self.instructions += 1
            assert uc.reg_read(UC_ARM_REG_R0) == 0x13579bdf
            result = 17 if address == BEGIN_CALLBACK else 7
            self.callback_calls.append({"phase": "begin" if address == BEGIN_CALLBACK else "end",
                                        "supplied_return_raw": result})
            self.back(result)
        elif within(address, size, list(CLEANUP_ALLOW)):
            self.instructions += 1
        else:
            super().step(uc, address, size, data)

    def finish(self, result: dict) -> dict:
        return {**super().finish(result), "synthetic_dma_contexts_present": self.dma,
                "dma_disable_calls": self.disable_calls, "synthetic_dma_writes": self.dma_writes,
                "synthetic_spi_control_writes": self.register_control_writes,
                "phase_returns": self.phase_returns, "callback_substitutes": self.callback_calls,
                "host_flag_changes": self.flag_changes, "physical_dma_verified": False}


def reference_size(fmt: int, height: int, stride: int) -> int:
    value = stride * height
    if fmt == 20:
        value += (stride >> 1) * height
    value += {7: 8, 8: 16, 9: 64, 10: 1024}.get(fmt, 0)
    return value & 0xffffffff


def suite(image: bytes) -> dict:
    code = checked_image(image)
    rows, maximum = [], 0

    def record(machine: BoundedMachine, name: str, result: dict, **metadata) -> None:
        nonlocal maximum
        maximum = max(maximum, machine.maximum)
        rows.append({"case": name, **metadata, **result})

    for fmt in (15, 16, 17, 18, 19, 20):
        for include_header in (False, True):
            m = PixelMachine(code, fmt=fmt, include_header_in_extent=include_header)
            captures = [m.row() for _ in range(3)]
            assert all(r["return_r0_raw"] == 1 and r["decoded_pointer_present"] for r in captures)
            assert all(not count["caller_requested_count_pointer"] for r in captures
                       for count in r["filesystem_read_counts"])
            counts = [count["internal_count_raw"] for r in captures for count in r["filesystem_read_counts"]]
            stride = (BITS[fmt] * 2 + 7) // 8
            if include_header:
                expected = [stride, 2] * 3 if fmt == 20 else [stride] * 3
            elif fmt == 20:
                expected = [4, 0, 2, 0, 0, 0]
            elif fmt == 16:
                expected = [8, 8, 2]
            elif fmt in (15, 19):
                expected = [6, 0, 0]
            elif fmt == 17:
                expected = [8, 4, 0]
            else:
                expected = [0, 0, 0]
            assert counts == expected, (fmt, include_header, counts, expected)
            ended = m.row()
            assert ended["return_r0_raw"] == 0 and not ended["filesystem_read_counts"] and not ended["storage_reads"]
            record(m, "actual_file_pixel_rows", {"rows": captures, "area_end": ended}, format_fixture=fmt,
                   synthetic_header_included_in_extent=include_header,
                   source_stride_raw=stride, indexed_extent_raw=m.length,
                   receiver_changed=False, short_rows_report_success=not include_header)
            closed = m.run(CLOSE, DRIVER, HANDLE)
            assert closed["return_r0_raw"] == 0
    for fixture, kwargs, expected_counts in (
        ("zero_extent", {"indexed_extent": 0}, [0]),
        ("partial_first_row", {"indexed_extent": 15}, [3]),
        ("subregion_column", {"column": 1, "row_stride": 3, "include_header_in_extent": True}, [3]),
        ("padded_row", {"row_stride": 8, "include_header_in_extent": True}, [6]),
    ):
        m = PixelMachine(code, **kwargs)
        # For padded output, actual requested row size follows column count, not
        # the supplied destination's eight-byte stride.
        result = m.row()
        assert result["return_r0_raw"] == 1
        assert [c["internal_count_raw"] for c in result["filesystem_read_counts"]] == expected_counts
        record(m, "short_or_subregion_row", result, fixture=fixture,
               bytes_after_reported_count_preserved=True)
    m = PixelMachine(code, fmt=21)
    before = bytes(m.uc.mem_read(PIXELS, 64))
    result = m.row()
    assert result["return_r0_raw"] == 0 and not m.reads and bytes(m.uc.mem_read(PIXELS, 64)) == before
    record(m, "unsupported_pixel_format", result, format_fixture=21)
    for fmt in (*range(22), 255):
        for callback_stride in (None, 6):
            m = SizeMachine(code, callback_stride=callback_stride)
            result = m.run(SIZE, 2, 3, fmt, 0)
            expected = reference_size(fmt, 3, callback_stride or 0)
            assert result["return_r0_raw"] == expected
            assert len(m.callback_calls) == int(callback_stride is not None)
            record(m, "actual_image_size", result, format_fixture=fmt, height_fixture=3,
                   explicit_stride_fixture=0, supplied_stride_callback=callback_stride,
                   callback_substitutes=m.callback_calls)
    for fmt, height, stride in ((15, 3, 6), (20, 3, 5), (7, 0, 0xffffffff),
                                (10, 65535, 0xffffffff), (20, 65535, 0xffffffff)):
        m = SizeMachine(code, callback_stride=17)
        result = m.run(SIZE, 2, height, fmt, stride)
        assert not m.callback_calls and result["return_r0_raw"] == reference_size(fmt, height, stride)
        record(m, "explicit_stride_and_wrap", result, format_fixture=fmt,
               height_fixture=height, explicit_stride_fixture=stride,
               allocator_or_pixel_access_executed=False)
    for dma in (False, True):
        for callback in (False, True):
            for fixture, clocks, statuses, received, receive_length, send_length, expected_phase in (
                ("ready", [0, 0, 0, 0, 0], [3, 3, 3, 3], [0x42], 1, 1, [0, 0]),
                ("send_timeout", [0, 0, 101], [0], [], 1, 1, [11, 11]),
                ("receive_timeout", [0, 0, 0, 0, 101], [3, 3, 0], [], 1, 1, [0, 11]),
                ("invalid_send_length", [0], [], [], 1, 0, [6, 6]),
                ("send_only", [0, 0, 0], [3, 3], [], 0, 1, [0, 0]),
            ):
                m = CleanupMachine(code, dma=dma, callback=callback, clocks=clocks,
                                   statuses=statuses, received=received)
                result = m.finish(m.run(FULL, SPI_CHANNEL, BUFFER, send_length,
                                       BUFFER + 32, fifth=receive_length))
                assert result["return_r0_raw"] == (7 if callback else 255)
                assert [r["return_r0_raw"] for r in m.phase_returns] == expected_phase
                expected_flags = ([0, 1] if fixture == "send_timeout" else
                                  [1, 0] if fixture == "receive_timeout" else [0, 0])
                assert result["channel_flags_after"] == ([0, 0] if dma else expected_flags)
                assert m.disable_calls == ["receive", "send"]
                assert len(m.dma_writes) == (2 if dma else 0)
                assert m.word(REGISTERS + 4) == (0x30 if dma else 0x33)
                if dma:
                    assert m.word(RX_REGS + 8) == m.word(TX_REGS + 8) == 0xa0
                assert len(m.callback_calls) == (2 if callback else 0)
                record(m, "actual_full_transfer_cleanup", result, fixture=fixture,
                       callback_substitutes_enabled=callback, receive_length_fixture=receive_length)
    for flags, clear_offset, clocks, expected_disable in (
        ((1, 0), None, [0, 3001, 3001, 3001], 4),
        ((0, 1), None, [0, 3001, 3001, 3001], 4),
        ((1, 0), 0, [0, 1, 3000, 3001, 3001, 3001], 4),
        ((0, 1), 1, [0, 1, 1, 1], 2),
    ):
        m = CleanupMachine(code, flags=flags, dma=True, clocks=clocks,
                           statuses=[3, 3], clear_flag_at_poll=clear_offset)
        result = m.finish(m.run(FULL, SPI_CHANNEL, BUFFER, 1, BUFFER + 32, fifth=0))
        assert result["return_r0_raw"] == 255 and len(m.disable_calls) == expected_disable
        assert result["channel_flags_after"] == [0, 0]
        record(m, "busy_wait_and_supplied_completion", result,
               initial_flags_fixture=list(flags), clock_fixture=clocks,
               cleared_flag_offset_fixture=clear_offset, physical_concurrency_verified=False)
    for null_channel in (True, False):
        m = CleanupMachine(code)
        if not null_channel:
            m.seed_word(SPI_CHANNEL + 8, 0)
        result = m.finish(m.run(FULL, 0 if null_channel else SPI_CHANNEL, BUFFER, 1, BUFFER + 32, fifth=1))
        assert result["return_r0_raw"] == 0 and not m.disable_calls and not m.phase_returns
        record(m, "null_transfer_context", result, null_channel_fixture=null_channel)
    return {"model": "A1763", "lcd_manifest_version": "0.1.9.6", "lcd_sha256": IMAGE_SHA256,
            "application_sha256": CODE_SHA256, "cases": len(rows), "results": rows,
            "instruction_limit_per_entry": 10000, "maximum_entry_instructions": maximum,
            "negative_guards": negative_guards(code), "station_commands_sent": 0,
            "real_mmio_executed": False, "physical_rendering_verified": False,
            "physical_flash_verified": False, "limits": __doc__.strip()}


def negative_guards(code: bytes) -> list[str]:
    rejected = []
    for name, action in (
        ("boot_excluded", lambda m: m.step(m.uc, 0x08006144, 2, None)),
        ("reset_body_excluded", lambda m: m.step(m.uc, 0x08007156, 2, None)),
        ("image_buffer_allocator_excluded", lambda m: m.step(m.uc, 0x080183f0, 2, None)),
        ("palette_conversion_excluded", lambda m: m.step(m.uc, 0x08017844, 2, None)),
        ("inline_palette_table_excluded", lambda m: m.step(m.uc, 0x0801785c, 2, None)),
        ("mmio_read_excluded", lambda m: m.read_guard(m.uc, 0, 0x40013008, 4, 0, None)),
        ("mmio_write_excluded", lambda m: m.write_guard(m.uc, 0, 0x4001300c, 4, 0, None)),
        ("runtime_write_excluded", lambda m: m.write_guard(m.uc, 0, RUNTIME, 4, 0, None)),
        ("index_write_excluded", lambda m: m.write_guard(m.uc, 0, INDEX, 4, 0, None)),
        ("pixel_buffer_overwrite_excluded", lambda m: m.write_guard(m.uc, 0, PIXELS + 64, 1, 0, None)),
    ):
        try:
            action(PixelMachine(code))
        except (AssertionError, RuntimeError):
            rejected.append(name)
        else:
            raise AssertionError(f"Accepted {name}")
    for name, action in (
        ("physical_dma_read_excluded", lambda m: m.read_guard(m.uc, 0, 0x40020408, 4, 0, None)),
        ("physical_dma_write_excluded", lambda m: m.write_guard(m.uc, 0, 0x40020408, 4, 0, None)),
        ("flash_program_excluded", lambda m: m.step(m.uc, 0x0801058e, 2, None)),
        ("mode_initialization_excluded", lambda m: m.step(m.uc, 0x08015624, 2, None)),
        ("missing_clock_excluded", lambda m: m.run(FULL, SPI_CHANNEL, BUFFER, 1, BUFFER + 32, fifth=0)),
    ):
        try:
            action(CleanupMachine(code))
        except (AssertionError, RuntimeError) as exc:
            if name == "missing_clock_excluded":
                assert str(exc) == "Synthetic clock exhausted"
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
        p = args.output_dir / f"lcd-pixel-cleanup-{suffix}.json"
        p.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
        p.chmod(0o600)
    print(json.dumps({"synthetic_cases": result["cases"], "negative_guards": len(result["negative_guards"]),
                      "maximum_entry_instructions": result["maximum_entry_instructions"],
                      "station_commands_sent": 0}))


if __name__ == "__main__":
    main()
