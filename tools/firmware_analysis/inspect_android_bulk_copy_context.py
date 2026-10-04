#!/usr/bin/env python3
"""Static SDK bulk-copy/caller provenance; no guest, JNI or Android execution.

The candidate +0x160 store copies caller-supplied bytes. One direct caller
uses stack objects; two forward an unresolved argument. No runtime key,
constructor, plaintext DEX, protected record or seed bytes are exported.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform

import capstone
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM

import inspect_android_archive_callback as archive

proof = archive.proof
COPY = 0xdd14c
FUNCTIONS = {COPY: 0xdd1f4, 0xdcc88: 0xdcd60, 0xdcd60: 0xdce84, 0xdcf0c: 0xdcff0}
CALLERS = [(0xdcc88, 0xdccb4, "bl"), (0xdcd60, 0xdcd88, "bl"), (0xdcf0c, 0xdcf3c, "bl")]
CHECKS = [
    (0xdd16c, "mov", "x20, x0"), (0xdd170, "stp", "x8, x9, [x0], #0x10"),
    (0xdd174, "mov", "w2, #0x110"), (0xdd178, "mov", "x19, x1"),
    (0xdd17c, "bl", "#0xeed20"), (0xdd19c, "ldp", "q2, q3, [x19, #0x150]"),
    (0xdd1ac, "stp", "q2, q3, [x20, #0x160]"),
    (0xdd198, "ldp", "q1, q0, [x19, #0x170]"),
    (0xdd1a8, "stp", "q1, q0, [x20, #0x180]"),
    (0xdd1a0, "mov", "x0, x20"), (0xdd1a4, "mov", "w1, wzr"),
    (0xdd1dc, "bl", "#0xddc4c"), (0xdd1e4, "mov", "w0, wzr"), (0xdd1f0, "ret", ""),
    (0xdcc98, "sub", "sp, sp, #0x4d0"), (0xdcca0, "add", "x0, sp, #0x270"),
    (0xdcca4, "bl", "#0xe1220"), (0xdcca8, "mov", "x0, sp"),
    (0xdccac, "add", "x1, sp, #0x270"), (0xdccb4, "bl", "#0xdd14c"),
    (0xdcd30, "add", "x0, sp, #0x270"), (0xdcd34, "mov", "x1, sp"),
    (0xdcd40, "bl", "#0xdcd60"),
    (0xdcd78, "mov", "x19, x1"), (0xdcd7c, "mov", "x1, x0"),
    (0xdcd80, "mov", "x0, x19"), (0xdcd88, "bl", "#0xdd14c"),
    (0xdcf24, "mov", "x21, x1"), (0xdcf28, "mov", "x1, x0"),
    (0xdcf2c, "mov", "x0, x21"), (0xdcf3c, "bl", "#0xdd14c"),
]


def inspect(image: bytes, bounds: dict) -> dict:
    engine = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    proof.check_instructions(engine, image, CHECKS)
    spans = []
    for start, end in FUNCTIONS.items():
        proof.vm.expect(bounds.get(start), (start, end), "exact original FDE span")
        spans.append({"start": hex(start), "end": hex(end),
                      "sha256": archive.digest(proof.vm.read(image, start, end - start))})
    if not 0 < len(bounds) <= 4096:
        raise ValueError("Function-count bound exceeded")
    callers, scanned = [], 0
    for lo, hi in sorted(bounds.values()):
        for instruction in engine.disasm(proof.vm.read(image, lo, hi - lo), lo):
            scanned += 1
            if scanned > 300000:
                raise ValueError("Static instruction-count bound exceeded")
            if instruction.mnemonic in ("bl", "b") and instruction.op_str == f"#{hex(COPY)}":
                callers.append((lo, instruction.address, instruction.mnemonic))
    proof.vm.expect(callers, CALLERS, "complete FDE direct-branch inventory")
    return {"static_instruction_checks": len(CHECKS), "function_spans": spans,
            "fde_functions_scanned": len(bounds), "static_instructions_scanned": scanned,
            "candidate_store": "0xdd1ac", "candidate_store_bytes": 32,
            "destination_expression": "original_arg0 + 0x160",
            "source_expression": "original_arg1 + 0x150",
            "direct_branch_callers": [{"function": hex(f), "address": hex(a), "kind": k}
                                      for f, a, k in callers],
            "caller_provenance": [
                {"function": "0xdcc88", "destination": "local_stack_base",
                 "source": "local_stack_base + 0x270", "destination_context_alias_proved": False},
                {"function": "0xdcd60", "destination": "original_arg1",
                 "source": "original_arg0", "destination_context_alias_proved": False,
                 "known_caller_0xdcd40_destination": "caller_local_stack_base"},
                {"function": "0xdcf0c", "destination": "original_arg1",
                 "source": "original_arg0", "destination_context_alias_proved": False},
            ],
            "runtime_key_writer_resolved": False, "guest_instructions_executed": 0,
            "station_commands_sent": 0,
            "limits": "Direct FDE branch inventory only; indirect calls, runtime aliases, called native effects and protected VM stores remain unresolved."}


def negative_checks(image: bytes, bounds: dict) -> list[str]:
    rejected = []
    for name, address in (("altered_bulk_store", 0xdd1ac), ("altered_source_load", 0xdd19c),
                           ("altered_stack_destination", 0xdcca8)):
        modified = bytearray(image)
        modified[address:address + 4] = bytes(4)
        try:
            inspect(bytes(modified), bounds)
        except (ValueError, AssertionError):
            rejected.append(name)
        else:
            raise AssertionError(f"Accepted {name}")
    altered = dict(bounds)
    altered[COPY] = (COPY, FUNCTIONS[COPY] - 4)
    try:
        inspect(image, altered)
    except (ValueError, AssertionError):
        rejected.append("altered_fde_span")
    else:
        raise AssertionError("Accepted altered FDE span")
    return rejected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base-apk", "images-dir", "initialized-image", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    elf, original = archive.load_inputs(args.base_apk, args.images_dir, args.initialized_image)
    rw = [(s[3], s[3] + s[6]) for s in elf.loads if s[1] & 2]
    image, *_ = proof.strings.relocate(elf, original, [(0, proof.vm.RX_END), *rw], rw)
    bounds = proof.strings.function_bounds(elf, image, proof.vm.RX_END)
    result = inspect(image, bounds)
    result["negative_checks"] = negative_checks(image, bounds)
    dependencies = (Path(__file__), Path(archive.__file__), Path(proof.__file__),
                    Path(proof.boundary.__file__), Path(proof.carrier.__file__),
                    Path(proof.strings.__file__), Path(proof.vm.__file__), Path(proof.record_two.__file__))
    manifest = {"apk_sha256": proof.carrier.APK_SHA256, "image_sha256": proof.vm.IMAGE_SHA,
                "packed_library_sha256": proof.carrier.LIBRARIES[proof.vm.LIBRARY],
                "initialized_image_sha256": proof.record_two.DECODED_SHA,
                "sources": {p.name: archive.digest(p.read_bytes()) for p in dependencies},
                "python": platform.python_version(), "capstone": capstone.__version__,
                "guest_instructions_executed": 0, "raw_code_or_keys_exported": False}
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    for suffix, value in (("results", result), ("manifest", manifest)):
        path = args.output_dir / f"android-bulk-copy-context-{suffix}.json"
        path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
        path.chmod(0o600)
    print(json.dumps({"static_instruction_checks": len(CHECKS), "negative_checks": len(result["negative_checks"]),
                      "runtime_key_writer_resolved": False, "guest_instructions_executed": 0}))


if __name__ == "__main__":
    main()
