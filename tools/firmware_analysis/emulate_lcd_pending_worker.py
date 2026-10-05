#!/usr/bin/env python3
"""Bounded LCD scheduler, resource bitmap and pending-marker consumer replay.

Selected timer-body, event activation/queue removal, callback, catalog placement,
bitmap and marker-consumer instructions execute with synthetic state. Timer
registers are RAM; heap, SPI reads, memory clearing, logs and post-update timer
notification are explicit substitutes. Stop BEFORE reset preparation, sector
erase, parameter erase/program or allocation-failure handlers. No boot, physical
timer/interrupt, flash write, resource installation or station command executes.
Consumer fixtures bypass ingress/CRC and use supplied versions and saved words;
they establish selected decisions and the first erase destination, not recovery.
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
                               UC_ARM_REG_R3, UC_ARM_REG_SP, UC_ARM_REG_LR,
                               UC_ARM_REG_PRIMASK)

from emulate_lcd_asset_status import BASE, STACK, STOP, Machine as StatusMachine
from emulate_lcd_resource_selection import (
    CATALOG, CATALOG_BYTES, MARKER_FLASH, host_unpack_rw, package,
)
from emulate_lcd_asset_transfer import (
    IMAGE_SHA256, CODE_SHA256, RESOURCE_HEADER, SPI_CONTEXT, STAGING_BASE,
    checked_image,
)

EVENTS, EVENT_BYTES, QUEUE = 0x20000290, 14 * 32, 0x2000045c
RUNTIME_PTR, RUNTIME = 0x20000710, 0x20019600
TIMER = 0x20019700
BITMAP = 0x2000022c
VERSION_PAGE = 0x08040000
CONSUMER = 0x0802a438
LIMIT = 10000
ALLOW = ((0x08006218, 0x0800625c), (0x080074c8, 0x08007574),
         (0x0802a374, 0x0802a40c), (CONSUMER, 0x0802aa8e),
         (0x0802ab1c, 0x0802ab44), (0x0802bf46, 0x0802bf92),
         (0x0802c184, 0x0802c220), (0x0803027a, 0x080302f0))


def event_address(index: int) -> int:
    assert 0 <= index < 14
    return EVENTS + index * 32


class Machine(StatusMachine):
    def __init__(self, code: bytes, *, resolve_catalog: bool = True,
                 stop_at_consumer: bool = False) -> None:
        super().__init__(code)
        fixture_regions = ((RESOURCE_HEADER - 4, RESOURCE_HEADER + 1024),
                           (RUNTIME, RUNTIME + 14 * 12), (TIMER, TIMER + 20))
        for index, (lo, hi) in enumerate(fixture_regions):
            assert all(hi <= other_lo or other_hi <= lo
                       for other_lo, other_hi in fixture_regions[index + 1:])
        rw, consumed = host_unpack_rw(code)
        assert consumed == 421
        assert hashlib.sha256(rw).hexdigest() == (
            "cfbbfae6df83687b91c87016bb40622b3e1a41da1698d7f13b888d1f1e909b5f")
        self.uc.mem_write(CATALOG, rw[CATALOG - 0x20000000:CATALOG - 0x20000000 + CATALOG_BYTES])
        self.uc.mem_write(EVENTS, rw[EVENTS - 0x20000000:EVENTS - 0x20000000 + EVENT_BYTES])
        # Model only one active event, with explicitly host-initialized lists.
        for index in range(14):
            self.seed_word(event_address(index), 0)
            node = event_address(index) + 24
            self.uc.mem_write(node, struct.pack("<II", node, node))
        self.uc.mem_write(QUEUE, struct.pack("<II", QUEUE, QUEUE))
        self.seed_word(0x20000458, TIMER)
        self.uc.mem_write(TIMER, bytes(20))
        self.seed_word(RUNTIME_PTR, RUNTIME)
        self.uc.mem_write(RUNTIME, bytes(14 * 12))
        self.seed_word(0x2000070c, SPI_CONTEXT)
        self.seed_word(0x200148c0, 0x20019e00)
        self.uc.mem_map(VERSION_PAGE, 0x1000)
        self.seed_word(VERSION_PAGE, 0x01020304)  # Absent page, supplied fixture.
        self.uc.mem_protect(VERSION_PAGE, 0x1000, unicorn.UC_PROT_READ)
        self.parameter_seed = bytes(20)
        self.seed_parameter((0, 0x11223344, 0x01000906, 0x19011d01, 0x01020304))
        self.instructions = 0
        self.maximum_entry_instructions = 0
        self.entry_instructions = []
        self.stop_at_consumer = stop_at_consumer
        self.layout_enabled = True
        self.storage = bytes(1024)
        self.calls, self.spi_reads, self.boundary_arguments = [], [], None
        self.header_live = False
        self.compiled_resource_word_observed = False
        self.uc.hook_add(unicorn.UC_HOOK_MEM_READ, self.read_guard)
        if resolve_catalog:
            self.run(0x0802bf46, boundary="catalog_placement_complete")
        self.layout_enabled = False
        self.catalog_before = bytes(self.uc.mem_read(CATALOG, CATALOG_BYTES))

    def seed_word(self, address: int, value: int) -> None:
        self.uc.mem_write(address, struct.pack("<I", value))

    def word(self, address: int) -> int:
        return int.from_bytes(self.uc.mem_read(address, 4), "little")

    def seed_parameter(self, words: tuple[int, int, int, int, int]) -> None:
        self.parameter_seed = struct.pack("<5I", *words)
        self.uc.mem_protect(MARKER_FLASH, 0x1000, unicorn.UC_PROT_ALL)
        self.uc.mem_write(MARKER_FLASH, self.parameter_seed)
        self.uc.mem_protect(MARKER_FLASH, 0x1000, unicorn.UC_PROT_READ | unicorn.UC_PROT_EXEC)

    def read_guard(self, uc, access, address, size, value, data) -> None:
        regions = ((BASE, BASE + 214016), (MARKER_FLASH, MARKER_FLASH + 20),
                   (VERSION_PAGE, VERSION_PAGE + 4), (STACK - 0x1000, STACK),
                   (EVENTS, EVENTS + EVENT_BYTES), (QUEUE, QUEUE + 8),
                   (CATALOG, CATALOG + CATALOG_BYTES), (RUNTIME, RUNTIME + 14 * 12),
                   (RESOURCE_HEADER - 4, RESOURCE_HEADER + 1024), (TIMER, TIMER + 20),
                   (0x20000458, 0x2000045c), (0x2000070c, 0x20000714),
                   (0x200148c0, 0x200148c8))
        assert any(lo <= address < address + size <= hi for lo, hi in regions), (
            f"Unexpected LCD worker read {address:08x}")

    def write_guard(self, uc, access, address, size, value, data) -> None:
        regions = [(STACK - 0x1000, STACK), (QUEUE, QUEUE + 8),
                   (BITMAP, BITMAP + 4), (0x200000f8, 0x200000fa),
                   (TIMER + 16, TIMER + 20), (RESOURCE_HEADER, RESOURCE_HEADER + 1024),
                   (0x200148c4, 0x200148c8)]
        for index in range(14):
            record = event_address(index)
            regions.extend(((record, record + 4), (record + 20, record + 32)))
            if getattr(self, "layout_enabled", False):
                regions.append((CATALOG + index * 12 + 4, CATALOG + index * 12 + 8))
        assert any(lo <= address < address + size <= hi for lo, hi in regions), (
            f"Unexpected LCD worker write {address:08x}")

    def stop(self, boundary: str, arguments: dict | None = None) -> None:
        self.boundary, self.boundary_arguments = boundary, arguments
        self.stopped = True
        self.uc.emu_stop()

    def cstring(self, address: int) -> bytes:
        result = bytearray()
        for offset in range(64):
            assert (BASE <= address + offset < BASE + 214016 or
                    RESOURCE_HEADER <= address + offset < RESOURCE_HEADER + 1024)
            byte = self.uc.mem_read(address + offset, 1)[0]
            if not byte:
                return bytes(result)
            result.append(byte)
        raise AssertionError("Unterminated LCD name")

    def step(self, uc, address, size, data) -> None:
        self.instructions += 1
        r0, r1, r2, r3 = [uc.reg_read(r) for r in (
            UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)]
        if address == STOP:
            self.stop("returned")
        elif address in (0x0802bf92, 0x0802c220):
            self.stop({0x0802bf92: "catalog_placement_complete",
                       0x0802c220: "one_queue_drain_complete"}[address])
        elif address == 0x080109a8:
            self.stop("interrupt_disable_preparation_excluded")
        elif address == 0x080102bc:
            assert r0 == SPI_CONTEXT
            self.stop("first_resource_sector_erase_excluded", {"destination_raw": r1})
        elif address in (0x08013f84, 0x08014140):
            assert (r0, r1) == (MARKER_FLASH, 20)
            candidate = bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_SP) + 0x3c, 20))
            assert candidate[:4] == bytes(4)
            self.stop("parameter_flash_operation_excluded", {
                "destination_raw": r0, "bytes": r1, "candidate_first_word_raw": 0,
                "synthetic_candidate_sha256": hashlib.sha256(candidate).hexdigest()})
        elif address == 0x08007154:
            self.stop("allocation_failure_handler_excluded")
        elif address == CONSUMER:
            self.calls.append({"operation": "marker_consumer_entry", "r0_raw": r0})
            if self.stop_at_consumer:
                self.stop("marker_consumer_entry_excluded")
        elif address == 0x0802a8a0:
            self.stop("marker_not_pending_branch")
        elif address in (0x0802cd4c, 0x0802cda0):
            if address == 0x0802cda0 and uc.reg_read(UC_ARM_REG_LR) & ~1 == 0x0802a480:
                assert r2 == 0x190a1801
                self.compiled_resource_word_observed = True
            self.back()  # Log construction/delivery only.
        elif address == 0x08006b06:
            assert 0 < r1 <= 1024
            self.write_guard(uc, 0, r0, r1, 0, None)
            uc.mem_write(r0, bytes(r1))
            self.back(r0)
        elif address in (0x08006218, 0x08006226):
            self.cstring(r0)
            if address == 0x08006226:
                self.cstring(r1)
            # Execute actual bounded strlen/strcmp.
        elif address == 0x08006242:
            assert r2 == 16 and STACK - 0x1000 <= r0 < r0 + r2 <= STACK
            assert STACK - 0x1000 <= r1 < r1 + r2 <= STACK
        elif address == 0x0802080c:
            assert r0 == 1024 and not self.header_live
            self.header_live = True
            uc.mem_write(RESOURCE_HEADER - 4, struct.pack("<I", 1028))
            self.seed_word(0x200148c4, 1028)
            self.calls.append({"operation": "header_allocation_substitute", "bytes": r0})
            self.back(RESOURCE_HEADER)
        elif address == 0x080104e2:
            assert r0 == SPI_CONTEXT and r3 in (4, 1024)
            assert self.header_live
            if r3 == 1024:
                assert (r1, r2) == (STAGING_BASE, RESOURCE_HEADER)
            else:
                assert STACK - 0x1000 <= r2 < r2 + r3 <= STACK
            offset = r1 - STAGING_BASE
            assert 0 <= offset < offset + r3 <= len(self.storage)
            self.write_guard(uc, 0, r2, r3, 0, None)
            value = bytes(self.storage[offset:offset + r3])
            uc.mem_write(r2, value)
            self.spi_reads.append({"source_raw": r1, "bytes": r3,
                                   "data_sha256": hashlib.sha256(value).hexdigest()})
            self.back()
        elif address == 0x0802606c:
            assert self.header_live and (r0, r1) == (0x20019e00, RESOURCE_HEADER)
            self.header_live = False
            self.calls.append({"operation": "header_free_substitute"})
            self.back()
        elif address == 0x08030200:
            assert (r0, r1) == (3, 1)
            self.calls.append({"operation": "post_update_timer_substitute", "index": r0, "enabled": r1})
            self.back()
        elif not any(lo <= address < hi for lo, hi in ALLOW):
            raise RuntimeError(f"Unexpected LCD worker instruction {address:08x}")

    def run(self, entry: int, r0: int = 0, r1: int = 0, *, boundary: str | None = None) -> dict:
        self.stopped, self.boundary, self.boundary_arguments = False, None, None
        before = self.instructions
        for register, value in ((UC_ARM_REG_R0, r0), (UC_ARM_REG_R1, r1),
                                (UC_ARM_REG_SP, STACK - 0x200), (UC_ARM_REG_LR, STOP | 1),
                                (UC_ARM_REG_PRIMASK, 0)):
            self.uc.reg_write(register, value)
        self.uc.emu_start(entry | 1, 0, count=LIMIT)
        assert self.stopped, "LCD worker instruction bound exceeded"
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
                "stopped_boundary": self.boundary, "boundary_arguments": self.boundary_arguments,
                "parameter_page_preserved": True, "interrupt_mask_restored": True}

    def activate(self, index: int, enabled: bool) -> dict:
        return self.run(0x0803028c, index, int(enabled), boundary="returned")

    def tick(self) -> dict:
        self.seed_word(TIMER + 12, 1)
        self.seed_word(TIMER + 16, 1)
        return self.run(0x080074c8)

    def consume(self, *, name: str, count: int = 1, same_version: bool = False,
                declared_length: int | None = None) -> dict:
        assert 0 <= count <= 0xffffffff
        self.storage = bytearray(package([(name, struct.pack("<I", count), True)]))
        # Explicit consumer fixtures; these do not re-execute ingress/CRC.
        struct.pack_into(">I", self.storage, 24, 0x01020304 if same_version else 0x01020305)
        if declared_length is not None:
            struct.pack_into("<I", self.storage, 16, declared_length)
        for index in range(14):
            self.uc.mem_write(RUNTIME + index * 12, struct.pack("<3I", 1, 0x01020304, 0x20019500))
        self.seed_parameter((0xa5a5a5a5, 0x11223344, 0x01000906, 0x19011d01, 0x01020304))
        result = self.run(CONSUMER)
        assert self.compiled_resource_word_observed
        return {**result, "name_fixture": name, "count_fixture": count,
                "stored_version_matches_fixture": same_version,
                "declared_length_fixture": declared_length or 4,
                "ingress_crc_replayed": False, "consumer_mode_after": self.uc.mem_read(0x200000f9, 1)[0],
                "compiled_resource_word_observed": self.compiled_resource_word_observed,
                "spi_read_substitutes": self.spi_reads, "calls": self.calls,
                "header_live_at_boundary": self.header_live,
                "consumer_error_accumulator_raw": (
                    self.uc.reg_read(unicorn.arm_const.UC_ARM_REG_R9)
                    if self.boundary == "parameter_flash_operation_excluded" else None)}


def suite(image: bytes) -> dict:
    code = checked_image(image)
    resource_offset, resource_bytes = struct.unpack_from("<II", image, 44)
    assert (resource_offset, resource_bytes) == (215040, 710656)
    resource = image[resource_offset:resource_offset + resource_bytes]
    resource_sha = hashlib.sha256(resource).hexdigest()
    assert resource_sha == "f61823dde70028254b74a534f88ed249f21136a776acdfd1d0420bc0a20c247c"
    assert struct.unpack_from("<I", resource)[0] == 0x190a1801
    rows = []
    m = Machine(code)
    rw, _ = host_unpack_rw(code)
    layout = []
    expected_destination = 4096
    for index in range(14):
        name_pointer, destination, capacity = struct.unpack(
            "<3I", m.uc.mem_read(CATALOG + index * 12, 12))
        assert destination == expected_destination and destination + capacity <= STAGING_BASE
        layout.append({"index": index, "name": m.cstring(name_pointer).decode("ascii"),
                       "destination_raw": destination, "capacity_raw": capacity})
        expected_destination = (destination + capacity + 4095) & ~4095
    rows.append({"case": "catalog_placement", "instructions": m.entry_instructions[0],
                 "entries": layout, "region_end_raw": expected_destination,
                 "initialized_data_sha256": hashlib.sha256(rw).hexdigest()})
    events = []
    for index in (1, 11, 13):
        words = struct.unpack_from("<8I", rw, 0x290 + index * 32)
        events.append({"index": index, "callback_address": f"{words[3] & ~1:08x}",
                       "mode_byte_raw": words[1] & 255, "interval_ticks_raw": words[2]})
    assert events == [
        {"index": 1, "callback_address": "0803027a", "mode_byte_raw": 1, "interval_ticks_raw": 3000},
        {"index": 11, "callback_address": "0802ab1c", "mode_byte_raw": 0, "interval_ticks_raw": 3000},
        {"index": 13, "callback_address": "0803027a", "mode_byte_raw": 1, "interval_ticks_raw": 900000}]

    for index in (1, 11):
        for count in (0, 2998, 2999):
            m = Machine(code)
            m.seed_word(event_address(index) + 20, 37)
            activation = m.activate(index, True)
            assert m.word(event_address(index)) == 1 and m.word(event_address(index) + 20) == 0
            m.seed_word(event_address(index) + 20, count)
            result = m.tick()
            due = count == 2999
            assert result["stopped_boundary"] == (
                "interrupt_disable_preparation_excluded" if due and index == 1 else "returned")
            node = event_address(index) + 24
            if due and index == 1:
                assert m.word(event_address(index)) == 0
            elif due:
                assert m.word(QUEUE) == m.word(QUEUE + 4) == node
                assert m.word(node) == m.word(node + 4) == QUEUE
            else:
                assert m.word(event_address(index) + 20) == count + 1
                assert m.word(QUEUE) == m.word(QUEUE + 4) == QUEUE
            rows.append({"case": "scheduler_tick", "index": index, "seeded_counter": count,
                         "activation": activation, **result, "enabled_after": m.word(event_address(index)),
                         "counter_after": m.word(event_address(index) + 20), "queued": due and index == 11})

    m = Machine(code, stop_at_consumer=True)
    m.activate(11, True)
    m.seed_word(event_address(11) + 16, 0x20017200)  # Earlier receipt's supplied context.
    m.seed_word(event_address(11) + 20, 2999)
    m.tick()
    result = m.run(0x0802c184, boundary="marker_consumer_entry_excluded")
    assert m.word(QUEUE) == m.word(QUEUE + 4) == QUEUE
    node = event_address(11) + 24
    assert m.word(node) == m.word(node + 4) == node and m.word(event_address(11)) == 0
    assert m.word(event_address(11) + 16) == 0x20017200
    rows.append({"case": "queued_callback_reaches_consumer", **result,
                 "queue_removed": True, "event_disabled": True, "supplied_argument_word_preserved": True})
    # Disabling a queued event removes it before the main loop can execute it.
    m = Machine(code)
    m.activate(11, True)
    m.seed_word(event_address(11) + 20, 2999)
    m.tick()
    result = m.activate(11, False)
    assert m.word(QUEUE) == m.word(QUEUE + 4) == QUEUE
    rows.append({"case": "disable_removes_queued_event", **result, "queue_removed": True})

    m = Machine(code)
    m.activate(11, True)
    m.seed_word(event_address(11) + 20, 37)
    result = m.activate(11, True)
    assert m.word(event_address(11) + 20) == 37
    rows.append({"case": "repeated_activation_preserves_counter", **result, "counter_after": 37})
    m = Machine(code)
    m.seed_word(event_address(11) + 8, 0)  # Explicit disabled-interval fixture.
    m.seed_word(event_address(11) + 20, 37)
    result = m.activate(11, True)
    assert m.word(event_address(11)) == 0 and m.word(event_address(11) + 20) == 37
    rows.append({"case": "zero_interval_rejects_activation", **result, "enabled_after": 0})
    m = Machine(code)
    m.activate(11, True)
    m.seed_word(event_address(11) + 20, 2999)
    m.tick()
    m.seed_word(event_address(11) + 20, 2999)
    result = m.tick()
    node = event_address(11) + 24
    assert m.word(QUEUE) == m.word(QUEUE + 4) == node
    assert m.word(node) == m.word(node + 4) == QUEUE
    rows.append({"case": "queued_event_not_duplicated", **result, "single_queue_node": True})

    for bitmap_fixture in (None, 0, 0x3fff, 0x1555, *(1 << i for i in range(14))):
        m = Machine(code)
        if bitmap_fixture is None:
            m.seed_word(RUNTIME_PTR, 0)
            expected = 0x3fff
        else:
            expected = bitmap_fixture
            for index in range(14):
                missing = bool(expected & (1 << index))
                m.uc.mem_write(RUNTIME + index * 12, struct.pack("<3I", int(not missing), 0, 1))
        table_before = bytes(m.uc.mem_read(RUNTIME, 14 * 12))
        result = m.run(0x0802a374, boundary="returned")
        assert m.word(BITMAP) == expected
        assert bytes(m.uc.mem_read(RUNTIME, 14 * 12)) == table_before
        rows.append({"case": "resource_bitmap", "missing_bits_fixture": bitmap_fixture,
                     "bitmap_raw": m.word(BITMAP), "runtime_table_preserved": True, **result})
    m = Machine(code)
    for index in range(14):
        m.uc.mem_write(RUNTIME + index * 12, struct.pack("<3I", 1, 0, 0))
    result = m.run(0x0802a374, boundary="returned")
    assert m.word(BITMAP) == 0x3fff
    rows.append({"case": "resource_bitmap_missing_pointer", "bitmap_raw": m.word(BITMAP), **result})

    # Every recovered catalog name reaches its actual first erase destination.
    for entry in layout:
        m = Machine(code)
        result = m.consume(name=entry["name"])
        assert result["stopped_boundary"] == "first_resource_sector_erase_excluded"
        assert result["boundary_arguments"] == {"destination_raw": entry["destination_raw"]}
        assert len(m.spi_reads) == 2 and m.header_live
        rows.append({"case": "consumer_first_destination", **result})
    for count in (0, 1, 300, 301):
        m = Machine(code)
        result = m.consume(name="ss_hour", count=count)
        expected = "parameter_flash_operation_excluded" if count in (0, 301) else "first_resource_sector_erase_excluded"
        assert result["stopped_boundary"] == expected
        if count in (0, 301):
            assert result["consumer_error_accumulator_raw"] == 8 and not m.header_live
        rows.append({"case": "consumer_count_gate", **result})
    for name in ("ss_hour", "unknown", "pps_lcd", "pps_lcd_res"):
        m = Machine(code)
        result = m.consume(name=name, same_version=True)
        assert result["stopped_boundary"] == "parameter_flash_operation_excluded"
        assert len(m.spi_reads) == 1 and not m.header_live
        rows.append({"case": "consumer_skips_unchanged_or_unknown", **result})
    for length in (155644, 155645):
        m = Machine(code)
        result = m.consume(name="ss_hour", declared_length=length)
        assert result["stopped_boundary"] == (
            "first_resource_sector_erase_excluded" if length == 155644 else "parameter_flash_operation_excluded")
        if length == 155645:
            assert result["consumer_error_accumulator_raw"] == 8 and not m.header_live
        rows.append({"case": "consumer_size_gate_supplied_header", **result})
    for marker in (0, 0xffffffff, 0xa5a5a5a4):
        m = Machine(code)
        m.seed_parameter((marker, 0, 0x01000906, 0x19011d01, 0x01020304))
        result = m.run(CONSUMER, boundary="marker_not_pending_branch")
        assert not m.spi_reads and not m.header_live
        rows.append({"case": "nonpending_marker", "marker_fixture": marker, **result})
    return {"model": "A1763", "lcd_manifest_version": "0.1.9.6", "lcd_sha256": IMAGE_SHA256,
            "application_sha256": CODE_SHA256, "cases": len(rows), "results": rows,
            "recovered_scheduler_metadata": events, "negative_guards": negative_guards(code),
            "public_resource_prefix_metadata": {"bytes": resource_bytes, "sha256": resource_sha,
                "first_word_raw": 0x190a1801, "compiled_expected_word_raw": 0x190a1801,
                "runtime_version_page_is_host_seeded": True, "flash_destination_verified": False},
            "instruction_limit_per_entry": LIMIT,
            "maximum_entry_instructions": max(row["instructions"] for row in rows),
            "physical_timer_units_verified": False, "resource_application_verified": False,
            "physical_flash_verified": False, "station_commands_sent": 0, "limits": __doc__.strip()}


def negative_guards(code: bytes) -> list[str]:
    rejected = []
    for name, action in (
        ("boot_excluded", lambda m: m.step(m.uc, 0x08006144, 2, None)),
        ("reset_instruction_excluded", lambda m: m.step(m.uc, 0x08006f80, 2, None)),
        ("real_flash_body_excluded", lambda m: m.step(m.uc, 0x08013f86, 2, None)),
        ("real_spi_write_excluded", lambda m: m.step(m.uc, 0x08010654, 2, None)),
        ("mmio_read_excluded", lambda m: m.read_guard(None, 0, 0xe000ed0c, 4, 0, None)),
        ("mmio_write_excluded", lambda m: m.write_guard(None, 0, 0x40022004, 4, 0, None)),
        ("catalog_write_after_layout_excluded", lambda m: m.write_guard(None, 0, CATALOG + 4, 4, 0, None)),
        ("event_callback_write_excluded", lambda m: m.write_guard(None, 0, EVENTS + 12, 4, 0, None)),
        ("unbounded_name_excluded", lambda m: m.cstring(0x2001a000)),
        ("marker_overread_excluded", lambda m: m.read_guard(None, 0, MARKER_FLASH + 20, 1, 0, None)),
    ):
        try:
            action(Machine(code))
        except (AssertionError, RuntimeError):
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
        p = args.output_dir / f"lcd-pending-worker-{suffix}.json"
        p.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
        p.chmod(0o600)
    print(json.dumps({"synthetic_cases": result["cases"], "negative_guards": len(result["negative_guards"]),
                      "station_commands_sent": 0}))


if __name__ == "__main__":
    main()
