#!/usr/bin/env python3
"""Bounded replay of factory selector 1/property 0016 with synthetic RAM.

Actual aggregate/getters/parser/CRC execute; inherited memory, logger and
ring-service substitutes remain. No transport, firmware boot or station runs.
This factory route stops an upgrade timer and refreshes cache: not a passive API.
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
from unicorn import arm_const as A
import emulate_gen2_diagnostic_getters as factory
from replay_io import FIRMWARE_SHA256, firmware_image

ADDITIONAL = ((0x0802eec4, 0x0802ef5e), (0x0801ab7c, 0x0801ab82),
              (0x0801aab8, 0x0801aae0), (0x0801aae8, 0x0801ab78),
              (0x080194f8, 0x0801952c))


class AggregateMachine(factory.Machine):
    def __init__(self, image, seed, timer):
        super().__init__(image, seed, timer)
        self.uc.mem_protect(0x08000000, 0x40000, unicorn.UC_PROT_READ | unicorn.UC_PROT_EXEC)
        self.uc.mem_protect(0x40011000, 0x1000, unicorn.UC_PROT_READ)

    def step(self, uc, address, size, data):
        if any(lo <= address < hi for lo, hi in ADDITIONAL):
            self.visited.add(address)
        else:
            super().step(uc, address, size, data)

    def write_hook(self, uc, access, address, size, value, data):
        if not self.guard:
            return
        assert 0x20000000 <= address < address + size <= 0x20020000
        assert not (address < factory.SETTINGS + 0x19f and address + size > factory.SETTINGS)
        assert not (address < 0x2000016c and address + size > 0x20000164)
        self.writes.update(range(address, address + size))

    def aggregate(self):
        self.raw = factory.inner(0x16, selector=1)
        settings = self.read(factory.SETTINGS, 0x19f)
        outputs = self.read(0x20000164, 8)
        self.guard = True
        self.uc.reg_write(A.UC_ARM_REG_SP, factory.STACK)
        self.uc.reg_write(A.UC_ARM_REG_LR, factory.STOP | 1)
        self.uc.emu_start(0x0802f6ad, 0, count=50000)
        assert self.stopped and 0x0802eec4 in self.visited
        assert self.read(factory.SETTINGS, 0x19f) == settings
        assert self.read(0x20000164, 8) == outputs
        assert not self.reads.intersection(range(factory.BACKUP, factory.BACKUP + 38))
        assert len(self.serialized_data) == len(self.replies) == 1
        assert len(self.serialized_data[0]) == 10
        assert self.replies[0] == factory.inner(0x16, self.serialized_data[0], selector=1)
        cache = sorted(self.writes - set(range(factory.SCRATCH, factory.SCRATCH + 0x41f))
                       - set(range(0x2001e000, 0x2001f000))
                       - set(range(0x2000071c, 0x20000724))
                       - {factory.TIMERS + 20})
        return {"reply_bytes": len(self.replies[0]), "data_bytes": 10,
                "data_sha256": hashlib.sha256(self.serialized_data[0]).hexdigest(),
                "backup_record_bytes_read": 0, "settings_and_output_flags_preserved": True,
                "upgrade_timer_status": self.read(factory.TIMERS + 20, 1)[0],
                "cache_written_addresses": [f"{address:08x}" for address in cache],
                "distinct_instruction_addresses": len(self.visited)}


def suite():
    image = firmware_image()
    entry = next(row for row in factory.tables(image)[1]["entries"] if row["property"] == "0016")
    assert entry["handler"] == "0802eec4"
    rows = []
    for seed in (0, 1):
        for timer in (False, True):
            machine = AggregateMachine(image, seed, timer)
            result = machine.aggregate()
            assert result["upgrade_timer_status"] == (3 if timer else 2)
            assert result["cache_written_addresses"]
            rows.append({"seed": seed, "allocated_upgrade_timer": timer, **result})
    for changed in (0, 9, 27):
        left, right = AggregateMachine(image, 0, False), AggregateMachine(image, 0, False)
        right.uc.mem_write(factory.BACKUP + changed, b"\x5a")
        assert left.read(factory.SETTINGS, 0x19f) != right.read(factory.SETTINGS, 0x19f)
        a, b = left.aggregate(), right.aggregate()
        assert a["data_sha256"] == b["data_sha256"] and left.replies == right.replies
        rows.append({"saved_record_offset_changed": changed, "identical_reply": True,
                     "backup_record_bytes_read": 0, "settings_and_output_flags_preserved": True})
    return {"model": "A1763", "main_version": "1.1.4.9", "firmware_sha256": FIRMWARE_SHA256,
            "selector": 1, "property": "0016", "callback": "0802eec4", "cases": len(rows),
            "results": rows, "instruction_limit_per_case": 50000, "station_commands_sent": 0,
            "complete_settings_export": False, "physical_transport_verified": False,
            "passive_query": False, "limits": __doc__.strip()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    result = suite()
    directory = Path(__file__).resolve().parent
    sources = sorted({Path(m.__file__).resolve() for m in tuple(sys.modules.values())
                      if getattr(m, "__file__", None) and Path(m.__file__).resolve().parent == directory})
    manifest = {"firmware_sha256": FIRMWARE_SHA256, "python": platform.python_version(),
                "unicorn": unicorn.__version__, "sources": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}}
    args.output_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    for suffix, value in (("results", result), ("manifest", manifest)):
        path = args.output_dir / f"gen2-factory-aggregate-{suffix}.json"
        path.write_text(json.dumps(value, indent=2) + "\n")
        path.chmod(0o600)
    print(f"Passed {result['cases']} synthetic factory aggregate cases; zero station commands")


if __name__ == "__main__":
    main()
