#!/usr/bin/env python3
"""Bounded A1763 LCD transfer descriptor and chunk/page construction replay.

Actual selected MAIN ingress/CRC, descriptor validation/copy, chunk ordering,
page splitting, final byte-sum/first-entry CRC checking and reply construction
execute on synthetic RAM. Page programming, SPI command/address/read, libc, logs,
UI signals and UART send are substitutes. Preparation/erase, resource name/commit
handling, progress math, real SPI, boot and stations are excluded. SPI channel 255
and its control register are synthetic RAM fixtures; real channel selection and
MMIO do not execute.
Host storage is a bounded synthetic byte array, not flash or a device filesystem.
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

from emulate_lcd_asset_status import (
    IMAGE_SHA256, CODE_SHA256, CONTEXT, STATE, STACK, STOP,
    Machine as StatusMachine, checked_image, crc16,
)

SPI_CONTEXT = 0x20017c00
SPI_CHANNEL, SPI_REGISTERS = 0x20017d00, 0x20017e00
RESOURCE_HEADER = 0x20019000
INPUT, OUTPUT = 0x20018000, 0x20018800
STAGING_BASE, STAGING_BYTES = 0x00c00000, 4096
ALLOW = ((0x0802d150, 0x0802d198), (0x0802d5be, 0x0802d648),
         (0x0802d73a, 0x0802d7d4), (0x0802dd5a, 0x0802dda4),
         (0x0802df5a, 0x0802df66), (0x0802e290, 0x0802e29c),
         (0x08010654, 0x08010716), (0x08014ee6, 0x08014f28))
CHECKSUM_ALLOW = ((0x0802d3d8, 0x0802d524), (0x0802d7d4, 0x0802d818),
                  (0x0802dc28, 0x0802dc60))
RESOURCE_ALLOW = ((0x0802d818, 0x0802d9c6),)


class Machine(StatusMachine):
    def __init__(self, code: bytes, *, allow_checksum: bool = False,
                 allow_resource_validation: bool = False) -> None:
        super().__init__(code)
        self.storage = bytearray(b"\xff" * STAGING_BYTES)
        self.page_program_substitutes = []
        self.maximum_entry_instructions = 0
        self.allow_checksum = allow_checksum
        self.allow_resource_validation = allow_resource_validation
        assert not allow_resource_validation or allow_checksum
        self.resource_header_allocated = False
        self.resource_header_read_bytes = 0
        self.resource_crc_comparison = None
        self.spi_read_bytes = 0
        self.spi_cursor = None
        self.spi_command_substitutes = []
        self.uc.mem_write(CONTEXT, struct.pack("<I", STATE) + bytes(0x7c - 4))
        self.uc.mem_write(CONTEXT + 0x6c, struct.pack("<II", INPUT, OUTPUT))
        self.uc.mem_write(STATE, struct.pack("<BB2xIII", 0, 0, 0, 0, CONTEXT) + bytes(7))
        self.uc.mem_write(0x2000070c, struct.pack("<I", SPI_CONTEXT))
        self.uc.mem_write(SPI_CONTEXT + 8, struct.pack("<I", SPI_CHANNEL))
        self.uc.mem_write(SPI_CHANNEL + 6, b"\xff")
        self.uc.mem_write(SPI_CHANNEL + 8, struct.pack("<I", SPI_REGISTERS))

    def write_guard(self, uc, access, address, size, value, data) -> None:
        regions = [(STACK - 0x1000, STACK), (STATE, STATE + 12), (STATE + 16, STATE + 23),
                   (OUTPUT, OUTPUT + 136)]
        if self.allow_checksum:
            regions += [(0x20000600, 0x20000601), (SPI_REGISTERS + 128, SPI_REGISTERS + 132)]
        if self.allow_resource_validation:
            regions += [(RESOURCE_HEADER, RESOURCE_HEADER + 1024)]
        assert any(lo <= address < address + size <= hi for lo, hi in regions), (
            f"Unexpected LCD transfer write {address:08x}")

    def step(self, uc, address, size, data) -> None:
        args = [uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1,
                                       UC_ARM_REG_R2, UC_ARM_REG_R3)]
        r0, r1, r2, r3 = args
        if address == 0x0802d236 or (address == 0x0802d3d8 and not self.allow_checksum):
            self.instructions += 1
            self.boundary = "preparation_erase_excluded" if address == 0x0802d236 else "final_resource_commit_excluded"
            self.stopped = True
            uc.emu_stop()
        elif address == 0x0802d818 and self.allow_checksum and not self.allow_resource_validation:
            self.instructions += 1
            self.boundary = "resource_header_validation_excluded"
            self.stopped = True
            uc.emu_stop()
        elif self.allow_resource_validation and address in (0x0802d9c6, 0x0802dd20, 0x0802dbf8, 0x0802dc60):
            self.instructions += 1
            self.boundary = {0x0802d9c6: "first_entry_crc_matched_before_name_or_commit",
                             0x0802dd20: "first_entry_crc_rejected_before_error_reply",
                             0x0802dbf8: "entry_size_rejected_before_error_reply",
                             0x0802dc60: "empty_descriptor_list_before_cleanup"}[address]
            self.stopped = True
            uc.emu_stop()
        elif address == 0x0802080c and self.allow_resource_validation:
            self.instructions += 1
            assert r0 == 1024 and not self.resource_header_allocated
            self.resource_header_allocated = True
            self.back(RESOURCE_HEADER)
        elif address == 0x08006b06 and self.allow_resource_validation:
            self.instructions += 1
            assert 0 < r1 <= 1024
            self.write_guard(uc, 0, r0, r1, 0, None)
            uc.mem_write(r0, bytes(r1))
            self.back(r0)
        elif address == 0x080104e2 and self.allow_resource_validation:
            self.instructions += 1
            assert (r0, r1, r2, r3) == (SPI_CONTEXT, STAGING_BASE, RESOURCE_HEADER, 1024)
            self.write_guard(uc, 0, r2, r3, 0, None)
            uc.mem_write(r2, bytes(self.storage[:1024]))
            self.resource_header_read_bytes += r3
            self.back()
        elif address == 0x080159f8 and self.allow_checksum:
            self.instructions += 1
            assert r0 == SPI_CHANNEL and r2 in (1, 4)
            command = bytes(uc.mem_read(r1, r2))
            assert command in (b"\x6b", b"\xa5" * 4)
            self.spi_command_substitutes.append({"bytes": r2, "sha256": hashlib.sha256(command).hexdigest()})
            self.back()
        elif address == 0x080107f6 and self.allow_checksum:
            self.instructions += 1
            assert r0 == SPI_CONTEXT and STAGING_BASE <= r1 < STAGING_BASE + STAGING_BYTES
            self.spi_cursor = r1 - STAGING_BASE
            self.back()
        elif address == 0x080156dc and self.allow_checksum:
            self.instructions += 1
            assert r0 == SPI_CHANNEL and r2 == 1 and self.spi_cursor is not None
            assert 0 <= self.spi_cursor < STAGING_BYTES
            self.write_guard(uc, 0, r1, 1, 0, None)
            uc.mem_write(r1, bytes(self.storage[self.spi_cursor:self.spi_cursor + 1]))
            self.spi_cursor += 1
            self.spi_read_bytes += 1
            self.back()
        elif address == 0x0801058e:
            self.instructions += 1
            offset = r1 - STAGING_BASE
            assert r0 == SPI_CONTEXT and 0 < r3 <= 256
            assert (r1 & 255) + r3 <= 256
            assert 0 <= offset < offset + r3 <= STAGING_BYTES
            assert INPUT + 8 <= r2 < r2 + r3 <= INPUT + 8 + 1040
            data_bytes = bytes(uc.mem_read(r2, r3))
            self.storage[offset:offset + r3] = data_bytes
            self.page_program_substitutes.append({"address_raw": r1, "bytes": r3,
                                                  "data_sha256": hashlib.sha256(data_bytes).hexdigest()})
            self.back()  # Page driver and all its hardware are excluded.
        elif address == 0x0803028c:
            self.instructions += 1
            assert r0 == 13 and r1 in (0, 1)
            self.ui_signal_substitutes.append({"signal_argument": r0, "value_argument": r1})
            self.back()
        elif address == 0x08015328:
            self.instructions += 1
            assert r0 == 0 and r1 == OUTPUT
            length = int.from_bytes(uc.mem_read(OUTPUT, 4), "little")
            assert length == 16
            frame = bytes(uc.mem_read(OUTPUT + 8, length))
            assert frame[:4] == b"main" and crc16(frame) == 0
            self.transmitted.append(frame)
            self.back()
        elif any(lo <= address < hi for lo, hi in ALLOW):
            self.instructions += 1
        elif self.allow_checksum and any(lo <= address < hi for lo, hi in CHECKSUM_ALLOW):
            self.instructions += 1
        elif self.allow_resource_validation and any(lo <= address < hi for lo, hi in RESOURCE_ALLOW):
            self.instructions += 1
            if address == 0x0802d9c0:
                computed = uc.reg_read(unicorn.arm_const.UC_ARM_REG_R8)
                self.resource_crc_comparison = {"stored_crc_raw": r0, "computed_crc_raw": computed,
                                                "matched": r0 == computed}
        else:
            super().step(uc, address, size, data)

    def deliver(self, point: int, payload: bytes, *, bad_crc: bool = False) -> dict:
        assert point in (0x10, 0x12, 0x13, 0x14, 0x16)
        assert len(payload) <= 1026
        assert point != 0x10 or len(payload) == 7
        assert point != 0x13 or len(payload) >= 3
        frame = b"MAIN" + struct.pack("<4H", 16, 5, len(payload) + 2, point) + payload
        frame += struct.pack("<H", crc16(frame))
        if bad_crc:
            frame = frame[:-1] + bytes((frame[-1] ^ 1,))
        self.uc.mem_write(INPUT, struct.pack("<II", len(frame), 0) + frame)
        self.uc.mem_write(OUTPUT, struct.pack("<II", 0, 128) + bytes(128))
        self.uc.reg_write(UC_ARM_REG_R0, CONTEXT)
        self.uc.reg_write(UC_ARM_REG_SP, STACK)
        self.uc.reg_write(UC_ARM_REG_LR, STOP | 1)
        self.uc.reg_write(UC_ARM_REG_PRIMASK, 0)
        self.stopped, self.boundary = False, None
        before = self.instructions
        sent_before = len(self.transmitted)
        pages_before = len(self.page_program_substitutes)
        read_before = self.spi_read_bytes
        commands_before = len(self.spi_command_substitutes)
        header_read_before = self.resource_header_read_bytes
        self.uc.emu_start(0x0802d039, 0, count=100000)
        assert self.stopped, "LCD instruction bound exceeded"
        assert self.uc.reg_read(UC_ARM_REG_PRIMASK) == 0
        assert int.from_bytes(self.uc.mem_read(STATE + 12, 4), "little") == CONTEXT
        used = self.instructions - before
        self.maximum_entry_instructions = max(self.maximum_entry_instructions, used)
        state = bytes(self.uc.mem_read(STATE, 23))
        return {"input_point_raw": point, "input_frame_bytes": len(frame),
                "input_frame_sha256": hashlib.sha256(frame).hexdigest(),
                "responses": [list(f[10:14]) for f in self.transmitted[sent_before:]],
                "response_frame_sha256": [hashlib.sha256(f).hexdigest() for f in self.transmitted[sent_before:]],
                "state_flag": state[0], "state_mode": state[1],
                "staged_bytes_raw": int.from_bytes(state[4:8], "little"),
                "last_block_raw": int.from_bytes(state[8:12], "little"),
                "retained_descriptor_sha256": hashlib.sha256(state[16:23]).hexdigest(),
                "page_program_substitutes": self.page_program_substitutes[pages_before:],
                "staged_storage_sha256": hashlib.sha256(self.storage).hexdigest(),
                "spi_read_substitute_bytes": self.spi_read_bytes - read_before,
                "resource_header_read_substitute_bytes": self.resource_header_read_bytes - header_read_before,
                "spi_command_substitutes": self.spi_command_substitutes[commands_before:],
                "first_entry_crc_comparison": self.resource_crc_comparison,
                "stopped_boundary": self.boundary, "instructions": used,
                "primask_restored": True, "physical_storage_verified": False}

    def start(self, *, marker: int = 1, blocks: int = 0x98, argument: int = 0) -> dict:
        assert 0 <= marker <= 255 and 0 <= blocks <= 65535 and 0 <= argument <= 0xffffffff
        return self.deliver(0x10, struct.pack("<BHI", marker, blocks, argument))

    def chunk(self, block: int, data: bytes, *, bad_crc: bool = False) -> dict:
        assert 0 <= block <= 65535 and 0 < len(data) <= 1024
        return self.deliver(0x13, struct.pack("<H", block) + data, bad_crc=bad_crc)


def fixture_bytes(n: int, seed: int = 0) -> bytes:
    return bytes((i * 17 + seed * 29 + 7) & 255 for i in range(n))


def public_container_metadata(image: bytes) -> dict:
    """Host inspection of the hash-pinned public input; no guest execution."""
    entries = []
    for p in range(12, 1024 - 31, 32):
        offset, length, stored_crc, _ = struct.unpack_from("<4I", image, p)
        if offset == 0xffffffff:
            break
        assert 0 <= offset < offset + length <= len(image)
        payload = image[offset:offset + length]
        computed_crc = crc16(payload)
        assert computed_crc == stored_crc
        entries.append({"descriptor_file_offset": p, "payload_offset": offset,
                        "payload_bytes": length, "stored_crc_raw": stored_crc,
                        "host_computed_crc16_modbus": computed_crc,
                        "version_field_bytes": list(image[p + 12:p + 16]),
                        "name": image[p + 16:p + 32].split(b"\0", 1)[0].decode("ascii"),
                        "payload_sha256": hashlib.sha256(payload).hexdigest()})
    assert [e["name"] for e in entries] == ["pps_lcd", "pps_lcd_res"]
    return {"evidence_kind": "host inspection of public container", "entries": entries,
            "receiver_commit_verified": False}


def suite(image: bytes) -> dict:
    code = checked_image(image)
    rows = []
    for marker, blocks, argument in ((1, 0x98, 0), (0, 1, 123), (1, 4096, 0xffffffff),
                                     (1, 0, 0), (1, 4097, 0), (2, 0x98, 0), (255, 0x98, 0)):
        m = Machine(code)
        result = m.start(marker=marker, blocks=blocks, argument=argument)
        accepted = marker < 2 and 1 <= blocks <= 4096
        status = 0xeeaa if accepted else 2 if marker >= 2 else 3
        assert result["responses"] == [[0x10, 0, status & 255, status >> 8]]
        assert result["state_mode"] == int(accepted)
        assert result["last_block_raw"] == (0xffffffff if accepted else 0)
        if accepted:
            assert bytes(m.uc.mem_read(STATE + 16, 7)) == struct.pack("<BHI", marker, blocks, argument)
        assert m.ui_signal_substitutes == ([{"signal_argument": 13, "value_argument": 1}]
                                            if accepted and marker == 0 else [])
        assert not m.page_program_substitutes
        rows.append({"case": "start", "marker_fixture": marker, "blocks_fixture": blocks,
                     "argument_fixture": argument, "ui_signal_substitutes": m.ui_signal_substitutes, **result})
    for length in (1, 255, 256, 257, 1024):
        m = Machine(code)
        m.start()
        data = fixture_bytes(length)
        result = m.chunk(0, data)
        assert result["responses"] == [[0x13, 0, 0xaa, 0xee]]
        assert result["staged_bytes_raw"] == length and result["last_block_raw"] == 0
        assert m.storage == data + b"\xff" * (STAGING_BYTES - length)
        assert [p["bytes"] for p in m.page_program_substitutes] == [256] * (length // 256) + ([length % 256] if length % 256 else [])
        rows.append({"case": "first_chunk", "data_bytes": length, **result})
    for name, block in (("duplicate_block", 0), ("next_block", 1), ("skipped_block", 2)):
        m = Machine(code)
        m.start()
        first, second = fixture_bytes(1024), fixture_bytes(1024, 1)
        m.chunk(0, first)
        result = m.chunk(block, second)
        duplicate = block == 0
        assert result["responses"] == [[0x13, 0, 0xaa, 0xee]]
        expected = first if duplicate else first + second
        assert m.storage == expected + b"\xff" * (STAGING_BYTES - len(expected))
        assert result["staged_bytes_raw"] == len(expected)
        assert len(result["page_program_substitutes"]) == (0 if duplicate else 4)
        rows.append({"case": name, **result})
    m = Machine(code)
    m.start()
    m.chunk(2, fixture_bytes(256))
    before = bytes(m.storage)
    result = m.chunk(1, fixture_bytes(256, 1))
    assert result["responses"] == [[0x13, 0, 1, 0]] and bytes(m.storage) == before
    assert result["last_block_raw"] == 2 and not result["page_program_substitutes"]
    rows.append({"case": "older_block_rejected", **result})
    m = Machine(code)
    m.start()
    first, second = fixture_bytes(1), fixture_bytes(257, 1)
    m.chunk(0, first)
    result = m.chunk(1, second)
    assert [p["bytes"] for p in result["page_program_substitutes"]] == [255, 2]
    assert m.storage == first + second + b"\xff" * (STAGING_BYTES - 258)
    rows.append({"case": "unaligned_page_split", **result})
    m = Machine(code)
    m.start()
    result = m.chunk(0, fixture_bytes(1024), bad_crc=True)
    assert not result["responses"] and not result["page_program_substitutes"]
    assert result["staged_bytes_raw"] == 0 and result["last_block_raw"] == 0xffffffff
    rows.append({"case": "bad_chunk_crc", **result})
    for point, boundary in ((0x12, "preparation_erase_excluded"), (0x14, "final_resource_commit_excluded"),
                             (0x16, "floating_point_progress_excluded")):
        m = Machine(code)
        m.start()
        result = m.deliver(point, bytes(2))
        assert result["stopped_boundary"] == boundary and not result["responses"]
        rows.append({"case": "excluded_transfer_stage", **result})
    for length, valid in ((0, True), (1, True), (257, True), (1024, True), (2048, True),
                          (1, False), (1024, False)):
        m = Machine(code, allow_checksum=True)
        data = fixture_bytes(length)
        m.start(marker=0, argument=sum(data) + int(not valid))
        for block, offset in enumerate(range(0, length, 1024)):
            m.chunk(block, data[offset:offset + 1024])
        result = m.deliver(0x14, bytes(2))
        assert result["spi_read_substitute_bytes"] == length
        assert result["stopped_boundary"] == ("resource_header_validation_excluded" if valid else None)
        assert result["responses"] == ([] if valid else [[0x14, 0, 4, 0]])
        assert result["state_flag"] == int(not valid) and result["state_mode"] == int(valid)
        rows.append({"case": "final_byte_sum", "data_bytes": length,
                     "sum_fixture_matches": valid, **result})
    m = Machine(code, allow_checksum=True)
    m.start(marker=1, argument=123)
    m.chunk(0, fixture_bytes(257))
    result = m.deliver(0x14, bytes(2))
    assert result["stopped_boundary"] == "resource_header_validation_excluded"
    assert result["spi_read_substitute_bytes"] == 0 and not result["spi_command_substitutes"]
    rows.append({"case": "marker_one_skips_whole_byte_sum", **result})
    for length, valid in ((1, True), (255, True), (1024, True), (1, False), (1024, False)):
        m = Machine(code, allow_checksum=True, allow_resource_validation=True)
        data = fixture_bytes(length)
        crc = crc16(data) ^ int(not valid)
        header = bytearray(b"\xff" * 1024)
        struct.pack_into("<4I16s", header, 12, 1024, length, crc, 0, b"synthetic")
        package = bytes(header) + data
        m.start(blocks=(len(package) + 1023) // 1024)
        for block, offset in enumerate(range(0, len(package), 1024)):
            m.chunk(block, package[offset:offset + 1024])
        result = m.deliver(0x14, bytes(2))
        assert result["spi_read_substitute_bytes"] == length
        assert result["first_entry_crc_comparison"] == {"stored_crc_raw": crc,
                                                        "computed_crc_raw": crc16(data), "matched": valid}
        assert result["stopped_boundary"] == ("first_entry_crc_matched_before_name_or_commit" if valid
                                               else "first_entry_crc_rejected_before_error_reply")
        assert not result["responses"]
        rows.append({"case": "first_resource_entry_crc", "data_bytes": length,
                     "crc_fixture_matches": valid, **result})
    for name, entry_size in (("zero_entry_size", 0), ("entry_size_above_staged_bytes", 1025),
                             ("empty_descriptor_list", None)):
        m = Machine(code, allow_checksum=True, allow_resource_validation=True)
        header = bytearray(b"\xff" * 1024)
        if entry_size is not None:
            struct.pack_into("<4I16s", header, 12, 1024, entry_size, 0, 0, b"synthetic")
        m.start(blocks=1)
        m.chunk(0, bytes(header))
        result = m.deliver(0x14, bytes(2))
        assert result["stopped_boundary"] == ("empty_descriptor_list_before_cleanup" if entry_size is None
                                               else "entry_size_rejected_before_error_reply")
        assert result["spi_read_substitute_bytes"] == 0 and result["first_entry_crc_comparison"] is None
        rows.append({"case": name, **result})
    return {"model": "A1763", "lcd_manifest_version": "0.1.9.6", "lcd_sha256": IMAGE_SHA256,
            "application_sha256": CODE_SHA256, "cases": len(rows), "results": rows,
            "public_container_host_metadata": public_container_metadata(image),
            "negative_guards": negative_guards(code), "instruction_limit_per_entry": 100000,
            "maximum_entry_instructions": max(r["instructions"] for r in rows),
            "station_commands_sent": 0, "receiver_application_verified": False,
            "physical_storage_verified": False, "complete_settings_export": False,
            "limits": __doc__.strip()}


def negative_guards(code: bytes) -> list[str]:
    rejected = []
    for name, action in (
        ("payload_fixture_bounds", lambda m: m.deliver(0x13, bytes(1027))),
        ("truncated_descriptor_fixture_excluded", lambda m: m.deliver(0x10, bytes(6))),
        ("truncated_chunk_fixture_excluded", lambda m: m.deliver(0x13, bytes(2))),
        ("zero_chunk_fixture_excluded", lambda m: m.chunk(0, b"")),
        ("real_spi_page_driver_excluded", lambda m: m.step(m.uc, 0x08010590, 2, None)),
        ("hardware_write_excluded", lambda m: m.write_guard(m.uc, 0, 0x40000000, 1, 0, None)),
        ("unrelated_ram_write_excluded", lambda m: m.write_guard(m.uc, 0, 0x200148f8, 1, 0, None)),
        ("retained_context_write_excluded", lambda m: m.write_guard(m.uc, 0, STATE + 12, 4, 0, None)),
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
        p = args.output_dir / f"lcd-asset-transfer-{suffix}.json"
        p.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
        p.chmod(0o600)
    print(json.dumps({"synthetic_cases": result["cases"], "negative_guards": len(result["negative_guards"]),
                      "maximum_entry_instructions": result["maximum_entry_instructions"], "station_commands_sent": 0}))


if __name__ == "__main__":
    main()
