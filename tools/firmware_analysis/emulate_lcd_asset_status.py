#!/usr/bin/env python3
"""Bounded A1763 LCD MAIN ingress, dispatch and asset status responses.

Actual selected LCD CRC validation, MAIN header/point dispatch, completion-state
branches and lowercase-main reply serialization/CRC execute on synthetic RAM.
Logging, libc, UART send and one UI signal call are substitutes. Ordinary
chunk storage, erase, final resource commit, floating-point progress, boot,
hardware and stations are excluded. This proves supplied-state responses,
not a completed physical resource update or a saved-settings exporter.
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
                               UC_ARM_REG_SP, UC_ARM_REG_LR)

from emulate_uart_request_worker import crc16

IMAGE_SHA256 = "c314816f396d6b6958a6a398476eafa182f7c368802569803b60bd6a4e6dc97d"
CODE_SHA256 = "f5683c2f7c9a5bdab638123a7a0a547d3d549e513e18fc10918ed979b83459c6"
BASE, STACK, STOP = 0x08006000, 0x2001f000, 0x08004000
CONTEXT, STATE, INPUT, OUTPUT = 0x20017000, 0x20017200, 0x20017400, 0x20017800
ALLOW = ((0x0802d038, 0x0802d142), (0x0802d198, 0x0802d1fe),
         (0x0802d336, 0x0802d39e), (0x0802d648, 0x0802d652),
         (0x0802d6a4, 0x0802d6ac), (0x0802e17a, 0x0802e196),
         (0x0802e434, 0x0802e56e))


def checked_image(image: bytes) -> bytes:
    if not __debug__:
        raise RuntimeError("Replay assertions are required; do not run Python with -O")
    if len(image) != 925696 or hashlib.sha256(image).hexdigest() != IMAGE_SHA256:
        raise ValueError("LCD image hash/size mismatch")
    length, payload_sum, version, payload_start = struct.unpack_from("<II4sI", image)
    code_length, code_crc = struct.unpack_from("<II", image, 16)
    assert length == len(image) and payload_start == 0x400 and version == bytes((0, 1, 9, 6))
    assert sum(image[payload_start:]) == payload_sum == 0x034b569e
    code = image[payload_start:payload_start + code_length]
    assert code_length == 0x34400 and crc16(code) == code_crc == 0xa038
    assert hashlib.sha256(code).hexdigest() == CODE_SHA256
    assert struct.unpack_from("<II", code) == (0x20016918, 0x08006145)
    assert code[0x144:0x14c] == bytes.fromhex("0648804706480047")
    return code


class Machine:
    def __init__(self, code: bytes) -> None:
        if not __debug__:
            raise RuntimeError("Replay assertions are required; do not run Python with -O")
        if hashlib.sha256(code).hexdigest() != CODE_SHA256:
            raise ValueError("LCD application hash mismatch")
        self.uc = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_THUMB | unicorn.UC_MODE_MCLASS)
        self.uc.mem_map(0x08000000, 0x40000)
        self.uc.mem_write(BASE, code)
        self.uc.mem_protect(0x08000000, 0x40000, unicorn.UC_PROT_READ | unicorn.UC_PROT_EXEC)
        self.uc.mem_map(0x20000000, 0x20000)
        self.transmitted, self.ui_signal_substitutes = [], []
        self.instructions = 0
        self.stopped = False
        self.boundary = None
        self.uc.hook_add(unicorn.UC_HOOK_CODE, self.step)
        self.uc.hook_add(unicorn.UC_HOOK_MEM_WRITE, self.write_guard)

    def back(self, value: int = 0) -> None:
        self.uc.reg_write(UC_ARM_REG_R0, value)
        self.uc.reg_write(unicorn.arm_const.UC_ARM_REG_PC, self.uc.reg_read(UC_ARM_REG_LR))

    def write_guard(self, uc, access, address, size, value, data) -> None:
        assert any(lo <= address < address + size <= hi for lo, hi in (
            (STACK - 0x1000, STACK), (OUTPUT, OUTPUT + 136), (STATE, STATE + 2))), (
                f"Unexpected LCD write {address:08x}")

    def step(self, uc, address, size, data) -> None:
        self.instructions += 1
        r0, r1, r2 = [uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2)]
        if address == STOP:
            self.stopped = True
            uc.emu_stop()
        elif address == 0x0802d652:
            self.boundary = "floating_point_progress_excluded"
            self.stopped = True
            uc.emu_stop()
        elif address == 0x08006b06:
            assert 0 < r1 <= 0x58
            self.write_guard(uc, 0, r0, r1, 0, None)
            uc.mem_write(r0, bytes(r1))
            self.back(r0)
        elif address == 0x08006ab8:
            assert 0 < r2 <= 6
            self.write_guard(uc, 0, r0, r2, 0, None)
            uc.mem_write(r0, bytes(uc.mem_read(r1, r2)))
            self.back(r0)
        elif address in (0x0802cd4c, 0x0802cda0):
            self.back()  # Log construction/delivery only.
        elif address == 0x0803028c:
            assert r0 == 13 and r1 == 0
            self.ui_signal_substitutes.append({"signal_argument": r0, "value_argument": r1})
            self.back()
        elif address == 0x08015328:
            assert r0 == 0 and r1 == OUTPUT
            length = int.from_bytes(uc.mem_read(OUTPUT, 4), "little")
            assert length == 16
            frame = bytes(uc.mem_read(OUTPUT + 8, length))
            assert frame[:4] == b"main" and crc16(frame) == 0
            self.transmitted.append(frame)
            self.back()
        elif not any(lo <= address < hi for lo, hi in ALLOW):
            raise RuntimeError(f"Unexpected LCD instruction {address:08x}")

    def replay(self, *, point: int = 22, mode: int = 0, flag: int = 0,
               offset: int = 0, bad_crc: bool = False, bad_header: bool = False) -> dict:
        assert 0 <= point <= 65535 and 0 <= mode <= 255 and 0 <= flag <= 255
        assert 0 <= offset <= 0xffffffff
        frame = b"MAIN" + struct.pack("<4H", 16, 5, 4, point) + bytes(2)
        frame += struct.pack("<H", crc16(frame))
        if bad_crc:
            frame = frame[:-1] + bytes((frame[-1] ^ 1,))
        if bad_header:
            frame = frame[:6] + b"\6\0" + frame[8:-2]
            frame += struct.pack("<H", crc16(frame))
        self.uc.mem_write(INPUT, struct.pack("<II", len(frame), 0) + frame)
        self.uc.mem_write(CONTEXT, struct.pack("<I", STATE) + bytes(0x7c - 4))
        self.uc.mem_write(CONTEXT + 0x6c, struct.pack("<II", INPUT, OUTPUT))
        self.uc.mem_write(STATE, struct.pack("<BB2xI4xI", flag, mode, offset, CONTEXT))
        self.uc.mem_write(OUTPUT, struct.pack("<II", 0, 128) + bytes(128))
        self.uc.reg_write(UC_ARM_REG_R0, CONTEXT)
        self.uc.reg_write(UC_ARM_REG_SP, STACK)
        self.uc.reg_write(UC_ARM_REG_LR, STOP | 1)
        self.uc.emu_start(0x0802d039, 0, count=10000)
        assert self.stopped, "LCD instruction bound exceeded"
        return {"input_point_raw": point, "seeded_mode": mode, "seeded_flag": flag,
                "final_state_word_raw": int.from_bytes(self.uc.mem_read(STATE, 2), "little"),
                "frames": [{"bytes": len(f), "header_words_le": list(struct.unpack_from("<4H", f, 4)),
                            "response_bytes_10_13": list(f[10:14]),
                            "sha256": hashlib.sha256(f).hexdigest()} for f in self.transmitted],
                "ui_signal_substitutes": self.ui_signal_substitutes,
                "stopped_boundary": self.boundary, "instructions": self.instructions,
                "flash_or_resource_backend_executed": False}


def suite(image: bytes) -> dict:
    code = checked_image(image)
    targets = [0x0802d142 + 2 * delta for delta in struct.unpack_from("<7H", code, 0x0802d142 - BASE)]
    assert targets == [0x0802d150, 0x0802e182, 0x0802d236, 0x0802d336,
                       0x0802d352, 0x0802e182, 0x0802d36a]
    rows = []
    for name, kwargs, reply, state_word in (
        ("idle_status", {}, [22, 0, 1, 100], 0),
        ("mode_zero_flag_one", {"flag": 1}, [22, 0, 1, 0], 1),
        ("mode_two_status", {"mode": 2}, [22, 0, 0, 50], 0x200),
        ("unknown_mode_status", {"mode": 3}, [22, 0, 0, 0], 0x300),
        ("mode_one_math_boundary", {"mode": 1}, None, 0x100),
        ("mode_two_final_receipt", {"point": 20, "mode": 2}, [20, 0, 1, 0], 1),
        ("mode_two_chunk_rejected", {"point": 19, "mode": 2}, [19, 0, 1, 0], 0x200),
        ("chunk_offset_above_bound", {"point": 19, "offset": 0x400001}, [19, 0, 6, 0], 0),
        ("unhandled_point_15", {"point": 21}, [21, 0, 7, 0], 0),
        ("bad_input_crc", {"bad_crc": True}, None, 0),
        ("wrong_selector_header", {"bad_header": True}, None, 0),
    ):
        m = Machine(code)
        result = m.replay(**kwargs)
        assert [f["response_bytes_10_13"] for f in result["frames"]] == ([] if reply is None else [reply]), name
        assert result["final_state_word_raw"] == state_word, name
        assert result["ui_signal_substitutes"] == ([{"signal_argument": 13, "value_argument": 0}]
                                                     if name == "mode_two_final_receipt" else []), name
        rows.append({"case": name, **result})
    return {"model": "A1763", "lcd_manifest_version": "0.1.9.6", "lcd_sha256": IMAGE_SHA256,
            "application_offset": 1024, "application_bytes": len(code), "application_sha256": CODE_SHA256,
            "application_crc16_modbus": 41016, "application_load_address": f"{BASE:08x}",
            "points_10_to_16_targets": [f"{t:08x}" for t in targets],
            "cases": len(rows), "results": rows, "negative_guards": negative_guards(code, image),
            "instruction_limit_per_entry": 10000, "station_commands_sent": 0,
            "receiver_application_verified": False, "complete_settings_export": False,
            "limits": __doc__.strip()}


def negative_guards(code: bytes, image: bytes) -> list[str]:
    rejected = []
    for name, action in (
        ("wrong_image_hash", lambda: checked_image(image[:-1] + bytes((image[-1] ^ 1,)))),
        ("ordinary_chunk_storage_excluded", lambda: Machine(code).replay(point=19)),
        ("final_resource_commit_excluded", lambda: Machine(code).replay(point=20)),
        ("mmio_write_excluded", lambda: Machine(code).write_guard(None, 0, 0x40000000, 1, 0, None)),
        ("unrelated_ram_write_excluded", lambda: Machine(code).write_guard(None, 0, 0x200148f8, 1, 0, None)),
    ):
        try:
            action()
        except (AssertionError, RuntimeError, ValueError):
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
        p = args.output_dir / f"lcd-asset-status-{suffix}.json"
        p.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
        p.chmod(0o600)
    print(json.dumps({"synthetic_cases": result["cases"],
                      "negative_guards": len(result["negative_guards"]), "station_commands_sent": 0}))


if __name__ == "__main__":
    main()
