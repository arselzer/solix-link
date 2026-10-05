#!/usr/bin/env python3
"""Bounded LCD resource catalog recovery, name policy and pending-marker replay.

Actual selected initialized-data decompression, MAIN ingress/CRC, descriptor
iteration, strcmp/name/size branches, mapping checks, cleanup bookkeeping,
persistent marker serialization and replies execute. Host substitutes provide
heap, SPI/storage, memory operations, logging,
UI/refresh and optional flash erase/program delivery. Firmware boot, real SPI/MMIO,
flash/file writes, resource application and physical stations remain excluded.
Large size fixtures explicitly alter a header after a bounded CRC checkpoint;
they prove selected downstream size branches, not full validation of large data.
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
                               UC_ARM_REG_R3, UC_ARM_REG_SP,
                               UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_PRIMASK)

from emulate_lcd_asset_status import BASE, STACK, STOP, Machine as StatusMachine
from emulate_lcd_asset_transfer import (
    IMAGE_SHA256, CODE_SHA256, STATE, RESOURCE_HEADER, SPI_CONTEXT, STAGING_BASE,
    STAGING_BYTES, Machine as TransferMachine, checked_image, crc16,
)

RW_SOURCE, RW_DEST, RW_BYTES = 0x0803a068, 0x20000000, 0x5f0
CATALOG, CATALOG_BYTES = 0x2000053c, 14 * 12
HEAP_OWNER, HEAP_USED = 0x20019e00, 0x200148c4
MARKER_FLASH = 0x08005000
MARKER_SEED = struct.pack("<5I", 0, 1, 2, 3, 4)
NAME_ALLOW = ((0x08006228, 0x08006242), (0x0802d9c6, 0x0802da1e),
              (0x0802da38, 0x0802da98),
              (0x0802dbf8, 0x0802dd5a), (0x0802df66, 0x0802df8e),
              (0x0802e108, 0x0802e17a), (0x0802e0f0, 0x0802e108))
MAPPING_ALLOW = ((0x0802da98, 0x0802dbf8), (0x0802def6, 0x0802defa),
                 (0x0802dfba, 0x0802dfe8), (0x0802e03c, 0x0802e0f0))
MARKER_ALLOW = ((0x0802e3e4, 0x0802e422),)


class CopyMachine(StatusMachine):
    def __init__(self, code: bytes) -> None:
        super().__init__(code)
        self.source_reads = set()
        self.uc.hook_add(unicorn.UC_HOOK_MEM_READ, self.read_guard)

    def read_guard(self, uc, access, address, size, value, data) -> None:
        assert any(lo <= address < address + size <= hi for lo, hi in (
            (RW_SOURCE, 0x0803a210), (RW_DEST, RW_DEST + RW_BYTES), (STACK - 0x1000, STACK)))
        if RW_SOURCE <= address < 0x0803a210:
            self.source_reads.update(range(address, address + size))

    def write_guard(self, uc, access, address, size, value, data) -> None:
        assert any(lo <= address < address + size <= hi for lo, hi in (
            (RW_DEST, RW_DEST + RW_BYTES), (STACK - 0x1000, STACK)))

    def step(self, uc, address, size, data) -> None:
        self.instructions += 1
        if address == STOP:
            self.stopped = True
            uc.emu_stop()
        elif not 0x08006cb0 <= address < 0x08006d06:
            raise RuntimeError(f"Unexpected LCD copy instruction {address:08x}")


def host_unpack_rw(code: bytes) -> tuple[bytes, int]:
    """Independent host reconstruction of the selected initialized-data stream."""
    stream = code[RW_SOURCE - BASE:0x0803a210 - BASE]
    output = bytearray()
    cursor = 0

    def take() -> int:
        nonlocal cursor
        assert cursor < len(stream)
        value = stream[cursor]
        cursor += 1
        return value

    while len(output) < RW_BYTES:
        control = take()
        literals = control & 7 or take()
        run = control >> 4 or take()
        assert literals >= 1
        for _ in range(literals - 1):
            output.append(take())
        if control & 8:
            distance = take()
            assert 0 < distance <= len(output)
            for _ in range(run + 2):
                output.append(output[-distance])
        else:
            output.extend(bytes(run))
        assert len(output) <= RW_BYTES
    return bytes(output), cursor


def recover_catalog(code: bytes) -> tuple[bytes, dict]:
    record = struct.unpack_from("<4I", code, 0x0803a044 - BASE)
    assert record == (RW_SOURCE, RW_DEST, RW_BYTES, 0x08006cb0)
    m = CopyMachine(code)
    for register, value in ((UC_ARM_REG_R0, RW_SOURCE), (UC_ARM_REG_R1, RW_DEST),
                            (UC_ARM_REG_R2, RW_BYTES), (UC_ARM_REG_SP, STACK),
                            (UC_ARM_REG_LR, STOP | 1)):
        m.uc.reg_write(register, value)
    m.uc.emu_start(0x08006cb1, 0, count=30000)
    assert m.stopped, "LCD copy instruction bound exceeded"
    rw = bytes(m.uc.mem_read(RW_DEST, RW_BYTES))
    host_rw, consumed = host_unpack_rw(code)
    assert rw == host_rw
    assert m.source_reads == set(range(RW_SOURCE, RW_SOURCE + consumed))
    entries = []
    for index in range(14):
        address, middle, capacity = struct.unpack_from("<3I", rw, CATALOG - RW_DEST + index * 12)
        assert BASE <= address < BASE + len(code)
        name = code[address - BASE:address - BASE + 64].split(b"\0", 1)[0]
        assert len(name) < 64
        entries.append({"index": index, "name": name.decode("ascii"),
                        "name_address": f"{address:08x}", "middle_word_raw": middle,
                        "capacity_word_raw": capacity})
    return rw[CATALOG - RW_DEST:CATALOG - RW_DEST + CATALOG_BYTES], {
        "case": "initialized_data_recovery", "instructions": m.instructions,
        "instruction_limit_per_entry": 30000, "scatter_record_address": "0803a044",
        "source_address": f"{RW_SOURCE:08x}", "destination_address": f"{RW_DEST:08x}",
        "decoded_bytes": RW_BYTES, "compressed_bytes_consumed": consumed,
        "initialized_data_sha256": hashlib.sha256(rw).hexdigest(),
        "host_reconstruction_matches": True, "entries": entries, "firmware_boot_executed": False}


class Machine(TransferMachine):
    def __init__(self, code: bytes, catalog: bytes, *, deliver_marker: bool = False,
                 pause_after_crc: bool = False, allow_mapping: bool = True) -> None:
        super().__init__(code, allow_checksum=True, allow_resource_validation=True)
        assert len(catalog) == CATALOG_BYTES
        self.uc.mem_write(CATALOG, catalog)
        self.catalog_before = catalog
        self.deliver_marker = deliver_marker
        self.pause_after_crc = pause_after_crc
        self.allow_mapping = allow_mapping
        self.allocations, self.frees, self.marker_substitutes = [], [], []
        self.flash_service_substitutes = []
        self.crc_comparisons, self.mapping_matches = [], []
        self.header_live = False
        self.refresh_substitutes = 0
        self.resume_context = None
        self.uc.mem_write(0x200148c0, struct.pack("<I", HEAP_OWNER))
        # Synthetic old parameter words; this page is absent from the input app.
        self.uc.mem_protect(MARKER_FLASH, 0x1000, unicorn.UC_PROT_ALL)
        self.uc.mem_write(MARKER_FLASH, MARKER_SEED)
        self.uc.mem_protect(MARKER_FLASH, 0x1000, unicorn.UC_PROT_READ | unicorn.UC_PROT_EXEC)

    def write_guard(self, uc, access, address, size, value, data) -> None:
        if any(lo <= address < address + size <= hi for lo, hi in (
                (HEAP_USED, HEAP_USED + 4), (0x200002b8, 0x200002bc),
                (0x200002c4, 0x200002c8), (0x20000400, 0x20000404))):
            return
        super().write_guard(uc, access, address, size, value, data)

    def stop(self, name: str) -> None:
        self.instructions += 1
        self.boundary = name
        self.stopped = True
        self.uc.emu_stop()

    def cstring(self, address: int) -> bytes:
        value = bytearray()
        for offset in range(64):
            assert (BASE <= address + offset < BASE + 214016 or
                    RESOURCE_HEADER <= address + offset < RESOURCE_HEADER + 1024)
            byte = self.uc.mem_read(address + offset, 1)[0]
            if not byte:
                return bytes(value)
            value.append(byte)
        raise AssertionError("Unterminated bounded name")

    def step(self, uc, address, size, data) -> None:
        r0, r1, r2, r3 = [uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1,
                                                UC_ARM_REG_R2, UC_ARM_REG_R3)]
        if address == 0x0802d9c6 and self.pause_after_crc:
            self.resume_context = uc.context_save()
            self.stop("post_crc_checkpoint_for_size_fixture")
        elif address == 0x0802da98 and not self.allow_mapping:
            self.stop("ordinary_asset_mapping_excluded")
        elif address == 0x0802080c:
            self.instructions += 1
            assert r0 == 1024 and not self.header_live and len(self.allocations) < 2
            self.header_live = True
            self.allocations.append({"bytes": r0})
            uc.mem_write(RESOURCE_HEADER - 4, struct.pack("<I", 1028))
            uc.mem_write(HEAP_USED, struct.pack("<I", 1028))
            self.back(RESOURCE_HEADER)
        elif address == 0x0802606c:
            self.instructions += 1
            assert self.header_live and (r0, r1) == (HEAP_OWNER, RESOURCE_HEADER)
            self.header_live = False
            self.frees.append({"bytes_from_synthetic_header": 1028})
            self.back()
        elif address == 0x08006226:
            self.instructions += 1
            self.cstring(r0)
            self.cstring(r1)
            # Validate bounded pointers, then execute the actual byte comparison.
        elif address == 0x080104e2 and r3 == 4:
            self.instructions += 1
            offset = r1 - STAGING_BASE
            assert r0 == SPI_CONTEXT and 0 <= offset < offset + r3 <= STAGING_BYTES
            self.write_guard(uc, 0, r2, r3, 0, None)
            uc.mem_write(r2, bytes(self.storage[offset:offset + r3]))
            self.back()
        elif address == 0x0802a374:
            self.instructions += 1
            self.refresh_substitutes += 1
            self.back()  # Renderer/resource refresh is deliberately excluded.
        elif address == 0x0803028c:
            self.instructions += 1
            assert (r0, r1) in ((1, 1), (11, 1), (13, 0), (13, 1))
            self.ui_signal_substitutes.append({"signal_argument": r0, "value_argument": r1})
            self.back()
        elif address == 0x0802e3e4:
            if not self.deliver_marker:
                self.stop("persistent_flash_marker_excluded")
            else:
                self.instructions += 1
                assert r0 == 0xa5a5a5a5
                self.marker_substitutes.append({"marker_argument_raw": r0})
                # Execute this helper; only its erase/program services are substituted.
        elif address == 0x08013f84 and self.deliver_marker:
            self.instructions += 1
            assert (r0, r1) == (MARKER_FLASH, 20)
            self.flash_service_substitutes.append({"operation": "erase", "address_raw": r0, "bytes": r1})
            self.back()
        elif address == 0x08014140 and self.deliver_marker:
            self.instructions += 1
            assert (r0, r1) == (MARKER_FLASH, 20)
            assert STACK - 0x1000 <= r2 < r2 + r1 <= STACK
            value = bytes(uc.mem_read(r2, r1))
            assert value == struct.pack("<I", 0xa5a5a5a5) + MARKER_SEED[4:]
            self.flash_service_substitutes.append({"operation": "program", "address_raw": r0,
                                                   "bytes": r1, "data_sha256": hashlib.sha256(value).hexdigest(),
                                                   "marker_word_raw": int.from_bytes(value[:4], "little"),
                                                   "following_words_preserved": True})
            self.back()  # No host/guest write to the parameter page occurs.
        elif any(lo <= address < hi for lo, hi in NAME_ALLOW):
            self.instructions += 1
        elif self.allow_mapping and any(lo <= address < hi for lo, hi in MAPPING_ALLOW):
            self.instructions += 1
            if address == 0x0802db4a:
                index = uc.reg_read(unicorn.arm_const.UC_ARM_REG_R11)
                self.mapping_matches.append({"catalog_index": index})
        elif self.deliver_marker and any(lo <= address < hi for lo, hi in MARKER_ALLOW):
            self.instructions += 1
        else:
            super().step(uc, address, size, data)
            if address == 0x0802d9c0:
                self.crc_comparisons.append(dict(self.resource_crc_comparison))

    def result(self, reply: dict) -> dict:
        assert bytes(self.uc.mem_read(CATALOG, CATALOG_BYTES)) == self.catalog_before
        assert bytes(self.uc.mem_read(MARKER_FLASH, 20)) == MARKER_SEED
        return {**reply, "allocations": self.allocations, "frees": self.frees,
                "synthetic_heap_used_after": int.from_bytes(self.uc.mem_read(HEAP_USED, 4), "little"),
                "header_live_after": self.header_live, "crc_comparisons": self.crc_comparisons,
                "mapping_matches": self.mapping_matches, "refresh_substitutes": self.refresh_substitutes,
                "ui_signal_substitutes": self.ui_signal_substitutes,
                "marker_substitutes": self.marker_substitutes,
                "flash_service_substitutes": self.flash_service_substitutes,
                "synthetic_parameter_page_preserved": True,
                "pending_counter_raw": int.from_bytes(self.uc.mem_read(0x200002b8, 4), "little"),
                "pending_context_pointer_set": int.from_bytes(self.uc.mem_read(0x20000400, 4), "little") == STATE,
                "maximum_entry_instructions": self.maximum_entry_instructions,
                "catalog_preserved": True, "resource_application_verified": False}

    def resume_size_fixture(self, declared_length: int) -> dict:
        assert self.resume_context is not None and 1 <= declared_length <= 0xffffffff
        self.pause_after_crc = False
        # Both header copies are explicit post-CRC fixture modifications.
        self.uc.mem_write(RESOURCE_HEADER + 16, struct.pack("<I", declared_length))
        struct.pack_into("<I", self.storage, 16, declared_length)
        self.uc.context_restore(self.resume_context)
        self.stopped, self.boundary = False, None
        before, sent_before = self.instructions, len(self.transmitted)
        self.uc.emu_start(self.uc.reg_read(UC_ARM_REG_PC) | 1, 0, count=100000)
        assert self.stopped and self.uc.reg_read(UC_ARM_REG_PRIMASK) == 0
        used = self.instructions - before
        self.maximum_entry_instructions = max(self.maximum_entry_instructions, used)
        return self.result({"host_modified_post_crc_length": declared_length,
                            "large_payload_crc_verified": False,
                            "stopped_boundary": self.boundary, "instructions": used,
                            "responses": [list(f[10:14]) for f in self.transmitted[sent_before:]],
                            "state_flag": self.uc.mem_read(STATE, 1)[0],
                            "state_mode": self.uc.mem_read(STATE + 1, 1)[0]})


def package(entries: list[tuple[str, bytes, bool]]) -> bytes:
    assert len(entries) <= 10
    header = bytearray(b"\xff" * 1024)
    payload = bytearray()
    for index, (name, data, valid) in enumerate(entries):
        encoded = name.encode("ascii")
        assert 0 < len(encoded) < 16 and 0 < len(data) <= 128
        offset = 1024 + len(payload)
        struct.pack_into("<4I16s", header, 12 + index * 32, offset, len(data),
                         crc16(data) ^ int(not valid), 0, encoded)
        payload.extend(data)
    assert 1024 + len(payload) <= STAGING_BYTES
    return bytes(header) + bytes(payload)


def upload(m: Machine, data: bytes) -> None:
    m.start(blocks=(len(data) + 1023) // 1024)
    for index, offset in enumerate(range(0, len(data), 1024)):
        m.chunk(index, data[offset:offset + 1024])


def assert_terminal(result: dict, *, special: bool = False, delivered: bool = False,
                    error: int | None = None) -> None:
    assert not result["header_live_after"] and result["synthetic_heap_used_after"] == 0
    assert len(result["allocations"]) == len(result["frees"])
    if error is None:
        assert result["state_flag"] == 0 and result["state_mode"] == 2
        assert result["responses"] == ([[0x14, 0, 0xaa, 0xee]] if delivered else [])
        assert result["stopped_boundary"] == (None if delivered else "persistent_flash_marker_excluded")
        assert result["pending_counter_raw"] == (3000 if special else 0)
        assert result["pending_context_pointer_set"] == (not special)
        assert len(result["allocations"]) == (1 if special else 2)
        assert result["refresh_substitutes"] == (0 if special else 1)
        assert result["marker_substitutes"] == ([{"marker_argument_raw": 0xa5a5a5a5}] if delivered else [])
        assert [s["operation"] for s in result["flash_service_substitutes"]] == (["erase", "program"] if delivered else [])
        expected_signal = [{"signal_argument": 1 if special else 11, "value_argument": 1}]
        if delivered:
            expected_signal.append({"signal_argument": 13, "value_argument": 0})
        assert result["ui_signal_substitutes"] == expected_signal
    else:
        assert result["state_flag"] == 1 and result["state_mode"] == 0
        assert result["responses"] == [[0x14, 0, error, 0]] and result["stopped_boundary"] is None
        assert not result["marker_substitutes"] and result["pending_counter_raw"] == 0
        assert not result["flash_service_substitutes"]
        assert not result["pending_context_pointer_set"]
        assert len(result["allocations"]) == (2 if error == 8 else 1)
        assert result["refresh_substitutes"] == int(error == 8)
        assert result["ui_signal_substitutes"] == [{"signal_argument": 13, "value_argument": 0}]


def suite(image: bytes) -> dict:
    code = checked_image(image)
    catalog, copy_result = recover_catalog(code)
    rows = [copy_result]
    for name in ("pps_lcd", "pps_lcd_res", "ss_hour", "unknown", "pps_lcd_extra", "PPS_LCD"):
        for marker_delivery in (False, True):
            m = Machine(code, catalog, deliver_marker=marker_delivery)
            upload(m, package([(name, struct.pack("<I", 1), True)]))
            reply = m.result(m.deliver(0x14, bytes(2)))
            assert_terminal(reply, special=name in ("pps_lcd", "pps_lcd_res"), delivered=marker_delivery)
            assert reply["mapping_matches"] == ([{"catalog_index": 1}] if name == "ss_hour" else [])
            assert len(reply["crc_comparisons"]) == 1 and reply["crc_comparisons"][0]["matched"]
            if marker_delivery:
                poll = m.deliver(0x16, bytes(2))
                assert poll["responses"] == [[0x16, 0, 0, 50]]
                reply["completion_poll_after_substituted_marker"] = poll["responses"]
            rows.append({"case": "name_route", "name_fixture": name,
                         "persistent_marker_delivery_substituted": marker_delivery, **reply})
    for name, length in (("pps_lcd", 0x3a000), ("pps_lcd", 0x3a001),
                         ("pps_lcd_res", 0xc0000), ("pps_lcd_res", 0xc0001),
                         ("ss_hour", 155644), ("ss_hour", 155645)):
        m = Machine(code, catalog, pause_after_crc=True)
        upload(m, package([(name, struct.pack("<I", 1), True)]))
        checkpoint = m.deliver(0x14, bytes(2))
        assert checkpoint["stopped_boundary"] == "post_crc_checkpoint_for_size_fixture"
        result = m.resume_size_fixture(length)
        special = name in ("pps_lcd", "pps_lcd_res")
        limit = {"pps_lcd": 0x3a000, "pps_lcd_res": 0xc0000, "ss_hour": 155644}[name]
        assert_terminal(result, special=special, error=(3 if special else 8) if length > limit else None)
        assert result["large_payload_crc_verified"] is False
        rows.append({"case": "post_crc_name_size_boundary", "name_fixture": name, **result})
    for entries in ([], [("pps_lcd", b"x", True), ("pps_lcd_res", b"y", True)],
                    [("unknown", b"a", True), ("pps_lcd", b"b", True)],
                    [("pps_lcd", b"a", True), ("unknown", b"b", False)]):
        m = Machine(code, catalog)
        upload(m, package(entries))
        result = m.result(m.deliver(0x14, bytes(2)))
        invalid = any(not entry[2] for entry in entries)
        special = any(entry[0] in ("pps_lcd", "pps_lcd_res") for entry in entries)
        assert_terminal(result, special=special, error=5 if invalid else None)
        assert len(result["crc_comparisons"]) == len(entries)
        assert [c["matched"] for c in result["crc_comparisons"]] == [e[2] for e in entries]
        rows.append({"case": "descriptor_iteration", "entries_fixture": len(entries),
                     "names_fixture": [e[0] for e in entries], **result})
    for count in (0, 1, 300, 301):
        m = Machine(code, catalog)
        upload(m, package([("ss_hour", struct.pack("<I", count), True)]))
        result = m.result(m.deliver(0x14, bytes(2)))
        assert_terminal(result, error=8 if count in (0, 301) else None)
        assert result["mapping_matches"] == [{"catalog_index": 1}]
        rows.append({"case": "mapped_payload_count", "count_fixture": count, **result})
    for entry in copy_result["entries"]:
        if entry["name"] == "ss_hour":
            continue  # Already exercised above.
        m = Machine(code, catalog)
        upload(m, package([(entry["name"], struct.pack("<I", 1), True)]))
        result = m.result(m.deliver(0x14, bytes(2)))
        assert_terminal(result)
        assert result["mapping_matches"] == [{"catalog_index": entry["index"]}]
        rows.append({"case": "catalog_name_mapping", "name_fixture": entry["name"], **result})
    for declared_length in (0, 1026):
        m = Machine(code, catalog)
        data = bytearray(package([("pps_lcd", b"x", True)]))
        struct.pack_into("<I", data, 16, declared_length)
        upload(m, bytes(data))
        result = m.result(m.deliver(0x14, bytes(2)))
        assert_terminal(result, error=3)
        assert not result["crc_comparisons"]
        rows.append({"case": "entry_length_rejected_before_crc", "declared_length_fixture": declared_length, **result})
    m = Machine(code, catalog, deliver_marker=True)
    upload(m, package([]))
    result = m.result(m.deliver(0x14, bytes(2)))
    assert_terminal(result, delivered=True)
    assert not result["crc_comparisons"] and not result["mapping_matches"]
    rows.append({"case": "empty_catalog_after_substituted_marker", **result})
    return {"model": "A1763", "lcd_manifest_version": "0.1.9.6", "lcd_sha256": IMAGE_SHA256,
            "application_sha256": CODE_SHA256, "cases": len(rows), "results": rows,
            "negative_guards": negative_guards(code, catalog), "station_commands_sent": 0,
            "resource_application_verified": False, "physical_flash_verified": False,
            "maximum_entry_instructions": max(row.get("maximum_entry_instructions", row["instructions"]) for row in rows),
            "instruction_limit_per_entry": 100000, "limits": __doc__.strip()}


def negative_guards(code: bytes, catalog: bytes) -> list[str]:
    rejected = []
    for name, action in (
        ("boot_instruction_excluded", lambda m: m.step(m.uc, 0x08006144, 2, None)),
        ("real_flash_instruction_excluded", lambda m: m.step(m.uc, 0x08013f84, 2, None)),
        ("catalog_write_excluded", lambda m: m.write_guard(m.uc, 0, CATALOG, 4, 0, None)),
        ("mmio_write_excluded", lambda m: m.write_guard(m.uc, 0, 0x40022004, 4, 0, None)),
        ("unbounded_name_pointer_excluded", lambda m: m.cstring(0x2001a000)),
        ("size_fixture_without_checkpoint_excluded", lambda m: m.resume_size_fixture(10)),
        ("real_refresh_instruction_excluded", lambda m: m.step(m.uc, 0x0802a376, 2, None)),
        ("copy_source_extent_guard", lambda m: CopyMachine(code).read_guard(None, 0, 0x0803a210, 1, 0, None)),
    ):
        try:
            action(Machine(code, catalog))
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
        p = args.output_dir / f"lcd-resource-selection-{suffix}.json"
        p.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
        p.chmod(0o600)
    print(json.dumps({"synthetic_cases": result["cases"], "negative_guards": len(result["negative_guards"]),
                      "station_commands_sent": 0}))


if __name__ == "__main__":
    main()
