#!/usr/bin/env python3
"""Replay A1763 counter reset and encoded width boundaries in synthetic RAM.

Original reset, accumulator and report encoding execute. Inherited statistics,
persistence and protobuf callback substitutes remain; no flash or station runs.
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

from emulate_energy_counters import COUNTERS, GROUP_TAGS, Machine
from replay_io import FIRMWARE_SHA256, firmware_image


def reset_callers(image):
    sites = []
    for position in range(0, len(image) - 3, 2):
        first, second = struct.unpack_from("<HH", image, position)
        if first & 0xf800 != 0xf000 or second & 0xd000 != 0xd000:
            continue
        sign = (first >> 10) & 1
        i1, i2 = 1 ^ (((second >> 13) & 1) ^ sign), 1 ^ (((second >> 11) & 1) ^ sign)
        delta = ((sign << 24) | (i1 << 23) | (i2 << 22) | ((first & 1023) << 12) | ((second & 2047) << 1))
        if sign:
            delta -= 1 << 25
        if 0x08005000 + position + 4 + delta == 0x08029868:
            sites.append(f"{0x08005000 + position:08x}")
    return sites


def suite():
    image = firmware_image()
    rows = []
    for fill in (0x11, 0x55, 0xff):
        for phase in (0, 1, 9):
            m = Machine()
            m.uc.mem_write(COUNTERS, bytes((fill,)) * 0x118)
            m.uc.mem_write(0x2000024a, bytes((phase,)))
            m.uc.mem_write(0x20000260, struct.pack("<I", 59))
            protected = bytes(m.uc.mem_read(0x20000164, 4))
            m.run(0x08029868)
            data = bytes(m.uc.mem_read(COUNTERS, 0x118))
            assert data[:0xd8] == bytes(0xd8)
            assert data[0xd8:] == bytes((fill,)) * 0x40
            assert m.uc.mem_read(0x2000024a, 1)[0] == phase
            assert bytes(m.uc.mem_read(0x20000260, 4)) == struct.pack("<I", 59)
            assert bytes(m.uc.mem_read(0x20000164, 4)) == protected
            assert m.calls == ["accounting persistence copy"]
            assert all(fields[3] == fields[4] == fields[7] == fields[8] == 0 for fields in m.report().values())
            m.powers(360, 360)
            m.tick()
            assert m.sums()[0] == (360 if phase == 9 else 0)
            rows.append({"group": "reset", "initial_fill": fill, "sample_phase_before": phase,
                         "cleared_accounting_bytes": 216, "following_64_bytes_preserved": True,
                         "sample_phase_and_report_counter_preserved": True,
                         "next_callback_ac_input_sum": m.sums()[0], "output_flags_preserved": True,
                         "persistence_copy_boundary_calls": 1, "flash_executed": False})
    for group in range(4):
        m = Machine(group=group)
        before = 360 * (2**32 - 1)
        m.uc.mem_write(COUNTERS + group * 48, struct.pack("<Q", before))
        a = m.report()[GROUP_TAGS[group]][3]
        m.uc.mem_write(COUNTERS + group * 48, struct.pack("<Q", before + 360))
        b = m.report()[GROUP_TAGS[group]][3]
        assert (a, b) == (2**32 - 1, 0)
        assert m.sums(group)[0] == before + 360
        rows.append({"group": "encoded_wrap", "accounting_group": group,
                     "encoded_before": a, "encoded_after": b,
                     "internal_sum_increased": True, "internal_sum_reset": False})
    return {"model": "A1763", "main_version": "1.1.4.9", "firmware_sha256": FIRMWARE_SHA256,
            "cases": len(rows), "results": rows, "reset_handler": "08029868",
            "direct_thumb_bl_candidate_sites": reset_callers(image),
            "reset_caller_semantics_verified": False, "physical_units_verified": False,
            "limits": __doc__.strip(), "station_commands_sent": 0}


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
        path = args.output_dir / f"gen2-energy-epochs-{suffix}.json"
        path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
        path.chmod(0o600)
    print(f"Passed {result['cases']} synthetic reset/wrap cases; zero station commands")


if __name__ == "__main__":
    main()
