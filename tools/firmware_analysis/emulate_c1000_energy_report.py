#!/usr/bin/env python3
"""Bounded A1761 1.5.9 energy encoder replay with synthetic counter RAM.

No device, transport, wall clock or power electronics run. The dynamic protobuf
callback succeeds without output; report field meanings and physical units are
not established by this proof. The installed station's 1.7.1 image is unavailable.
"""

import argparse
import hashlib
import json
from pathlib import Path
import struct

from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1

from emulate_c1000_original_commands import IMAGE, IMAGE_SHA256, Machine, OUTPUT
from solix_link.energy_report import _fields

BUILDER, COUNTERS = 0x080262ec, 0x20002120


class EnergyMachine(Machine):
    def step(self, uc, address, size, data):
        if address == 0x08015c14:
            self.calls.append("dynamic protobuf callback: success without output")
            self.back(1)
        else:
            super().step(uc, address, size, data)


def run_suite(image: bytes) -> dict:
    if not __debug__:
        raise RuntimeError("Replay assertions are required; do not use Python -O")
    rows = []
    cases = (
        ([0, 0, 0, 0], [0, 0, 0, 0]),
        ([360, 720, 1080, 1440], [11, 22, 33, 44]),
        ([359, 361, 719, 721], [1, 2, 3, 4]),
    )
    for sums, durations in cases:
        machine = EnergyMachine(image)
        machine.uc.mem_write(COUNTERS, struct.pack("<4Q5I", *sums, *durations, 99))
        before = bytes(machine.uc.mem_read(COUNTERS, 52))
        machine.run(BUILDER, OUTPUT, registers={UC_ARM_REG_R1: 1024}, count=100000)
        size = machine.uc.reg_read(UC_ARM_REG_R0)
        assert 0 < size < 1024
        payload = bytes(machine.uc.mem_read(OUTPUT, size))
        assert bytes(machine.uc.mem_read(COUNTERS, 52)) == before
        groups = {str(tag): {str(field): value for field, wire, value in _fields(body) if wire == 0}
                  for tag, wire, body in _fields(payload) if tag == 19 and wire == 2}
        assert groups == {"19": {"1": durations[2], "2": durations[0],
            "3": sums[0] // 360, "4": sums[2] // 360, "5": durations[3],
            "6": durations[1], "7": sums[1] // 360, "8": sums[3] // 360}}
        rows.append({"synthetic_sums": sums, "synthetic_durations": durations,
                     "groups": groups, "substitutes": machine.calls})
    return {"firmware_version": "1.5.9", "firmware_sha256": IMAGE_SHA256,
            "builder": f"{BUILDER:08x}", "counter_ram": f"{COUNTERS:08x}",
            "cases": rows, "physical_units_verified": False,
            "limits": ["1.7.1 firmware not replayed", "Dynamic fields omitted",
                       "No port mapping, scheduler timing, resets or retention proof"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware", type=Path, default=IMAGE)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_suite(args.firmware.read_bytes())
    result["source_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result["base_machine_sha256"] = hashlib.sha256(
        Path(__file__).with_name("emulate_c1000_original_commands.py").read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"Passed {len(result['cases'])} actual-encoder cases; physical units unverified")


if __name__ == "__main__":
    main()
