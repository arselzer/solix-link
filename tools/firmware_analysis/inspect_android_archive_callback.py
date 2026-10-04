#!/usr/bin/env python3
"""Hash-pinned static archive callback proof; zero guest/JNI/Android execution.

Exports fixed addresses, span hashes and relocation provenance only. No asset
transform, DEX extraction, key derivation, host callback or station command runs.
"""
from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import platform
import struct
import zipfile

import capstone
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
import inspect_android_loader_container_record as proof

FUNCTIONS = (0x54954, 0x54e6c, 0x827a4, 0x82840, 0x82918, 0x82970, 0x82b50, 0x6e2cc)
TABLE = (0x54998, 0x54a60, 0x54b4c, 0x54d20, 0x54e6c, 0x54f44, 0x550c4, 0x55218)
IMPORTS = {0xeeb00: "malloc", 0xeec00: "pthread_mutex_lock", 0xeec10: "pthread_mutex_unlock",
           0xeed20: "memcpy", 0xeea80: "strlen", 0xeeac0: "memcmp"}
CHECKS = [
    (0x5495c, "adrp", "x8, #0xf8000"), (0x54960, "ldr", "x8, [x8, #0xab0]"),
    (0x54964, "adrp", "x9, #0xfe000"), (0x5496c, "add", "x9, x9, #0x768"),
    (0x54970, "ldr", "x8, [x8]"), (0x5497c, "str", "x9, [x8, #0x48]"),
    (0xb2ed0, "ldr", "x8, [x8, #0x48]"), (0xb2ed4, "ldr", "x8, [x8, #0x20]"),
    (0xb2ed8, "blr", "x8"), (0x54e80, "mov", "x23, x0"),
    (0x54e8c, "mov", "x20, x4"), (0x54e90, "mov", "x19, x3"),
    (0x54e94, "mov", "x21, x2"), (0x54e98, "mov", "x22, x1"),
    (0x54e9c, "bl", "#0xeec00"), (0x54ea8, "bl", "#0x827a4"),
    (0x54eb8, "mov", "x1, x21"), (0x54ebc, "bl", "#0x82840"),
    (0x54ec8, "bl", "#0x82918"), (0x54ed8, "str", "x0, [x20]"),
    (0x54ee8, "bl", "#0xeeb00"), (0x54eec, "str", "x0, [x19]"),
    (0x54f00, "bl", "#0x82970"), (0x54f04, "cmp", "w0, #0"),
    (0x54f10, "cset", "w19, eq"), (0x827e0, "bl", "#0x82b50"),
    (0x82850, "ldr", "x19, [x0, #0x30]"), (0x8285c, "bl", "#0xeea80"),
    (0x82890, "bl", "#0xeeac0"), (0x82918, "ldr", "w0, [x0, #0x14]"),
    (0x8298c, "ldrh", "w8, [x0, #0x10]"), (0x82990, "cmp", "w8, #8"),
    (0x829ac, "bl", "#0xeed20"), (0x829fc, "mov", "w1, #-0xf"),
    (0x82a18, "bl", "#0x8e0e4"), (0x82a50, "bl", "#0x8e3e4"),
    (0x82aa8, "bl", "#0x8ffd8"), (0x82bc8, "mov", "w12, #0x4b00"),
    (0x82bd0, "movk", "w12, #0x605, lsl #16"), (0x82bf4, "cmp", "w14, #0x50"),
    # These two pointer writes address the distinct dynamic-symbol table.
    (0x6e314, "adrp", "x21, #0x11a000"), (0x6e318, "add", "x21, x21, #0xf8"),
    (0x6e9b4, "str", "x0, [x21, #0x160]"), (0x6e9cc, "str", "x0, [x21, #0x160]"),
]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load_inputs(apk_path, images_dir, initialized_path):
    if apk_path.stat().st_size > 256 * 1024 * 1024:
        raise ValueError("APK size bound exceeded")
    apk = apk_path.read_bytes()
    proof.vm.expect(digest(apk), proof.carrier.APK_SHA256, "APK hash")
    image_path = images_dir / proof.vm.IMAGE_NAME
    for path in (image_path, initialized_path):
        proof.vm.expect(path.stat().st_size, proof.vm.IMAGE_SIZE, "image size")
    image, initialized = image_path.read_bytes(), initialized_path.read_bytes()
    proof.vm.expect(digest(image), proof.vm.IMAGE_SHA, "original image hash")
    proof.vm.expect(digest(initialized), proof.record_two.DECODED_SHA, "initialized image hash")
    with zipfile.ZipFile(BytesIO(apk)) as archive:
        proof.vm.expect(archive.namelist().count(proof.vm.LIBRARY), 1, "selected library count")
        if archive.getinfo(proof.vm.LIBRARY).file_size > 2 * 1024 * 1024:
            raise ValueError("Library size bound exceeded")
        packed = archive.read(proof.vm.LIBRARY)
        proof.vm.expect(digest(packed), proof.carrier.LIBRARIES[proof.vm.LIBRARY], "packed library hash")
    return proof.carrier.LoadElf(packed), image


def inspect(elf, original):
    rw = [(s[3], s[3] + s[6]) for s in elf.loads if s[1] & 2]
    image, _, _, _ = proof.strings.relocate(elf, original, [(0, proof.vm.RX_END), *rw], rw)
    proof.vm.expect(proof.vm.pointer(image, 0xf8ab0), 0xfd220, "global context alias")
    bounds = proof.strings.function_bounds(elf, image, proof.vm.RX_END)
    engine = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    proof.check_instructions(engine, image, CHECKS)
    spans = []
    for start in FUNCTIONS:
        if start not in bounds:
            raise ValueError("Missing original FDE boundary")
        lo, hi = bounds[start]
        spans.append({"start": hex(lo), "end": hex(hi), "sha256": digest(proof.vm.read(image, lo, hi-lo))})
    targets = {0xfe768 + 8 * i: value for i, value in enumerate(TABLE)}
    relative = {}
    proof.vm.expect(elf.tags[9], 24, "RELA stride")
    for pos in range(elf.tags[7], elf.tags[7] + elf.tags[8], 24):
        target, info, addend = struct.unpack("<QQq", elf.read(pos, 24))
        if target in targets:
            if target in relative:
                raise ValueError("Duplicate interface relocation")
            proof.vm.expect((info & 0xffffffff, info >> 32, addend), (1027, 0, targets[target]), "interface provenance")
            proof.vm.expect(proof.vm.pointer(image, target), addend, "interface pointer")
            relative[target] = addend
    proof.vm.expect(set(relative), set(targets), "complete interface relocations")
    symbols = elf.symbols()
    jumps = {}
    proof.vm.expect(elf.tags[20], 7, "PLT RELA type")
    for pos in range(elf.tags[23], elf.tags[23] + elf.tags[2], 24):
        target, info, addend = struct.unpack("<QQq", elf.read(pos, 24))
        proof.vm.expect((info & 0xffffffff, addend), (1026, 0), "JUMP_SLOT")
        if target in jumps or info >> 32 >= len(symbols):
            raise ValueError("Invalid PLT relocation")
        jumps[target] = symbols[info >> 32]
    imports = []
    for address, name in IMPORTS.items():
        code = list(engine.disasm(proof.vm.read(image, address, 16), address))
        proof.vm.expect([i.mnemonic for i in code], ["adrp", "ldr", "add", "br"], "PLT shape")
        # Pin register shape too; a different load base is not accepted.
        page = int(code[0].op_str.removeprefix("x16, #"), 16)
        offset = int(code[1].op_str.removeprefix("x17, [x16, #").removesuffix("]"), 16)
        proof.vm.expect(code[2].op_str, f"x16, x16, #{hex(offset)}", "PLT base")
        proof.vm.expect(code[3].op_str, "x17", "PLT branch register")
        symbol = jumps.get(page + offset)
        if symbol is None or symbol["defined"]:
            raise ValueError("Missing unresolved import")
        proof.vm.expect(symbol["name"], name, "import name")
        imports.append({"plt": hex(address), "name": name, "relocation_type": 1026})
    candidates, scanned = [], 0
    if not 0 < len(bounds) <= 4096:
        raise ValueError("Function count bound exceeded")
    for lo, hi in sorted(bounds.values()):
        for instruction in engine.disasm(proof.vm.read(image, lo, hi-lo), lo):
            scanned += 1
            if scanned > 300000:
                raise ValueError("Static scan instruction bound exceeded")
            operands = instruction.op_str
            if ("#0x160]" in operands or instruction.mnemonic == "stp" and "#0x158]" in operands) and "[sp," not in operands:
                candidates.append({"address": hex(instruction.address), "function": hex(lo), "mnemonic": instruction.mnemonic})
    return {"static_instruction_checks": len(CHECKS), "function_spans": spans,
            "context_interface_offset": "0x48", "interface_table": "0xfe768",
            "callback_slot": "0x20", "callback_target": "0x54e6c",
            "interface_relative_relocations": [{"address": hex(k), "target": hex(v)} for k, v in relative.items()],
            "imports": imports, "archive_eocd_signature": "504b0506",
            "entry_name": "classes.dex", "stored_and_raw_deflate_branches_identified": True,
            "callback_execution_verified": False, "runtime_key_writer_resolved": False,
            "distinct_symbol_table_base": "0x11a0f8", "false_key_writer_candidates": ["0x6e9b4", "0x6e9cc"],
            "fde_functions_scanned": len(bounds), "static_instructions_scanned": scanned,
            "immediate_160_candidates": candidates,
            "scan_limits": "FDE-bounded immediate operands only; no complete alias/VM/copy writer analysis",
            "guest_instructions_executed": 0, "station_commands_sent": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base-apk", "images-dir", "initialized-image", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    elf, image = load_inputs(args.base_apk, args.images_dir, args.initialized_image)
    result = inspect(elf, image)
    dependencies = [Path(__file__), Path(proof.__file__), Path(proof.boundary.__file__),
                    Path(proof.carrier.__file__), Path(proof.strings.__file__), Path(proof.vm.__file__), Path(proof.record_two.__file__)]
    manifest = {"apk_sha256": proof.carrier.APK_SHA256, "image_sha256": proof.vm.IMAGE_SHA,
                "packed_library_sha256": proof.carrier.LIBRARIES[proof.vm.LIBRARY],
                "initialized_image_sha256": proof.record_two.DECODED_SHA,
                "sources": {p.name: digest(p.read_bytes()) for p in dependencies},
                "python": platform.python_version(), "capstone": capstone.__version__,
                "guest_instructions_executed": 0, "raw_code_or_keys_exported": False}
    args.output_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    for suffix, value in (("results", result), ("manifest", manifest)):
        path = args.output_dir / f"android-archive-callback-{suffix}.json"
        path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
        path.chmod(0o600)
    print(json.dumps({"static_instruction_checks": len(CHECKS), "callback_target": result["callback_target"],
                      "guest_instructions_executed": 0, "runtime_key_writer_resolved": False}))


if __name__ == "__main__":
    main()
