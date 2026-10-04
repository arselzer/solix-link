#!/usr/bin/env python3
"""Bounded static expression flow for the pinned selector-2 loader record.

Models operand widths and local copies, never runtime memory or native calls.
Both branch paths are retained. Dereferences and malloc results remain symbolic;
no allocation success, disjointness, key value or whole-program absence follows.
"""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import platform

import capstone
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
import inspect_android_selector_two as prior

MAX_PATH_STEPS = 128
MAX_PATHS = 2
VIEW_CHECKS = [
    (0xbd6e8, "sub", "x9, x14, x9, lsl #2"),
    (0xbd6ec, "stp", "x9, x21, [sp, #0x10]"),
    (0xbd6f4, "ldr", "x11, [sp, #0x10]"),
    (0xbd704, "add", "x11, x11, #4"),
    (0xbd708, "stp", "x22, x11, [sp, #0x20]"),
    (0xbd70c, "ldr", "x11, [sp, #0x10]"),
    (0xbd71c, "str", "x11, [sp, #0x30]"),
]
THUNK_CHECKS = [
    (0x0c, "ldr", "x20, [x0]"),
    (0x2c, "sub", "x9, x20, #0x10"),
    (0x30, "ldur", "w0, [x20, #-8]"),
    (0x38, "ldur", "w10, [x20, #-0xc]"),
    (0x40, "ldur", "w9, [x20, #-0x10]"),
    (0x44, "bfi", "x0, x8, #0x20, #0x20"),
    (0x48, "bfi", "x9, x10, #0x20, #0x20"),
    (0x4c, "blr", "x9"),
    (0x50, "lsr", "x8, x0, #0x20"),
    (0x54, "stur", "w0, [x20, #-0x10]"),
    (0x5c, "stur", "w8, [x20, #-0xc]"),
    (0x60, "str", "x21, [x19]"),
]


def graph(record: bytes, image: bytes | bytearray) -> dict:
    """Reconstruct nodes independently after the full prior identity proof."""
    prior.parse_record(record, image)
    proof, vm = prior.proof, prior.proof.vm
    handlers = {**proof.record_two.HANDLERS, **proof.boundary.NEW_HANDLERS,
                **{k: v[:4] for k, v in proof.NEW_HANDLERS.items()},
                0x67: (0, prior.ADD_START, prior.ADD_END, "add_words")}
    pending = deque([(4, record[1], None)])
    seen, nodes = set(), {}
    while pending:
        position, key, last = pending.popleft()
        if (position, key, last) in seen:
            continue
        if len(seen) >= prior.MAX_STATES:
            raise ValueError("Static graph bound exceeded")
        seen.add((position, key, last))
        raw = record[position]
        op = (0x3b if last is not None and ((last + 0x7b) & 255) < 5 and raw == 0x3b
              else vm.byte_transform(raw) ^ key ^ 0x5f)
        width, _, _, meaning = handlers[op]
        data = proof.record_two.read_record(record, position + 1, width)
        operand = int.from_bytes(data, "little") if width else None
        edges = []
        if op in (0x85, 0x86):
            edges.append(position + int.from_bytes(data, "little", signed=True))
            if op == 0x86:
                edges.append(position + 1 + width)
        elif op not in (0, 1, 2):
            edges.append(position + 1 + width)
        nodes[position] = (meaning, operand, edges)
        for destination in edges:
            pending.append((destination, operand if op == 0x3b else op, op))
    prior.proof.vm.expect(len(nodes), 127, "full expression graph")
    return nodes


def binary(operation: str, left: tuple, right: tuple) -> tuple:
    if operation == "add" and left[0] == right[0] == "constant":
        return ("constant", (left[1] + right[1]) & ((1 << 64) - 1))
    return (operation, left, right)


def expression(value: tuple) -> str:
    """Render structural constants only, never seeds, keys or runtime values."""
    kind = value[0]
    if kind == "constant":
        if not 0 <= value[1] <= 4096:
            raise ValueError("Unexpected nonstructural constant")
        return hex(value[1])
    if kind in ("context", "input_pointer", "malloc", "context_cell"):
        return kind if len(value) == 1 else f"{kind}@{value[1]}"
    if kind in ("load32", "load64", "sxt32", "equal"):
        return kind + "(" + ",".join(expression(v) for v in value[1:]) + ")"
    if kind in ("add", "add32", "multiply"):
        return kind + "(" + ",".join(expression(v) for v in value[1:]) + ")"
    raise ValueError("Unsupported public expression")


def trace(nodes: dict, branch: str) -> dict:
    stack, cells, writes, allocations = [], {(0, 8): ("input_pointer",)}, [], []
    position, steps, visited = 4, 0, set()

    def push(width: int, value: tuple) -> None:
        stack.append((width, value))

    def pop(width: int) -> tuple:
        if not stack or stack[-1][0] != width:
            raise ValueError("Symbolic operand width mismatch")
        return stack.pop()[1]

    def store(offset: int, width: int, value: tuple) -> None:
        for key in list(cells):
            if offset < key[0] + key[1] and offset + width > key[0]:
                del cells[key]
        cells[(offset, width)] = value

    while True:
        if position in visited or steps >= MAX_PATH_STEPS:
            raise ValueError("Symbolic path bound exceeded")
        visited.add(position)
        steps += 1
        meaning, operand, edges = nodes[position]
        if meaning == "reserve_zero_words":
            if position != 4 or operand != 59:
                raise ValueError("Unexpected frame reservation")
        elif meaning == "push_call_table_pointer":
            if operand not in (28, 32):
                raise ValueError("Unexpected native pointer")
            push(8, ("context_cell",) if operand == 28 else ("allocator",))
        elif meaning.startswith("push_immediate_"):
            push(8 if meaning.endswith("pointer") else 4, ("constant", operand))
        elif meaning.startswith("push_local_"):
            width = 8 if meaning.endswith("pointer") else 1 if meaning.endswith("byte") else 4
            value = cells.get((operand if width == 1 else operand * 4, width))
            if value is None:
                raise ValueError("Unresolved local view")
            push(8 if width == 8 else 4, value)
        elif meaning.startswith("store_local_"):
            width = 8 if meaning.endswith("pointer") else 1 if meaning.endswith("byte") else 4
            store(operand if width == 1 else operand * 4, width, pop(8 if width == 8 else 4))
        elif meaning in ("add_pointers", "multiply_pointers", "add_words", "compare_pointers_equal"):
            width = 4 if meaning == "add_words" else 8
            right, left = pop(width), pop(width)
            operation = {"add_pointers": "add", "multiply_pointers": "multiply",
                         "add_words": "add32", "compare_pointers_equal": "equal"}[meaning]
            push(4 if meaning == "compare_pointers_equal" else width, binary(operation, left, right))
        elif meaning in ("load_indirect_pointer", "load_indirect_word"):
            address = pop(8)
            value = ("context",) if address == ("context_cell",) else (
                "load64" if meaning.endswith("pointer") else "load32", address)
            push(8 if meaning.endswith("pointer") else 4, value)
        elif meaning in ("store_indirect_pointer", "store_indirect_word"):
            width = 8 if meaning.endswith("pointer") else 4
            value, address = pop(width), pop(8)
            writes.append({"token_offset": position, "width": width,
                           "target": expression(address), "value": expression(value)})
        elif meaning == "sign_extend_word_to_pointer":
            push(8, ("sxt32", pop(4)))
        elif meaning == "call_native_thunk":
            argument = pop(8)
            if operand not in (41, 42, 43) or argument[0] != "constant" or pop(8) != ("allocator",):
                raise ValueError("Unexpected native-call shape")
            # Black-box return only: never call malloc or assume its success/aliasing.
            push(8, ("malloc", position))
            allocations.append({"token_offset": position, "size_argument": argument[1]})
        elif meaning == "jump_relative_if_byte_nonzero":
            condition = pop(4)
            if position != 55 or condition != ("equal", ("load64", ("add", ("context",), ("constant", 0x128))), ("constant", 0)):
                raise ValueError("Unexpected branch predicate")
            position = edges[0 if branch == "null" else 1]
            continue
        elif meaning == "return_void":
            if position != 409 or stack:
                raise ValueError("Unexpected terminal stack")
            break
        elif meaning not in ("reset_rolling_key", "jump_relative"):
            raise ValueError("Unsupported symbolic handler")
        if len(edges) != 1:
            raise ValueError("Unexpected path split")
        position = edges[0]
    return {"branch": branch, "static_steps": steps, "indirect_stores": writes,
            "allocation_calls": allocations, "terminal_operand_stack_empty": True}


def inspect(elf: prior.proof.carrier.LoadElf, original: bytes) -> dict:
    checked = prior.inspect(elf, original)
    vm, proof = prior.proof.vm, prior.proof
    rw = [(s[3], s[3] + s[6]) for s in elf.loads if s[1] & 2]
    image, *_ = proof.strings.relocate(elf, original, [(0, vm.RX_END), *rw], rw)
    engine = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    proof.check_instructions(engine, image, VIEW_CHECKS)
    for row in checked["thunks"]:
        base = int(row["start"], 16)
        proof.check_instructions(engine, image, [(base + offset, mnemonic, operands)
                                                for offset, mnemonic, operands in THUNK_CHECKS])
    nodes = graph(vm.read(image, prior.START, prior.SIZE), image)
    paths = [trace(nodes, branch) for branch in ("non_null", "null")]
    vm.expect([len(row["indirect_stores"]) for row in paths], [3, 7], "store inventory")
    vm.expect(paths[1]["indirect_stores"][0]["target"], "add(context,0x128)", "direct context writer")
    return {"record_sha256": prior.RECORD_SHA, "paths": paths, "max_paths": MAX_PATHS,
            "additional_instruction_checks": len(VIEW_CHECKS) + 3 * len(THUNK_CHECKS),
            "inherited_instruction_checks": checked["inherited_instruction_checks"] + len(prior.ADD_CHECKS),
            "runtime_memory_evaluated": False, "runtime_aliases_resolved": False,
            "runtime_key_writer_resolved": False, "allocation_success_verified": False,
            "guest_instructions_executed": 0, "station_commands_sent": 0,
            "limits": __doc__.strip()}


def negative_checks(elf: prior.proof.carrier.LoadElf, original: bytes) -> list[dict]:
    """Reject altered stack views and handler shapes without guest execution."""
    checks = []
    for label, address in (("frame_byte_view_instruction", 0xbd71c),
                           ("native_pointer_return_instruction", 0xe2c00)):
        changed = bytearray(original)
        changed[address:address + 4] = bytes(4)
        try:
            inspect(elf, bytes(changed))
        except ValueError:
            checks.append({"case": label, "rejected": True})
        else:
            raise ValueError("Altered semantic instruction accepted")
    vm, proof = prior.proof.vm, prior.proof
    rw = [(s[3], s[3] + s[6]) for s in elf.loads if s[1] & 2]
    image, *_ = proof.strings.relocate(elf, original, [(0, vm.RX_END), *rw], rw)
    for label, offset, meaning, operand in (
        ("local_byte_alias", 49, "store_local_byte", 48),
        ("indirect_store_width", 373, "store_indirect_word", None),
    ):
        nodes = graph(vm.read(image, prior.START, prior.SIZE), image)
        nodes[offset] = (meaning, operand, nodes[offset][2])
        try:
            trace(nodes, "non_null")
        except ValueError:
            checks.append({"case": label, "rejected": True})
        else:
            raise ValueError("Altered symbolic stack shape accepted")
    return checks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base-apk", "images-dir", "initialized-image", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    elf, image = prior.archive.load_inputs(args.base_apk, args.images_dir, args.initialized_image)
    result = inspect(elf, image)
    result["negative_checks"] = negative_checks(elf, image)
    sources = [Path(__file__), Path(prior.__file__), Path(prior.archive.__file__),
               Path(prior.proof.__file__), Path(prior.proof.boundary.__file__),
               Path(prior.proof.record_two.__file__), Path(prior.proof.vm.__file__),
               Path(prior.proof.strings.__file__), Path(prior.proof.carrier.__file__)]
    manifest = {"apk_sha256": prior.proof.carrier.APK_SHA256,
                "image_sha256": prior.proof.vm.IMAGE_SHA,
                "packed_library_sha256": prior.proof.carrier.LIBRARIES[prior.proof.vm.LIBRARY],
                "initialized_image_sha256": prior.proof.record_two.DECODED_SHA,
                "sources": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
                "python": platform.python_version(), "capstone": capstone.__version__,
                "guest_instructions_executed": 0, "raw_records_or_keys_exported": False}
    args.output_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    for suffix, value in (("results", result), ("manifest", manifest)):
        path = args.output_dir / f"android-selector-two-dataflow-{suffix}.json"
        path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
        path.chmod(0o600)
    print(json.dumps({"static_paths": len(result["paths"]), "context_writer_offset": "0x128",
                      "runtime_key_writer_resolved": False, "guest_instructions_executed": 0}))


if __name__ == "__main__":
    main()
