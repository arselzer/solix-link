#!/usr/bin/env python3
"""Bounded actual-instruction replay of all 19 ordinary A1763 status callbacks.

Synthetic RAM/RTC/GPIO; calendar conversion and logger are substitutes. Original
getters and memcpy/strcmp execute. No station, flash, UART or whole firmware runs.
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
from unicorn.arm_const import UC_ARM_REG_PC

from emulate_clock_semantics import BUFFER, DESC, LENGTH, STOP
from emulate_disaster_plan import AUTOMATIC, NOW, record
from emulate_general_settings import SETTINGS
from emulate_gen2_status_export_limits import ExportMachine
from replay_io import FIRMWARE_SHA256, firmware_image


class StatusMachine(ExportMachine):
    def __init__(self):
        super().__init__()
        self.visited = set()
        self.uc.mem_map(0x40011000, 0x1000, unicorn.UC_PROT_READ)
        self.uc.mem_protect(0x40002000, 0x1000, unicorn.UC_PROT_READ)
        self.uc.hook_add(unicorn.UC_HOOK_MEM_WRITE, self.check_write)

    def check_write(self, _uc, _access, address, size, _value, _data):
        assert (0x20000000 <= address < address + size <= 0x20020000
                or 0x21000000 <= address < address + size <= 0x21002000)

    def step(self, uc, address, size, data):
        self.visited.add(address)
        if address == STOP:
            self.stopped = True
            uc.emu_stop()
        elif address == 0x0800d284:  # Logger only.
            self.back()
        elif address == 0x08014710:  # Calendar conversion, same synthetic year.
            uc.mem_write(0x200008b4, struct.pack("<I", 0x2000e000))
            uc.mem_write(0x2000e014, struct.pack("<I", 126))
            self.back()
        else:
            assert 0x08005000 <= address < 0x08005000 + len(firmware_image())

    def serialize(self, entry, *, mode=1, fill=0):
        tag, kind, address, extra = entry
        self.uc.mem_write(BUFFER, bytes((fill,)) * 128)
        self.uc.mem_write(BUFFER, bytes((tag,)))
        self.uc.mem_write(LENGTH, b"\1\0")
        # The real traversal puts descriptor entry+8 in callback context+16.
        # Omitting it causes false failures in shared port serializers.
        self.uc.mem_write(DESC, struct.pack("<B3xIIHBxI", kind, BUFFER, LENGTH, 128, mode, extra))
        self.saved_reads.clear()
        self.visited.clear()
        saved = bytes(self.uc.mem_read(SETTINGS, 0x19f))
        outputs = bytes(self.uc.mem_read(0x20000164, 4))
        self.run(address & ~1, DESC)  # Inherited 30,000-instruction bound.
        length = struct.unpack("<H", self.uc.mem_read(LENGTH, 2))[0]
        assert 2 < length <= 128
        packet = bytes(self.uc.mem_read(BUFFER, length))
        assert packet[0] == tag and packet[1] == length - 2
        assert bytes(self.uc.mem_read(SETTINGS, 0x19f)) == saved
        assert bytes(self.uc.mem_read(0x20000164, 4)) == outputs
        offsets = sorted({i - SETTINGS for a, size in self.saved_reads
                          for i in range(max(a, SETTINGS), min(a + size, SETTINGS + 0x19f))})
        return packet[2:], offsets


def descriptors():
    image = firmware_image()
    table = struct.unpack_from("<I", image, 0x08022534 - 0x08005000)[0]
    entries = [struct.unpack_from("<BB2xII", image, table - 0x08005000 + 12 * i) for i in range(19)]
    assert [e[0] for e in entries] == [0xa2, 0xa3, 0xa4, 0xa5, 0xa6, 0xa7, 0xa8,
                                      0xaa, 0xab, 0xac, 0xae, 0xb2, 0xd9, 0xda,
                                      0xdc, 0xf9, 0xfa, 0xfd, 0xfe]
    return entries


def dormant(machine):
    for index in range(3):
        machine.uc.mem_write(AUTOMATIC + 9 * index,
                             record(NOW + 3600 + index * 7200,
                                    NOW + 5400 + index * 7200, 70 + index))


def suite():
    entries = descriptors()
    rows, inventory = [], []
    for entry in entries:
        variants, offsets = {}, set()
        for mode in (1, 2, 3):
            for fill in (0, 0x55, 0xaa, 0xff):
                m = StatusMachine()
                dormant(m)
                value, reads = m.serialize(entry, mode=mode, fill=fill)
                offsets.update(reads)
                variants[mode, fill] = value
                rows.append({"tag": f"{entry[0]:02x}", "mode": mode, "prior_fill": fill,
                             "typed_length": len(value), "saved_byte_reads": reads,
                             "value_sha256": hashlib.sha256(value).hexdigest(),
                             "saved_settings_and_output_flags_preserved": True,
                             "distinct_instruction_addresses": len(m.visited)})
        inventory.append({"tag": f"{entry[0]:02x}", "callback": f"{entry[2] & ~1:08x}",
                          "extra_pointer": f"{entry[3]:08x}", "saved_byte_reads": sorted(offsets),
                          "prior_fill_sensitive_value_offsets": {
                              str(mode): [i for i, (a, b) in enumerate(zip(variants[mode, 0], variants[mode, 0xff])) if a != b]
                              for mode in (1, 2, 3)}})
    differences = [f"automatic_{i}_{field}" for i in range(3) for field in ("maximum", "start", "end")]
    differences += ["clock_first_enable", "clock_text", "clock_reserved_bits"]
    for difference in differences:
        left, right = StatusMachine(), StatusMachine()
        dormant(left); dormant(right)
        if difference.startswith("automatic_"):
            _, index, field = difference.split("_")
            address = AUTOMATIC + int(index) * 9
            offset, size = {"maximum": (0, 1), "start": (1, 4), "end": (5, 4)}[field]
            value = int.from_bytes(right.uc.mem_read(address + offset, size), "little") + 1
            right.uc.mem_write(address + offset, value.to_bytes(size, "little"))
        elif difference == "clock_first_enable":
            right.uc.mem_write(SETTINGS + 0x46, b"\1")
        elif difference == "clock_text":
            right.uc.mem_write(SETTINGS + 0x46 + 0x17, b"OTHER-SYNTHETIC-TEXT\0")
        else:
            right.uc.mem_write(SETTINGS + 0x46, b"\x40")
            right.uc.mem_write(SETTINGS + 0x46 + 6, b"\x40")
        assert bytes(left.uc.mem_read(SETTINGS, 0x19f)) != bytes(right.uc.mem_read(SETTINGS, 0x19f))
        matching = [f"{entry[0]:02x}" for entry in entries
                    if left.serialize(entry)[0] == right.serialize(entry)[0]]
        assert len(matching) == 19
        rows.append({"hidden_difference": difference, "all_19_callback_values_identical": True,
                     "matching_tags": matching, "distinct_complete_saved_state": True})
    return {"firmware_sha256": FIRMWARE_SHA256, "model": "A1763", "main_version": "1.1.4.9",
            "cases": len(rows), "inventory": inventory, "results": rows,
            "limits": __doc__.strip(), "instruction_limit_per_callback": 30000,
            "station_commands_sent": 0, "complete_restore_supported": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    os.environ["SOLIX_ANALYSIS_OUTPUT"] = str(args.output_dir.resolve())
    result = suite()
    directory = Path(__file__).resolve().parent
    sources = sorted({Path(m.__file__).resolve() for m in tuple(sys.modules.values())
                      if getattr(m, "__file__", None) and Path(m.__file__).resolve().parent == directory})
    manifest = {"firmware_sha256": FIRMWARE_SHA256, "python": platform.python_version(),
                "unicorn": unicorn.__version__, "sources": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}}
    args.output_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    for suffix, value in (("results", result), ("manifest", manifest)):
        path = args.output_dir / f"gen2-full-status-inventory-{suffix}.json"
        path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
        path.chmod(0o600)
    print(f"Passed {result['cases']} synthetic status cases across 19 callbacks; zero station commands")


if __name__ == "__main__":
    main()
