#!/usr/bin/env python3
"""Static coverage of the exact selector-2 loader record; zero guest execution.

Native calls and runtime memory remain symbolic. Public output is bounded
metadata and hashes, without raw records, rolling keys, seed or key material.
"""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import platform
import struct

import capstone
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
import inspect_android_archive_callback as archive

proof = archive.proof
START, SIZE, DESCRIPTOR = 0x10c294, 410, 0x10c440
RECORD_SHA = "556013fd0bef4072440062da80d14c2d782e5d6ec66691687107f0c2251477ba"
ADD_START, ADD_END = 0xc163c, 0xc1700
ADD_SHA = "7e0a51913d62121474d8ea3361425dfdf3eed0cdcd24f22a6e52dfe27bc933a8"
MAX_STATES = 256
CALLS = [(6, 28), (106, 32), (198, 32), (317, 32)]
THUNKS = [(118, 41), (210, 42), (329, 43)]
ADD_CHECKS = [
    (0xc163c, "ldr", "x8, [sp, #8]"), (0xc1640, "ldr", "x8, [x8]"),
    (0xc1644, "ldr", "w9, [x8, #-4]!"), (0xc1648, "ldur", "w10, [x8, #-4]"),
    (0xc164c, "ldr", "x11, [sp, #8]"), (0xc1650, "add", "w9, w10, w9"),
    (0xc1654, "str", "x8, [x11]"), (0xc1658, "stur", "w9, [x8, #-4]"),
    (0xc16f8, "add", "x10, x10, #1"), (0xc16fc, "b", "#0xbd8ac"),
]


def parse_record(record: bytes, image: bytes) -> dict:
    vm, rt = proof.vm, proof.record_two
    vm.expect((len(record), archive.digest(record)), (SIZE, RECORD_SHA), "selector-2 record identity")
    vm.expect(record[0], 0x3b, "record marker")
    key = record[1] ^ 0x5f
    vm.expect((vm.byte_transform(record[2]) ^ key, vm.byte_transform(record[3]) ^ key),
              (3, 0), "initial frame words")
    handlers = {**rt.HANDLERS, **proof.boundary.NEW_HANDLERS,
                **{k: v[:4] for k, v in proof.NEW_HANDLERS.items()},
                0x67: (0, ADD_START, ADD_END, "add_words")}
    pending = deque([(4, record[1], None)])
    seen, identities, occupied, nodes = set(), {}, {}, []
    while pending:
        state = pending.popleft()
        if state in seen:
            continue
        if len(seen) >= MAX_STATES:
            raise ValueError("Selector-2 static state bound exceeded")
        position, key, last = state
        if not 4 <= position < SIZE:
            raise ValueError("Selector-2 branch leaves record")
        seen.add(state)
        raw = record[position]
        op = (0x3b if last is not None and ((last + 0x7b) & 255) < 5 and raw == 0x3b
              else vm.byte_transform(raw) ^ key ^ 0x5f)
        if op not in handlers:
            raise ValueError("Unsupported selector-2 static handler")
        width, target, _, meaning = handlers[op]
        vm.expect(vm.pointer(image, 0xf3af0 + op * 8), target, "handler dispatch identity")
        data = rt.read_record(record, position + 1, width)
        operand = int.from_bytes(data, "little") if width else None
        vm.expect(identities.setdefault(position, (op, width, operand)),
                  (op, width, operand), "consistent token identity")
        for at in range(position, position + 1 + width):
            vm.expect(occupied.setdefault(at, position), position, "token byte ownership")
        successors = []
        if op in (0x85, 0x86):
            successors.append(position + int.from_bytes(data, "little", signed=True))
            if op == 0x86:
                successors.append(position + 1 + width)
        elif op not in (0, 1, 2):
            successors.append(position + 1 + width)
        for destination in successors:
            if not 4 <= destination < SIZE:
                raise ValueError("Selector-2 successor leaves record")
            pending.append((destination, operand if op == 0x3b else op, op))
        nodes.append({"offset": position, "handler": op, "operand": operand,
                      "meaning": meaning, "successors": successors})
    vm.expect(set(occupied), set(range(4, SIZE)), "complete record body coverage")
    vm.expect(len(seen), 127, "pinned static state count")
    vm.expect(sorted((r["offset"], r["operand"]) for r in nodes if r["handler"] == 0x4c), CALLS, "native pointer tokens")
    vm.expect(sorted((r["offset"], r["operand"]) for r in nodes if r["handler"] == 0x4d), THUNKS, "native thunk tokens")
    vm.expect([(r["offset"], r["handler"]) for r in nodes if not r["successors"]], [(409, 0)], "void terminal")
    # Pin the direct context-cell load and first field address, not runtime data.
    by_offset = {r["offset"]: r for r in nodes}
    vm.expect([(by_offset[p]["meaning"], by_offset[p]["operand"]) for p in (6, 9, 10, 13, 16, 25, 26, 29, 32)],
              [("push_call_table_pointer", 28), ("load_indirect_pointer", None),
               ("store_local_pointer", 4), ("push_local_pointer", 4),
               ("push_immediate_pointer", 0x128), ("add_pointers", None),
               ("store_local_pointer", 6), ("push_local_pointer", 6),
               ("load_indirect_pointer", None)], "initial context+128 path")
    return {"bytes": SIZE, "sha256": RECORD_SHA, "initial_frame_words": 3,
            "covered_body_bytes": len(occupied),
            "static_states": len(seen), "distinct_token_offsets": len(identities),
            "unknown_handlers": 0, "terminal_offset": 409, "terminal_kind": "return_void",
            "native_pointer_tokens": [{"offset": p, "index": i} for p, i in CALLS],
            "native_thunk_tokens": [{"offset": p, "index": i} for p, i in THUNKS],
            "initial_context_field_offset": "0x128",
            "symbolic_branches": True, "runtime_memory_evaluated": False}


def inspect(elf, original: bytes) -> dict:
    vm = proof.vm
    rw = [(s[3], s[3] + s[6]) for s in elf.loads if s[1] & 2]
    image, *_ = proof.strings.relocate(elf, original, [(0, vm.RX_END), *rw], rw)
    vm.expect(struct.unpack("<ii", vm.read(image, DESCRIPTOR, 8)), (3124, SIZE), "selector-2 descriptor")
    vm.expect(0x10b660 + 3124, START, "selected record address")
    engine = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    inherited = [*proof.record_two.INSTRUCTION_CHECKS.values(),
                 *(checks for label, checks in proof.boundary.CHECKS.items() if label.startswith("new_handler_")),
                 *proof.NEW_HANDLER_CHECKS.values(),
                 proof.CHECKS["remaining_protected_wrapper"], proof.CHECKS["shared_zero_operand_advance"]]
    for checks in (*inherited, ADD_CHECKS):
        proof.check_instructions(engine, image, checks)
    vm.expect(archive.digest(vm.read(image, ADD_START, ADD_END - ADD_START)), ADD_SHA, "full add-word span")
    bounds = proof.strings.function_bounds(elf, image, vm.RX_END)
    relocations = {}
    for at in range(elf.tags[7], elf.tags[7] + elf.tags[8], 24):
        target, info, addend = struct.unpack("<QQq", elf.read(at, 24))
        if target in {0x10b3e0 + 28*8, 0x10b3e0 + 32*8, *(0x10b500 + i*8 for i in (41, 42, 43))}:
            if target in relocations:
                raise ValueError("Duplicate selected relocation")
            relocations[target] = (info, addend)
    vm.expect(len(relocations), 5, "complete selected relocations")
    vm.expect(relocations[0x10b3e0 + 28*8], (1027, 0xfd220), "global context cell provenance")
    vm.expect(vm.pointer(image, 0x10b3e0 + 28*8), 0xfd220, "global context cell")
    info, addend = relocations[0x10b3e0 + 32*8]
    symbol = elf.symbols()[info >> 32]
    vm.expect((info & 0xffffffff, addend, symbol["name"], symbol["defined"]), (257, 0, "malloc", False), "allocator import provenance")
    vm.expect(vm.read(image, 0x10b3e0 + 32*8, 8), bytes(8), "allocator remains uncalled")
    thunks = []
    for index in (41, 42, 43):
        target = vm.pointer(image, 0x10b500 + index*8)
        vm.expect(relocations[0x10b500 + index*8], (1027, target), "thunk provenance")
        lo, hi = bounds[target]
        code = list(engine.disasm(vm.read(image, lo, hi - lo), lo))
        vm.expect(sum(i.size for i in code), hi - lo, "complete thunk disassembly")
        sites = [hex(i.address) for i in code if i.mnemonic == "blr"]
        vm.expect(len(sites), 1, "one unexecuted indirect native call")
        thunks.append({"index": index, "start": hex(lo), "end": hex(hi),
                       "sha256": archive.digest(vm.read(image, lo, hi-lo)), "indirect_call_sites": sites})
    cfg = parse_record(vm.read(image, START, SIZE), image)
    return {"descriptor": hex(DESCRIPTOR), "selector": 2, "record": cfg,
            "new_handler": {"index": "0x67", "meaning": "add_words", "start": hex(ADD_START),
                            "end": hex(ADD_END), "sha256": ADD_SHA, "instruction_checks": len(ADD_CHECKS)},
            "inherited_instruction_checks": sum(map(len, inherited)),
            "selected_relocations": 5, "allocator_import": "malloc", "allocation_call_sites": 3,
            "thunks": thunks, "runtime_key_writer_resolved": False,
            "selected_asset_filename_resolved": False, "guest_instructions_executed": 0,
            "station_commands_sent": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base-apk", "images-dir", "initialized-image", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    elf, image = archive.load_inputs(args.base_apk, args.images_dir, args.initialized_image)
    result = inspect(elf, image)
    sources = [Path(__file__), Path(archive.__file__), Path(proof.__file__), Path(proof.boundary.__file__),
               Path(proof.record_two.__file__), Path(proof.vm.__file__), Path(proof.strings.__file__), Path(proof.carrier.__file__)]
    manifest = {"apk_sha256": proof.carrier.APK_SHA256, "image_sha256": proof.vm.IMAGE_SHA,
                "initialized_image_sha256": proof.record_two.DECODED_SHA,
                "sources": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
                "python": platform.python_version(), "capstone": capstone.__version__,
                "guest_instructions_executed": 0, "raw_records_or_keys_exported": False}
    args.output_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    for suffix, value in (("results", result), ("manifest", manifest)):
        path = args.output_dir / f"android-selector-two-{suffix}.json"
        path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
        path.chmod(0o600)
    print(json.dumps({"record_bytes": SIZE, "static_states": result["record"]["static_states"],
                      "guest_instructions_executed": 0, "runtime_key_writer_resolved": False}))


if __name__ == "__main__":
    main()
