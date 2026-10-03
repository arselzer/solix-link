#!/usr/bin/env python3
"""Replay saved-state collisions and the FA full-status callback, offline only.

Actual A4/D9/DA/FA serializers execute in synthetic RAM. Existing calendar,
memory, logging, transport and persistence substitutes remain. This does not
execute all 19 callbacks, an Android SDK, flash, or any station/network action.
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

from unicorn import UC_HOOK_MEM_READ, UC_PROT_EXEC, UC_PROT_READ
import unicorn

from emulate_clock_semantics import BUFFER, DESC, LENGTH
from emulate_disaster_plan import AUTOMATIC, NOW, DisasterMachine, record
from emulate_general_settings import SETTINGS
from replay_io import FIRMWARE_SHA256, firmware_image


class ExportMachine(DisasterMachine):
    def __init__(self):
        super().__init__()
        self.saved_reads = []
        self.uc.mem_protect(0x08000000, 0x40000, UC_PROT_READ | UC_PROT_EXEC)
        self.uc.hook_add(UC_HOOK_MEM_READ, self.read_hook)

    def read_hook(self, _uc, _access, address, size, _value, _data):
        if address < SETTINGS + 0x19f and address + size > SETTINGS:
            self.saved_reads.append((address, size))

    def step(self, uc, address, size, data):
        if (0x08018730 <= address < 0x08018818
                or 0x0801af54 <= address < 0x0801b018):
            return
        super().step(uc, address, size, data)

    def callback(self, address, *, mode=3, fill=0, capacity=128):
        self.uc.mem_write(BUFFER, bytes((fill,)) * 128)
        self.uc.mem_write(LENGTH, bytes(2))
        self.uc.mem_write(DESC, struct.pack("<B3xIIHBx", 4, BUFFER, LENGTH, capacity, mode))
        self.saved_reads.clear()
        self.run(address, DESC)
        length = struct.unpack("<H", self.uc.mem_read(LENGTH, 2))[0]
        return bytes(self.uc.mem_read(BUFFER, length))

    def wire_values(self):
        return {"a4": self.a4().hex(), "d9": self.d9().hex(),
                "da": self.callback(0x08018730)[1:].hex(),
                "fa": self.callback(0x0801af54)[1:].hex()}


def run_suite():
    image = firmware_image()
    base = 0x08005000
    table = struct.unpack_from("<I", image, 0x08022534 - base)[0]
    descriptors = [struct.unpack_from("<BB2xII", image, table - base + 12 * i)
                   for i in range(19)]
    assert [(tag, kind, fn & ~1) for tag, kind, fn, _ in descriptors if tag == 0xfa] == [
        (0xfa, 4, 0x0801af54)]
    rows = []
    for mode in (1, 2, 3):
        for fill in (0, 0x55, 0xaa, 0xff):
            m = ExportMachine()
            saved = bytes(m.uc.mem_read(SETTINGS, 0x19f))
            outputs = bytes(m.uc.mem_read(0x20000164, 4))
            result = m.callback(0x0801af54, mode=mode, fill=fill)
            expected = bytearray((fill,)) * 21
            expected[0:5] = bytes((4, 1, 1, 1, 1))
            expected[6], expected[7] = 0x17, (fill & 0xc0) | 0x11
            expected[10:21] = bytes(11)
            assert result == b"\x15" + expected
            assert not m.saved_reads
            assert bytes(m.uc.mem_read(SETTINGS, 0x19f)) == saved
            assert bytes(m.uc.mem_read(0x20000164, 4)) == outputs
            assert m.persistence_calls == 0
            rows.append({"group": "fa_callback", "mode": mode, "prior_buffer_fill": fill,
                         "typed_value_hex": result[1:].hex(), "saved_reads": 0,
                         "saved_state_and_output_flags_preserved": True})
    for capacity in (0, 20, 21, 22, 128):
        m = ExportMachine()
        result = m.callback(0x0801af54, capacity=capacity, fill=0xaa)
        assert bool(result) == (capacity > 21)
        assert not m.saved_reads and m.persistence_calls == 0
        rows.append({"group": "fa_capacity", "capacity": capacity,
                     "response_written": bool(result), "saved_reads": 0})

    differences = [f"automatic_{index}_{field}" for index in range(3)
                   for field in ("maximum", "start", "end")]
    differences += ["clock_first_enable", "clock_text", "clock_reserved_bits"]
    for difference in differences:
        left, right = ExportMachine(), ExportMachine()
        for m in (left, right):
            # Disabled, distinct future automatic records are deliberately dormant.
            for index in range(3):
                m.uc.mem_write(AUTOMATIC + 9 * index,
                               record(NOW + 3600 + index * 7200,
                                      NOW + 5400 + index * 7200, 70 + index))
        if difference.startswith("automatic_"):
            _, index, field = difference.split("_")
            address = AUTOMATIC + 9 * int(index)
            raw = bytearray(right.uc.mem_read(address, 9))
            position, size = {"maximum": (0, 1), "start": (1, 4), "end": (5, 4)}[field]
            value = int.from_bytes(raw[position:position + size], "little") + 1
            raw[position:position + size] = value.to_bytes(size, "little")
            right.uc.mem_write(address, bytes(raw))
        elif difference == "clock_first_enable":
            right.uc.mem_write(SETTINGS + 0x46, b"\x01")
        elif difference == "clock_text":
            right.uc.mem_write(SETTINGS + 0x46 + 0x17, b"OTHER-SYNTHETIC-TEXT\0")
        else:
            right.uc.mem_write(SETTINGS + 0x46, b"\x40")
            right.uc.mem_write(SETTINGS + 0x46 + 6, b"\x40")
        before_left = bytes(left.uc.mem_read(SETTINGS, 0x19f))
        before_right = bytes(right.uc.mem_read(SETTINGS, 0x19f))
        assert before_left != before_right
        a, b = left.wire_values(), right.wire_values()
        assert a == b
        assert bytes(left.uc.mem_read(SETTINGS, 0x19f)) == before_left
        assert bytes(right.uc.mem_read(SETTINGS, 0x19f)) == before_right
        assert left.persistence_calls == right.persistence_calls == 0
        rows.append({"group": "saved_state_collision", "hidden_difference": difference,
                     "same_a4_d9_da_fa": True, "distinct_complete_saved_state": True,
                     "serializers_preserve_saved_state": True, "responses": a})
    return {"model": "C1000 Gen 2", "main_firmware": "1.1.4.9",
            "firmware_sha256": FIRMWARE_SHA256, "cases": len(rows), "results": rows,
            "fa_handler": "0801af54", "fa_typed_length": 21,
            "fa_prior_buffer_bytes": [5, 8, 9], "fa_prior_buffer_bits": {"7": "c0"},
            "limits": __doc__.strip(),
            "claim_scope": "These four responses cannot reconstruct the differing saved fields. "
                           "FA is not a saved-state export. Other status callbacks and indirect "
                           "diagnostic routes are not exhaustively proved here.",
            "station_commands_sent": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.environ["SOLIX_ANALYSIS_OUTPUT"] = str(args.output_dir.resolve())
    result = run_suite()
    directory = Path(__file__).resolve().parent
    sources = sorted({Path(module.__file__).resolve() for module in tuple(sys.modules.values())
                      if getattr(module, "__file__", None)
                      and Path(module.__file__).resolve().parent == directory})
    manifest = {"script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "firmware_sha256": FIRMWARE_SHA256, "python": platform.python_version(),
                "unicorn": unicorn.__version__, "sources": {
                    p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}}
    for name, data in (("gen2-status-export-limits-results.json", result),
                       ("gen2-status-export-limits-manifest.json", manifest)):
        path = args.output_dir / name
        path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
        path.chmod(0o600)
    print(f"Passed {result['cases']} synthetic FA/saved-state collision cases; zero station commands")


if __name__ == "__main__":
    main()
