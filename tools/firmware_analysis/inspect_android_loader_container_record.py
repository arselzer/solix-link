#!/usr/bin/env python3
"""Static coverage of one exact protected container-preparation record.

No guest, VM, JNI, Android, native initializer or data transform executes.
Only fixed metadata, static references and hashes are exported. Runtime
memory, branches and callback results remain symbolic. These protector
handler indices are not station protocol opcodes.
"""
from __future__ import annotations

import argparse
from collections import deque
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import platform
import struct
import sys
import zipfile

sys.dont_write_bytecode = True
import capstone
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
import inspect_android_loader_classloader as boundary

carrier, strings, vm, record_two = (
    boundary.carrier, boundary.strings, boundary.vm, boundary.record_two)
START, SIZE, TABLE = boundary.RECORD_START, boundary.RECORD_BYTES, boundary.RECORD_TABLE
CALLS, THUNKS, MAX_STATES = 0x118A40, 0x118AC0, 768
RECORD_SHA = boundary.RECORD_SHA
NEXT_WRAPPER = 0x43308
FUNCTIONS = (0xB20C4, 0xB2DBC, 0xB6B34, 0xB6D44, 0xB69E8, NEXT_WRAPPER)
ASSETS = {
    "assets/IJMDal.Data": (32016, "358ac3092eaa68058739c497ff6cbb27d2cc54e3e92bddb1599fa57e96f5fa31"),
    "assets/ijiami.ajm": (7176288, "ea68ea4fa5981f12e89651f50d637f5d4c80e24fe1700b41c11c2a02cc055960"),
    "assets/ijiami.dat": (8847278, "56cb2bd0d8c6eea2aeced1b274c006be6b852309b5e267daa53a50641e43eb1c"),
}
CALL_ENTRIES = (
    0xFD220, 0xB353C, "memset", 0xB366C, 0xB2DBC, 0x43308, "munmap",
    0xB39C4, 0x10A140, 0x109924, 0xB20FC, 0xB3278, "free", 0xAE000, "memcpy")
THUNK_BOUNDS = (
    (0xEE188, 0xEE1AC), (0xEE1AC, 0xEE248), (0xEE248, 0xEE304),
    (0xEE304, 0xEE3BC), (0xEE3BC, 0xEE474), (0xEE474, 0xEE4E0),
    (0xEE4E0, 0xEE56C), (0xEE56C, 0xEE5E8), (0xEE5E8, 0xEE62C),
    (0xEE62C, 0xEE6E4), (0xEE6E4, 0xEE750), (0xEE750, 0xEE7BC),
    (0xEE7BC, 0xEE868), (0xEE868, 0xEE8BC))
NATIVE_CALL_TOKENS = (
    (92, 0), (150, 1), (241, 2), (264, 3), (297, 4), (334, 5),
    (362, 6), (515, 7), (638, 8), (735, 9), (772, 10), (805, 11),
    (1164, 12), (1539, 13))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def instruction(engine: Cs, image: bytes, address: int):
    code = list(engine.disasm(vm.read(image, address, 4), address))
    vm.expect(len(code), 1, "single native instruction")
    vm.expect(code[0].size, 4, "AArch64 instruction width")
    return code[0]


def check_instructions(engine: Cs, image: bytes, checks: list) -> None:
    for address, mnemonic, operands in checks:
        code = instruction(engine, image, address)
        vm.expect((code.mnemonic, code.op_str), (mnemonic, operands),
                  "pinned static instruction")


# NEW_HANDLERS and semantic checks below are exact pinned interpreter spans.
# Their exclusive ends are the first audited common-advance/return branch.

NEW_HANDLERS = {
    0x00: (0, 0xBD8E0, 0xBD8E4, 'return_void', 'f94f59a4391f01b89f328cf531d646734788efcb1fd4cdc856988bd185e96fdc'),
    0x49: (0, 0xC4180, 0xC4240, 'store_indirect_word', 'e6696723f7f913a882484f5d0b4cf7a49d723f1839687aad6202e5567c19c029'),
    0x44: (0, 0xC1008, 0xC10CC, 'load_indirect_word', '3421c573d3a64b41672730d0975c1ecbaf65acd7fbe8c0c32ec093a46876f80d'),
    0x0E: (0, 0xC08FC, 0xC09C4, 'compare_signed_words_greater', '2b484c37cd3ef6304157e3e545f383fb5f8a7c5d295c4c4bb98e7a02024ada9d'),
    0x16: (0, 0xC13E8, 0xC14B0, 'compare_pointers_equal', 'ffbbea85f7eac87880f1b2b70086a0d537c554e6114161a472fdde8337acaf94'),
    0x81: (0, 0xC3AB0, 0xC3B74, 'xor_words', '1db1bb47b124d9bb44f4b67d5ebeed2f044634138be8d02f1c6747b42e4a5b3d'),
    0x53: (0, 0xC3FF8, 0xC40B4, 'sign_extend_word_to_pointer', '86616cff118bdfa4211876c108eabbeb46ad514222589668ba930b11812ba11a'),
    0x52: (0, 0xBE754, 0xBE808, 'zero_extend_word_to_pointer', 'f211f7424b90e86fb5116bed625959edb6f0f776156ec823411539a4b6e16287'),
    0x70: (0, 0xBF528, 0xBF5F0, 'multiply_pointers', '8076fe1e28c343345896295c069c435d1aca45a56235e6902335f0f0caa866b4'),
    0x07: (0, 0xC0DB4, 0xC0E7C, 'arithmetic_shift_right_pointer', 'd86f5320aa868d07cd843a9c14f62389f23cd0c03afa9eaa5daab8855a05597c'),
    0x0D: (0, 0xC2140, 0xC2208, 'compare_words_not_equal', '8f3b687e2e8d323682715e8b6c016f6509ce89989169ff2ab50be7dfcdb8784f'),
    0x83: (0, 0xC26D4, 0xC2798, 'or_words', '201659e6928ea23bdafe948f637d9715d95a1f17edcdaa26d7a2ca4f7dafbb61'),
    0x18: (0, 0xC2C54, 0xC2D1C, 'compare_signed_pointers_greater', '7f2f13bf1e1d974d843ecd864f299f73aa15422e25ac7a6fddb3fe35555a8dec'),
    0x47: (0, 0xBECA4, 0xBED70, 'store_indirect_byte', '83815a5b8918f950275c778c76b9bcd15b95023704dc40090d9e8a197958c686'),
    0x80: (0, 0xBDBA4, 0xBDC6C, 'and_pointers', '0ed92e8146cd7563238ec0774f326a612cfa271f39aebe305a160e23366e41ae'),
}
NEW_HANDLER_CHECKS = {
    0x00: [
        (0xBD8E0, 'b', '#0xc46e8'),
    ],
    0x49: [
        (0xC4180, 'ldr', 'x8, [sp, #8]'),
        (0xC4184, 'ldr', 'x8, [x8]'),
        (0xC4188, 'ldr', 'x9, [x8, #-0xc]!'),
        (0xC418C, 'ldr', 'w10, [x8, #8]'),
        (0xC4190, 'str', 'w10, [x9]'),
        (0xC4194, 'ldr', 'x9, [sp, #8]'),
        (0xC4198, 'adrp', 'x10, #0x2b000'),
        (0xC419C, 'str', 'x8, [x9]'),
        (0xC41A0, 'ldrb', 'w8, [sp, #0x82c]'),
        (0xC41A4, 'ldr', 'x9, [sp, #0x820]'),
        (0xC41A8, 'add', 'w8, w8, #0x7b'),
        (0xC41AC, 'ldrb', 'w9, [x9]'),
        (0xC41B0, 'and', 'w8, w8, #0xff'),
        (0xC41B4, 'cmp', 'w8, #5'),
        (0xC41B8, 'strb', 'w9, [sp, #0x274]'),
        (0xC41BC, 'ldrb', 'w8, [sp, #0x274]'),
        (0xC4228, 'strb', 'w8, [sp, #0x278]'),
        (0xC422C, 'ldr', 'x10, [sp, #0x820]'),
        (0xC4230, 'ldrb', 'w8, [sp, #0x278]'),
        (0xC4234, 'ldrb', 'w9, [sp, #0x27c]'),
        (0xC4238, 'add', 'x10, x10, #1'),
        (0xC423C, 'b', '#0xbd8ac'),
        (0xC4210, 'eor', 'w8, w13, w8'),
        (0xC4214, 'eor', 'w8, w8, w11'),
    ],
    0x44: [
        (0xC1008, 'ldr', 'x8, [sp, #8]'),
        (0xC100C, 'adrp', 'x10, #0x2b000'),
        (0xC1010, 'ldr', 'x8, [x8]'),
        (0xC1014, 'ldur', 'x9, [x8, #-8]'),
        (0xC1018, 'ldr', 'w9, [x9]'),
        (0xC101C, 'stur', 'w9, [x8, #-8]'),
        (0xC1020, 'ldr', 'x9, [sp, #8]'),
        (0xC1024, 'sub', 'x8, x8, #4'),
        (0xC1028, 'str', 'x8, [x9]'),
        (0xC102C, 'ldrb', 'w8, [sp, #0x82c]'),
        (0xC1030, 'ldr', 'x9, [sp, #0x820]'),
        (0xC1034, 'add', 'w8, w8, #0x7b'),
        (0xC1038, 'ldrb', 'w9, [x9]'),
        (0xC103C, 'and', 'w8, w8, #0xff'),
        (0xC1040, 'cmp', 'w8, #5'),
        (0xC1044, 'strb', 'w9, [sp, #0x214]'),
        (0xC10B4, 'strb', 'w8, [sp, #0x218]'),
        (0xC10B8, 'ldr', 'x10, [sp, #0x820]'),
        (0xC10BC, 'ldrb', 'w8, [sp, #0x218]'),
        (0xC10C0, 'ldrb', 'w9, [sp, #0x21c]'),
        (0xC10C4, 'add', 'x10, x10, #1'),
        (0xC10C8, 'b', '#0xbd8ac'),
        (0xC109C, 'eor', 'w8, w13, w8'),
        (0xC10A0, 'eor', 'w8, w8, w11'),
    ],
    0x0E: [
        (0xC08FC, 'ldr', 'x8, [sp, #8]'),
        (0xC0900, 'ldr', 'x8, [x8]'),
        (0xC0904, 'ldr', 'w9, [x8, #-4]!'),
        (0xC0908, 'ldur', 'w10, [x8, #-4]'),
        (0xC090C, 'ldr', 'x11, [sp, #8]'),
        (0xC0910, 'cmp', 'w10, w9'),
        (0xC0914, 'cset', 'w9, gt'),
        (0xC0918, 'str', 'x8, [x11]'),
        (0xC091C, 'stur', 'w9, [x8, #-4]'),
        (0xC0920, 'ldrb', 'w8, [sp, #0x82c]'),
        (0xC0924, 'ldr', 'x9, [sp, #0x820]'),
        (0xC0928, 'adrp', 'x10, #0x2b000'),
        (0xC092C, 'add', 'w8, w8, #0x7b'),
        (0xC0930, 'ldrb', 'w9, [x9]'),
        (0xC0934, 'and', 'w8, w8, #0xff'),
        (0xC0938, 'cmp', 'w8, #5'),
        (0xC09AC, 'strb', 'w8, [sp, #0x34c]'),
        (0xC09B0, 'ldr', 'x10, [sp, #0x820]'),
        (0xC09B4, 'ldrb', 'w8, [sp, #0x34c]'),
        (0xC09B8, 'ldrb', 'w9, [sp, #0x350]'),
        (0xC09BC, 'add', 'x10, x10, #1'),
        (0xC09C0, 'b', '#0xbd8ac'),
        (0xC0994, 'eor', 'w8, w13, w8'),
        (0xC0998, 'eor', 'w8, w8, w11'),
    ],
    0x16: [
        (0xC13E8, 'ldr', 'x8, [sp, #8]'),
        (0xC13EC, 'ldr', 'x8, [x8]'),
        (0xC13F0, 'ldp', 'x9, x10, [x8, #-0x10]'),
        (0xC13F4, 'ldr', 'x11, [sp, #8]'),
        (0xC13F8, 'sub', 'x12, x8, #0xc'),
        (0xC13FC, 'cmp', 'x9, x10'),
        (0xC1400, 'cset', 'w9, eq'),
        (0xC1404, 'str', 'x12, [x11]'),
        (0xC1408, 'stur', 'w9, [x8, #-0x10]'),
        (0xC140C, 'ldrb', 'w8, [sp, #0x82c]'),
        (0xC1410, 'ldr', 'x9, [sp, #0x820]'),
        (0xC1414, 'adrp', 'x10, #0x2b000'),
        (0xC1418, 'add', 'w8, w8, #0x7b'),
        (0xC141C, 'ldrb', 'w9, [x9]'),
        (0xC1420, 'and', 'w8, w8, #0xff'),
        (0xC1424, 'cmp', 'w8, #5'),
        (0xC1498, 'strb', 'w8, [sp, #0x3ac]'),
        (0xC149C, 'ldr', 'x10, [sp, #0x820]'),
        (0xC14A0, 'ldrb', 'w8, [sp, #0x3ac]'),
        (0xC14A4, 'ldrb', 'w9, [sp, #0x3b0]'),
        (0xC14A8, 'add', 'x10, x10, #1'),
        (0xC14AC, 'b', '#0xbd8ac'),
        (0xC1480, 'eor', 'w8, w13, w8'),
        (0xC1484, 'eor', 'w8, w8, w11'),
    ],
    0x81: [
        (0xC3AB0, 'ldr', 'x8, [sp, #8]'),
        (0xC3AB4, 'ldr', 'x8, [x8]'),
        (0xC3AB8, 'ldr', 'w9, [x8, #-4]!'),
        (0xC3ABC, 'ldur', 'w10, [x8, #-4]'),
        (0xC3AC0, 'ldr', 'x11, [sp, #8]'),
        (0xC3AC4, 'eor', 'w9, w10, w9'),
        (0xC3AC8, 'str', 'x8, [x11]'),
        (0xC3ACC, 'stur', 'w9, [x8, #-4]'),
        (0xC3AD0, 'ldrb', 'w8, [sp, #0x82c]'),
        (0xC3AD4, 'ldr', 'x9, [sp, #0x820]'),
        (0xC3AD8, 'adrp', 'x10, #0x2b000'),
        (0xC3ADC, 'add', 'w8, w8, #0x7b'),
        (0xC3AE0, 'ldrb', 'w9, [x9]'),
        (0xC3AE4, 'and', 'w8, w8, #0xff'),
        (0xC3AE8, 'cmp', 'w8, #5'),
        (0xC3AEC, 'strb', 'w9, [sp, #0x594]'),
        (0xC3B5C, 'strb', 'w8, [sp, #0x598]'),
        (0xC3B60, 'ldr', 'x10, [sp, #0x820]'),
        (0xC3B64, 'ldrb', 'w8, [sp, #0x598]'),
        (0xC3B68, 'ldrb', 'w9, [sp, #0x59c]'),
        (0xC3B6C, 'add', 'x10, x10, #1'),
        (0xC3B70, 'b', '#0xbd8ac'),
        (0xC3B44, 'eor', 'w8, w13, w8'),
        (0xC3B48, 'eor', 'w8, w8, w11'),
    ],
    0x53: [
        (0xC3FF8, 'ldr', 'x8, [sp, #8]'),
        (0xC3FFC, 'adrp', 'x10, #0x2b000'),
        (0xC4000, 'ldr', 'x8, [x8]'),
        (0xC4004, 'ldur', 'w9, [x8, #-4]'),
        (0xC4008, 'asr', 'w9, w9, #0x1f'),
        (0xC400C, 'str', 'w9, [x8], #4'),
        (0xC4010, 'ldr', 'x9, [sp, #8]'),
        (0xC4014, 'str', 'x8, [x9]'),
        (0xC4018, 'ldrb', 'w8, [sp, #0x82c]'),
        (0xC401C, 'ldr', 'x9, [sp, #0x820]'),
        (0xC4020, 'add', 'w8, w8, #0x7b'),
        (0xC4024, 'ldrb', 'w9, [x9]'),
        (0xC4028, 'and', 'w8, w8, #0xff'),
        (0xC402C, 'cmp', 'w8, #5'),
        (0xC4030, 'strb', 'w9, [sp, #0x720]'),
        (0xC4034, 'ldrb', 'w8, [sp, #0x720]'),
        (0xC409C, 'strb', 'w9, [sp, #0x728]'),
        (0xC40A0, 'strb', 'w8, [sp, #0x724]'),
        (0xC40A4, 'ldr', 'x8, [sp, #0x820]'),
        (0xC40A8, 'ldrb', 'w10, [sp, #0x724]'),
        (0xC40AC, 'ldrb', 'w9, [sp, #0x728]'),
        (0xC40B0, 'b', '#0xc45f0'),
        (0xC4088, 'eor', 'w8, w13, w8'),
        (0xC408C, 'eor', 'w8, w8, w11'),
    ],
    0x52: [
        (0xBE754, 'ldr', 'x8, [sp, #8]'),
        (0xBE758, 'adrp', 'x10, #0x2b000'),
        (0xBE75C, 'ldr', 'x8, [x8]'),
        (0xBE760, 'str', 'wzr, [x8], #4'),
        (0xBE764, 'ldr', 'x9, [sp, #8]'),
        (0xBE768, 'str', 'x8, [x9]'),
        (0xBE76C, 'ldrb', 'w8, [sp, #0x82c]'),
        (0xBE770, 'ldr', 'x9, [sp, #0x820]'),
        (0xBE774, 'add', 'w8, w8, #0x7b'),
        (0xBE778, 'ldrb', 'w9, [x9]'),
        (0xBE77C, 'and', 'w8, w8, #0xff'),
        (0xBE780, 'cmp', 'w8, #5'),
        (0xBE784, 'strb', 'w9, [sp, #0x714]'),
        (0xBE788, 'ldrb', 'w8, [sp, #0x714]'),
        (0xBE78C, 'ldr', 'w10, [x10, #0x854]'),
        (0xBE790, 'cset', 'w9, lo'),
        (0xBE7F0, 'strb', 'w9, [sp, #0x71c]'),
        (0xBE7F4, 'strb', 'w8, [sp, #0x718]'),
        (0xBE7F8, 'ldr', 'x8, [sp, #0x820]'),
        (0xBE7FC, 'ldrb', 'w10, [sp, #0x718]'),
        (0xBE800, 'ldrb', 'w9, [sp, #0x71c]'),
        (0xBE804, 'b', '#0xc45f0'),
        (0xBE7DC, 'eor', 'w8, w13, w8'),
        (0xBE7E0, 'eor', 'w8, w8, w11'),
    ],
    0x70: [
        (0xBF528, 'ldr', 'x8, [sp, #8]'),
        (0xBF52C, 'ldr', 'x8, [x8]'),
        (0xBF530, 'ldr', 'x9, [x8, #-8]!'),
        (0xBF534, 'ldur', 'x10, [x8, #-8]'),
        (0xBF538, 'ldr', 'x11, [sp, #8]'),
        (0xBF53C, 'mul', 'x9, x10, x9'),
        (0xBF540, 'lsr', 'x10, x9, #0x20'),
        (0xBF544, 'str', 'x8, [x11]'),
        (0xBF548, 'stp', 'w9, w10, [x8, #-8]'),
        (0xBF54C, 'ldrb', 'w8, [sp, #0x82c]'),
        (0xBF550, 'ldr', 'x9, [sp, #0x820]'),
        (0xBF554, 'adrp', 'x10, #0x2b000'),
        (0xBF558, 'add', 'w8, w8, #0x7b'),
        (0xBF55C, 'ldrb', 'w9, [x9]'),
        (0xBF560, 'and', 'w8, w8, #0xff'),
        (0xBF564, 'cmp', 'w8, #5'),
        (0xBF5D8, 'strb', 'w8, [sp, #0x634]'),
        (0xBF5DC, 'ldr', 'x10, [sp, #0x820]'),
        (0xBF5E0, 'ldrb', 'w8, [sp, #0x634]'),
        (0xBF5E4, 'ldrb', 'w9, [sp, #0x638]'),
        (0xBF5E8, 'add', 'x10, x10, #1'),
        (0xBF5EC, 'b', '#0xbd8ac'),
        (0xBF5C0, 'eor', 'w8, w13, w8'),
        (0xBF5C4, 'eor', 'w8, w8, w11'),
    ],
    0x07: [
        (0xC0DB4, 'ldr', 'x8, [sp, #8]'),
        (0xC0DB8, 'ldr', 'x8, [x8]'),
        (0xC0DBC, 'ldr', 'x9, [x8, #-8]!'),
        (0xC0DC0, 'ldur', 'x10, [x8, #-8]'),
        (0xC0DC4, 'ldr', 'x11, [sp, #8]'),
        (0xC0DC8, 'asr', 'x9, x10, x9'),
        (0xC0DCC, 'lsr', 'x10, x9, #0x20'),
        (0xC0DD0, 'str', 'x8, [x11]'),
        (0xC0DD4, 'stp', 'w9, w10, [x8, #-8]'),
        (0xC0DD8, 'ldrb', 'w8, [sp, #0x82c]'),
        (0xC0DDC, 'ldr', 'x9, [sp, #0x820]'),
        (0xC0DE0, 'adrp', 'x10, #0x2b000'),
        (0xC0DE4, 'add', 'w8, w8, #0x7b'),
        (0xC0DE8, 'ldrb', 'w9, [x9]'),
        (0xC0DEC, 'and', 'w8, w8, #0xff'),
        (0xC0DF0, 'cmp', 'w8, #5'),
        (0xC0E64, 'strb', 'w8, [sp, #0x55c]'),
        (0xC0E68, 'ldr', 'x10, [sp, #0x820]'),
        (0xC0E6C, 'ldrb', 'w8, [sp, #0x55c]'),
        (0xC0E70, 'ldrb', 'w9, [sp, #0x560]'),
        (0xC0E74, 'add', 'x10, x10, #1'),
        (0xC0E78, 'b', '#0xbd8ac'),
        (0xC0E4C, 'eor', 'w8, w13, w8'),
        (0xC0E50, 'eor', 'w8, w8, w11'),
    ],
    0x0D: [
        (0xC2140, 'ldr', 'x8, [sp, #8]'),
        (0xC2144, 'ldr', 'x8, [x8]'),
        (0xC2148, 'ldr', 'w9, [x8, #-4]!'),
        (0xC214C, 'ldur', 'w10, [x8, #-4]'),
        (0xC2150, 'ldr', 'x11, [sp, #8]'),
        (0xC2154, 'cmp', 'w10, w9'),
        (0xC2158, 'cset', 'w9, ne'),
        (0xC215C, 'str', 'x8, [x11]'),
        (0xC2160, 'stur', 'w9, [x8, #-4]'),
        (0xC2164, 'ldrb', 'w8, [sp, #0x82c]'),
        (0xC2168, 'ldr', 'x9, [sp, #0x820]'),
        (0xC216C, 'adrp', 'x10, #0x2b000'),
        (0xC2170, 'add', 'w8, w8, #0x7b'),
        (0xC2174, 'ldrb', 'w9, [x9]'),
        (0xC2178, 'and', 'w8, w8, #0xff'),
        (0xC217C, 'cmp', 'w8, #5'),
        (0xC21F0, 'strb', 'w8, [sp, #0x340]'),
        (0xC21F4, 'ldr', 'x10, [sp, #0x820]'),
        (0xC21F8, 'ldrb', 'w8, [sp, #0x340]'),
        (0xC21FC, 'ldrb', 'w9, [sp, #0x344]'),
        (0xC2200, 'add', 'x10, x10, #1'),
        (0xC2204, 'b', '#0xbd8ac'),
        (0xC21D8, 'eor', 'w8, w13, w8'),
        (0xC21DC, 'eor', 'w8, w8, w11'),
    ],
    0x83: [
        (0xC26D4, 'ldr', 'x8, [sp, #8]'),
        (0xC26D8, 'ldr', 'x8, [x8]'),
        (0xC26DC, 'ldr', 'w9, [x8, #-4]!'),
        (0xC26E0, 'ldur', 'w10, [x8, #-4]'),
        (0xC26E4, 'ldr', 'x11, [sp, #8]'),
        (0xC26E8, 'orr', 'w9, w10, w9'),
        (0xC26EC, 'str', 'x8, [x11]'),
        (0xC26F0, 'stur', 'w9, [x8, #-4]'),
        (0xC26F4, 'ldrb', 'w8, [sp, #0x82c]'),
        (0xC26F8, 'ldr', 'x9, [sp, #0x820]'),
        (0xC26FC, 'adrp', 'x10, #0x2b000'),
        (0xC2700, 'add', 'w8, w8, #0x7b'),
        (0xC2704, 'ldrb', 'w9, [x9]'),
        (0xC2708, 'and', 'w8, w8, #0xff'),
        (0xC270C, 'cmp', 'w8, #5'),
        (0xC2710, 'strb', 'w9, [sp, #0x5ac]'),
        (0xC2780, 'strb', 'w8, [sp, #0x5b0]'),
        (0xC2784, 'ldr', 'x10, [sp, #0x820]'),
        (0xC2788, 'ldrb', 'w8, [sp, #0x5b0]'),
        (0xC278C, 'ldrb', 'w9, [sp, #0x5b4]'),
        (0xC2790, 'add', 'x10, x10, #1'),
        (0xC2794, 'b', '#0xbd8ac'),
        (0xC2768, 'eor', 'w8, w13, w8'),
        (0xC276C, 'eor', 'w8, w8, w11'),
    ],
    0x18: [
        (0xC2C54, 'ldr', 'x8, [sp, #8]'),
        (0xC2C58, 'ldr', 'x8, [x8]'),
        (0xC2C5C, 'ldp', 'x9, x10, [x8, #-0x10]'),
        (0xC2C60, 'ldr', 'x11, [sp, #8]'),
        (0xC2C64, 'sub', 'x12, x8, #0xc'),
        (0xC2C68, 'cmp', 'x9, x10'),
        (0xC2C6C, 'cset', 'w9, gt'),
        (0xC2C70, 'str', 'x12, [x11]'),
        (0xC2C74, 'stur', 'w9, [x8, #-0x10]'),
        (0xC2C78, 'ldrb', 'w8, [sp, #0x82c]'),
        (0xC2C7C, 'ldr', 'x9, [sp, #0x820]'),
        (0xC2C80, 'adrp', 'x10, #0x2b000'),
        (0xC2C84, 'add', 'w8, w8, #0x7b'),
        (0xC2C88, 'ldrb', 'w9, [x9]'),
        (0xC2C8C, 'and', 'w8, w8, #0xff'),
        (0xC2C90, 'cmp', 'w8, #5'),
        (0xC2D04, 'strb', 'w8, [sp, #0x3c4]'),
        (0xC2D08, 'ldr', 'x10, [sp, #0x820]'),
        (0xC2D0C, 'ldrb', 'w8, [sp, #0x3c4]'),
        (0xC2D10, 'ldrb', 'w9, [sp, #0x3c8]'),
        (0xC2D14, 'add', 'x10, x10, #1'),
        (0xC2D18, 'b', '#0xbd8ac'),
        (0xC2CEC, 'eor', 'w8, w13, w8'),
        (0xC2CF0, 'eor', 'w8, w8, w11'),
    ],
    0x47: [
        (0xBECA4, 'ldr', 'x8, [sp, #8]'),
        (0xBECA8, 'adrp', 'x10, #0x2b000'),
        (0xBECAC, 'ldr', 'x8, [x8]'),
        (0xBECB0, 'ldur', 'x9, [x8, #-0xc]'),
        (0xBECB4, 'ldurb', 'w8, [x8, #-4]'),
        (0xBECB8, 'strb', 'w8, [x9]'),
        (0xBECBC, 'ldr', 'x8, [sp, #8]'),
        (0xBECC0, 'ldr', 'x8, [x8]'),
        (0xBECC4, 'ldr', 'x9, [sp, #8]'),
        (0xBECC8, 'sub', 'x8, x8, #0xc'),
        (0xBECCC, 'str', 'x8, [x9]'),
        (0xBECD0, 'ldrb', 'w8, [sp, #0x82c]'),
        (0xBECD4, 'ldr', 'x9, [sp, #0x820]'),
        (0xBECD8, 'add', 'w8, w8, #0x7b'),
        (0xBECDC, 'ldrb', 'w9, [x9]'),
        (0xBECE0, 'and', 'w8, w8, #0xff'),
        (0xBED58, 'strb', 'w8, [sp, #0x260]'),
        (0xBED5C, 'ldr', 'x10, [sp, #0x820]'),
        (0xBED60, 'ldrb', 'w8, [sp, #0x260]'),
        (0xBED64, 'ldrb', 'w9, [sp, #0x264]'),
        (0xBED68, 'add', 'x10, x10, #1'),
        (0xBED6C, 'b', '#0xbd8ac'),
        (0xBED40, 'eor', 'w8, w13, w8'),
        (0xBED44, 'eor', 'w8, w8, w11'),
    ],
    0x80: [
        (0xBDBA4, 'ldr', 'x8, [sp, #8]'),
        (0xBDBA8, 'ldr', 'x8, [x8]'),
        (0xBDBAC, 'ldr', 'x9, [x8, #-8]!'),
        (0xBDBB0, 'ldur', 'x10, [x8, #-8]'),
        (0xBDBB4, 'ldr', 'x11, [sp, #8]'),
        (0xBDBB8, 'and', 'x9, x10, x9'),
        (0xBDBBC, 'lsr', 'x10, x9, #0x20'),
        (0xBDBC0, 'str', 'x8, [x11]'),
        (0xBDBC4, 'stp', 'w9, w10, [x8, #-8]'),
        (0xBDBC8, 'ldrb', 'w8, [sp, #0x82c]'),
        (0xBDBCC, 'ldr', 'x9, [sp, #0x820]'),
        (0xBDBD0, 'adrp', 'x10, #0x2b000'),
        (0xBDBD4, 'add', 'w8, w8, #0x7b'),
        (0xBDBD8, 'ldrb', 'w9, [x9]'),
        (0xBDBDC, 'and', 'w8, w8, #0xff'),
        (0xBDBE0, 'cmp', 'w8, #5'),
        (0xBDC54, 'strb', 'w8, [sp, #0x58c]'),
        (0xBDC58, 'ldr', 'x10, [sp, #0x820]'),
        (0xBDC5C, 'ldrb', 'w8, [sp, #0x58c]'),
        (0xBDC60, 'ldrb', 'w9, [sp, #0x590]'),
        (0xBDC64, 'add', 'x10, x10, #1'),
        (0xBDC68, 'b', '#0xbd8ac'),
        (0xBDC3C, 'eor', 'w8, w13, w8'),
        (0xBDC40, 'eor', 'w8, w8, w11'),
    ],
}

CHECKS = {
    "shared_zero_operand_advance": [
        (0xC45F0, "add", "x11, x8, #1"),
        (0xC45F4, "mov", "w8, w10"),
        (0xC45F8, "mov", "x10, x11"),
        (0xC45FC, "b", "#0xbd8ac"),
    ],
    "native_container_arguments_and_type2": [
        (0xB2DE0, "mov", "x19, x2"), (0xB2DE4, "mov", "x20, x1"),
        (0xB2DE8, "mov", "x21, x0"), (0xB2DF4, "mov", "w2, #0x28"),
        (0xB2DF8, "mov", "x22, x3"), (0xB2E08, "bl", "#0xb69e8"),
        (0xB2E1C, "ldr", "w8, [x0]"), (0xB2E20, "cmp", "w8, #3"),
        (0xB2E24, "b.ge", "#0xb2e50"), (0xB2E28, "cmp", "w8, #2"),
        (0xB2E2C, "b.ne", "#0xb2f14"), (0xB2E38, "mov", "x0, x21"),
        (0xB2E3C, "mov", "x1, x20"), (0xB2E40, "mov", "x2, x19"),
        (0xB2E48, "bl", "#0xb6b34"),
    ],
    "native_container_type3": [
        (0xB2E50, "cmp", "w8, #4"), (0xB2E54, "b.ge", "#0xb2f08"),
        (0xB2E58, "mov", "w8, #3"), (0xB2E5C, "str", "w8, [x22]"),
        (0xB2E64, "add", "x0, sp, #0x18"),
        (0xB2E68, "sub", "x1, x29, #0x14"), (0xB2E6C, "mov", "w2, wzr"),
        (0xB2E78, "bl", "#0xb69e8"), (0xB2E88, "ldr", "x21, [sp, #0x18]"),
        (0xB2E8C, "ldur", "w8, [x29, #-0x14]"),
        (0xB2E90, "add", "x0, sp, #0x18"), (0xB2E94, "mov", "w2, #1"),
        (0xB2E98, "add", "x9, x21, #0x28"),
        (0xB2E9C, "sub", "w1, w8, #0x28"),
        (0xB2EA0, "str", "x9, [sp, #0x18]"),
        (0xB2EA4, "stur", "w1, [x29, #-0x14]"),
        (0xB2EA8, "bl", "#0xb6d44"),
        (0xB2EB4, "ldr", "x0, [sp, #0x18]"),
        (0xB2EB8, "ldursw", "x1, [x29, #-0x14]"),
        (0xB2EC4, "add", "x2, x2, #0xa8"),
        (0xB2EC8, "sub", "x3, x29, #0x10"),
        (0xB2ECC, "sub", "x4, x29, #0x18"),
        (0xB2ED0, "ldr", "x8, [x8, #0x48]"),
        (0xB2ED4, "ldr", "x8, [x8, #0x20]"),
        (0xB2ED8, "blr", "x8"), (0xB2EDC, "tbz", "w0, #0, #0xb3140"),
        (0xB2EE0, "ldur", "x8, [x29, #-0x10]"),
        (0xB2EE4, "str", "x8, [x20]"),
        (0xB2EE8, "ldur", "w9, [x29, #-0x18]"),
        (0xB2EEC, "str", "w9, [x19]"),
        (0xB2EF4, "str", "x8, [x9, #0x2b8]"),
        (0xB2EF8, "str", "wzr, [x9, #0x2c0]"),
    ],
    "runtime_key_dependency": [
        (0xB6D5C, "adrp", "x11, #0x10a000"),
        (0xB6D60, "add", "x11, x11, #0xd0"),
        (0xB6D6C, "ldr", "q0, [x11]"),
        (0xB6D70, "ldur", "q1, [x11, #0xe]"),
        (0xB6D84, "str", "q0, [sp]"),
        (0xB6D88, "stur", "q1, [sp, #0xe]"),
        (0xB6D8C, "ldr", "x13, [x11, #0x160]"),
        (0xB6D90, "add", "x14, x12, x10"),
        (0xB6D94, "ldrb", "w15, [x14, #1]"),
        (0xB6D98, "add", "x13, x13, x10"),
        (0xB6D9C, "ldrb", "w13, [x13, #3]"),
        (0xB6DA0, "add", "x10, x10, #1"),
        (0xB6DA4, "cmp", "x10, #0x1a"),
        (0xB6DA8, "eor", "w13, w13, w15"),
        (0xB6DAC, "strb", "w13, [x14]"),
        (0xB6DB0, "b.ne", "#0xb6d8c"),
    ],
    "four_pointer_one_bit_thunk": [
        (0xEE330, "ldur", "w3, [x20, #-8]"),
        (0xEE348, "ldur", "w2, [x20, #-0x10]"),
        (0xEE360, "ldur", "w1, [x20, #-0x18]"),
        (0xEE37C, "ldur", "w0, [x20, #-0x20]"),
        (0xEE36C, "bfi", "x3, x8, #0x20, #0x20"),
        (0xEE390, "bfi", "x2, x9, #0x20, #0x20"),
        (0xEE394, "bfi", "x1, x10, #0x20, #0x20"),
        (0xEE398, "bfi", "x0, x8, #0x20, #0x20"),
        (0xEE39C, "bfi", "x12, x11, #0x20, #0x20"),
        (0xEE3A0, "blr", "x12"), (0xEE3A4, "and", "w8, w0, #1"),
        (0xEE3A8, "stur", "w8, [x20, #-0x28]"),
    ],
    "remaining_protected_wrapper": [
        (0x43318, "mov", "w19, w1"), (0x4331C, "mov", "x20, x0"),
        (0x43320, "bl", "#0xe2d48"), (0x43324, "mov", "x1, x20"),
        (0x4332C, "bl", "#0xe2e10"), (0x43330, "and", "w1, w19, #0xff"),
        (0x43338, "bl", "#0xe2e20"),
        (0x43348, "adrp", "x1, #0x10b000"),
        (0x4334C, "adrp", "x2, #0x10b000"),
        (0x43350, "adrp", "x3, #0x10b000"),
        (0x43354, "adrp", "x4, #0x10c000"),
        (0x43358, "add", "x1, x1, #0x3e0"),
        (0x4335C, "add", "x2, x2, #0x500"),
        (0x43360, "add", "x3, x3, #0x660"),
        (0x43364, "add", "x4, x4, #0x430"),
        (0x43368, "mov", "w5, #2"), (0x43370, "b", "#0xe2e00"),
    ],
}


def parse_record(record: bytes, dispatch: list[int], max_states: int = MAX_STATES) -> dict:
    """Parse tokens and both branch edges; never evaluate the VM stack."""
    vm.expect(len(record), SIZE, "container record length")
    vm.expect(record[0], 0x3B, "record header marker")
    header_key = record[1] ^ 0x5F
    vm.expect((vm.byte_transform(record[2]) ^ header_key, vm.byte_transform(record[3]) ^ header_key),
              (0, 0), "encoded initial frame count")
    vm.expect(len(dispatch), 256, "protector dispatch table size")
    if not 1 <= max_states <= MAX_STATES:
        raise ValueError("Static state bound outside supported range")
    handlers = dict(record_two.HANDLERS)
    handlers.update(boundary.NEW_HANDLERS)
    handlers.update({op: values[:4] for op, values in NEW_HANDLERS.items()})
    pending = deque([(4, record[1], None)])
    seen, identities, occupied, nodes = set(), {}, {}, []
    while pending:
        state = pending.popleft()
        if state in seen:
            continue
        if len(seen) >= max_states:
            raise ValueError("Static record exceeds state bound")
        position, key, last = state
        if not 4 <= position < len(record):
            raise ValueError("Branch leaves record body")
        seen.add(state)
        raw = record_two.read_record(record, position, 1)[0]
        marker = last is not None and ((last + 0x7B) & 255) < 5 and raw == 0x3B
        op = 0x3B if marker else vm.byte_transform(raw) ^ key ^ 0x5F
        if op not in handlers:
            raise ValueError("Unsupported static protector handler")
        width, target, _, meaning = handlers[op]
        vm.expect(dispatch[op], target, "protector dispatch identity")
        data = record_two.read_record(record, position + 1, width)
        operand = int.from_bytes(data, "little") if width else None
        identity = (op, width, operand)
        vm.expect(identities.setdefault(position, identity), identity, "consistent token identity")
        for offset in range(position, position + 1 + width):
            vm.expect(occupied.setdefault(offset, position), position, "token byte ownership")
        row = {"offset": position, "handler_index": hex(op), "operand_bytes": width,
               "operand": operand, "meaning": meaning, "incoming_key": key,
               "previous_handler": None if last is None else hex(last), "successors": []}
        if op == 0x4C and not 0 <= operand < len(CALL_ENTRIES):
            raise ValueError("Call table index exceeds audited entries")
        if op == 0x4D and not 0 <= operand < len(THUNK_BOUNDS):
            raise ValueError("Thunk index exceeds audited entries")
        end = position + 1 + width
        if op in (0x85, 0x86):
            displacement = int.from_bytes(data, "little", signed=True)
            row["signed_displacement"] = displacement
            row["successors"].append(position + displacement)
            if op == 0x86:
                row["successors"].append(end)
        elif op not in (0, 1, 2):
            row["successors"].append(end)
        next_key = operand if op == 0x3B else op
        for destination in row["successors"]:
            if not 4 <= destination < len(record):
                raise ValueError("Successor leaves record body")
            pending.append((destination, next_key, op))
        nodes.append(row)
    vm.expect(set(occupied), set(range(4, SIZE)), "complete non-header byte coverage")
    vm.expect(len(nodes), 530, "pinned static state count")
    terminals = {(row["offset"], row["handler_index"]) for row in nodes if not row["successors"]}
    vm.expect(terminals, {(1643, "0x0")}, "pinned void terminal")
    calls = sorted({(row["offset"], row["operand"]) for row in nodes if row["handler_index"] == "0x4d"})
    vm.expect(calls, list(NATIVE_CALL_TOKENS), "all static native-call tokens")
    nodes.sort(key=lambda row: (row["offset"], row["incoming_key"], row["previous_handler"] or ""))
    return {"record_start": hex(START), "record_end_exclusive": hex(START + SIZE),
            "record_bytes": SIZE, "record_sha256": digest(record), "header_bytes": 4,
            "covered_body_bytes": len(occupied), "visited_states": len(seen),
            "distinct_token_offsets": len(identities), "unknown_handlers": 0,
            "terminals": [{"offset": 1643, "handler_index": "0x0", "meaning": "return_void"}],
            "native_call_tokens": [{"offset": p, "thunk_index": t} for p, t in calls],
            "nodes": nodes, "runtime_branches_and_native_results": "symbolic",
            "guest_instructions_executed": 0}


def call_table(elf, image: bytes, rw: list[tuple[int, int]]) -> list[dict]:
    """Resolve only 15 audited entries from original ELF RELA provenance."""
    tags = elf.tags
    if not all(tag in tags for tag in (7, 8, 9)):
        raise ValueError("Missing original ELF RELA metadata")
    vm.expect(tags[9], 24, "RELA entry stride")
    if not 0 < tags[8] <= 1024 * 1024 or tags[8] % 24:
        raise ValueError("RELA table outside static size bound")
    symbols = elf.symbols()
    targets = {CALLS + index * 8 for index in range(len(CALL_ENTRIES))}
    relocations = {}
    for position in range(tags[7], tags[7] + tags[8], 24):
        target, info, addend = struct.unpack("<QQq", elf.read(position, 24))
        if target not in targets:
            continue
        if target in relocations or target % 8:
            raise ValueError("Duplicate or unaligned selected relocation")
        if not any(lo <= target and target + 8 <= hi for lo, hi in rw):
            raise ValueError("Selected relocation target outside original RW LOAD")
        kind, symbol_index = info & 0xFFFFFFFF, info >> 32
        if symbol_index >= len(symbols):
            raise ValueError("Selected relocation symbol index outside table")
        relocations[target] = (kind, symbol_index, addend)
    vm.expect(set(relocations), targets, "complete selected call-table relocations")
    rows = []
    for index, expected in enumerate(CALL_ENTRIES):
        address = CALLS + index * 8
        kind, symbol_index, addend = relocations[address]
        if isinstance(expected, str):
            vm.expect((kind, addend), (257, 0), "selected imported R_AARCH64_ABS64")
            symbol = symbols[symbol_index]
            vm.expect((symbol["name"], symbol["defined"]), (expected, False), "selected import")
            vm.expect(struct.unpack("<Q", vm.read(image, address, 8))[0], 0,
                      "unresolved import left unused")
            row = {"index": index, "kind": "unresolved_import", "name": expected,
                   "relocation_type": kind, "symbol_index": symbol_index}
        else:
            vm.expect((kind, symbol_index, addend), (1027, 0, expected),
                      "selected R_AARCH64_RELATIVE provenance")
            vm.expect(vm.pointer(image, address), expected, "selected static table pointer")
            role = "static_data" if index in (0, 8, 9) else "native_function"
            row = {"index": index, "kind": role, "target": hex(expected),
                   "relocation_type": kind}
        rows.append(row)
    return rows


def inspect(elf, image: bytes, decoded: bytes, assets: dict[str, bytes]) -> dict:
    rw = [(s[3], s[3] + s[6]) for s in elf.loads if s[1] & 2]
    relocated, _, _, _ = strings.relocate(elf, image, [(0, vm.RX_END), *rw], rw)
    vm.expect(vm.pointer(relocated, 0xF8AB0), 0xFD220, "global runtime context pointer alias")
    bounds = strings.function_bounds(elf, relocated, vm.RX_END)
    engine = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    vm.expect(struct.unpack("<ii", vm.read(relocated, TABLE, 8)), (0, SIZE), "record descriptor")
    record = vm.read(relocated, START, SIZE)
    vm.expect(digest(record), RECORD_SHA, "complete container record SHA-256")
    dispatch = [vm.pointer(relocated, 0xF3AF0 + index * 8) for index in range(256)]
    inherited_checks = dict(record_two.INSTRUCTION_CHECKS)
    inherited_checks.update({label: checks for label, checks in boundary.CHECKS.items()
                             if label.startswith("new_handler_")})
    inherited_checks["protected_preparation_wrapper"] = boundary.CHECKS["protected_preparation_wrapper"]
    for checks in (*inherited_checks.values(), *CHECKS.values(), *NEW_HANDLER_CHECKS.values()):
        check_instructions(engine, relocated, checks)
    new_handlers = []
    for op, (width, start, end, meaning, expected_sha) in sorted(NEW_HANDLERS.items()):
        if not 0xBD624 <= start < end <= 0xC472C or not end - start <= 384:
            raise ValueError("New handler outside pinned interpreter bounds")
        vm.expect(dispatch[op], start, "new handler dispatch identity")
        data = vm.read(relocated, start, end - start)
        vm.expect(digest(data), expected_sha, "new handler full span SHA-256")
        code = list(engine.disasm(data, start))
        vm.expect(sum(i.size for i in code), len(data), "new handler complete disassembly")
        vm.expect(code[-1].mnemonic, "b", "new handler common advance/return")
        if code[-1].op_str not in ("#0xbd8ac", "#0xbd8a8", "#0xc45f0", "#0xc46e8"):
            raise ValueError("Handler leaves audited common exit")
        if op not in (0, 0x52, 0x53):
            if not any(i.mnemonic == "add" and i.op_str == "x10, x10, #1" for i in code[-6:]):
                raise ValueError("Zero-operand handler lacks exact one-byte token advance")
        new_handlers.append({"handler_index": hex(op), "operand_bytes": width,
                             "start": hex(start), "end_exclusive": hex(end), "bytes": len(data),
                             "meaning": meaning, "sha256": digest(data),
                             "semantic_checks": len(NEW_HANDLER_CHECKS[op]),
                             "common_exit": code[-1].op_str.removeprefix("#")})
    functions = []
    for start in FUNCTIONS:
        if start not in bounds:
            raise ValueError("Selected function lacks original supported FDE")
        lo, hi = bounds[start]
        if not 0 <= lo < hi <= vm.RX_END or hi - lo > 16 * 1024:
            raise ValueError("Selected function outside bounded recovered RX")
        data = vm.read(relocated, lo, hi - lo)
        code = list(engine.disasm(data, lo))
        vm.expect(sum(i.size for i in code), len(data), "complete selected function disassembly")
        functions.append({"start": hex(lo), "end_exclusive": hex(hi), "bytes": len(data),
                          "sha256": digest(data), "direct_calls": sum(i.mnemonic == "bl" for i in code),
                          "indirect_calls": sum(i.mnemonic == "blr" for i in code)})
    calls = call_table(elf, relocated, rw)
    thunks = []
    for index, (start, end) in enumerate(THUNK_BOUNDS):
        vm.expect(vm.pointer(relocated, THUNKS + index * 8), start, "native thunk table pointer")
        vm.expect(bounds.get(start), (start, end), "native thunk exact original FDE")
        data = vm.read(relocated, start, end - start)
        code = list(engine.disasm(data, start))
        vm.expect(sum(i.size for i in code), len(data), "native thunk full disassembly")
        thunks.append({"index": index, "start": hex(start), "end_exclusive": hex(end),
                       "bytes": len(data), "sha256": digest(data),
                       "indirect_branch_sites": [{"address": hex(i.address), "kind": i.mnemonic}
                                                  for i in code if i.mnemonic in ("br", "blr")]})
    cfg = parse_record(record, dispatch)
    preparations = {row["offset"]: row for row in cfg["nodes"]}
    for offset, op, operand in ((249, "0x4c", 4), (252, "0x39", 23), (255, "0x39", 4),
                                (258, "0x39", 38), (261, "0x39", 6), (264, "0x4d", 3)):
        vm.expect((preparations[offset]["handler_index"], preparations[offset]["operand"]),
                  (op, operand), "native container call preparation")
    vm.expect(vm.read(decoded, 0x10A0A8, 12), b"classes.dex\0", "whitelisted extraction name")
    asset_rows = []
    for name, (size, expected_sha) in ASSETS.items():
        data = assets[name]
        vm.expect((len(data), digest(data)), (size, expected_sha), "selected asset hash and size")
        first_word = struct.unpack("<I", data[:4])[0]
        kind = first_word if first_word in (2, 3) else "other"
        asset_rows.append({"entry": name, "bytes": len(data), "sha256": digest(data),
                           "header_type_2_or_3": kind, "starts_with_dex_magic": data.startswith(b"dex\n")})
    vm.expect([row["header_type_2_or_3"] for row in asset_rows], ["other", "other", 3],
              "selected assets do not match type2")
    return {
        "scope": "Static coverage and candidate container route only; no native/VM/JNI/Android execution",
        "guest_instructions_executed": 0, "protected_record": cfg,
        "selected_call_table": calls, "selected_native_thunks": thunks,
        "new_zero_operand_handlers": new_handlers, "selected_native_functions": functions,
        "instruction_check_counts": {
            "inherited": sum(map(len, inherited_checks.values())),
            "new_handlers": sum(map(len, NEW_HANDLER_CHECKS.values())),
            "container_routes_and_next_wrapper": sum(map(len, CHECKS.values()))},
        "selected_assets": asset_rows,
        "native_container_call": {"record_token_offset": 264, "table_index": 4,
            "target": "0xb2dbc", "thunk_index": 3,
            "arguments": ["local23 filename buffer", "local4 out-buffer pointer cell",
                          "local38 out-length cell", "local6 out-type cell"],
            "return": "native w0 bit0; symbolic branch at record offset272",
            "type2": {"helper": "0xb6b34", "matches_selected_asset_headers": False},
            "type3": {"reader": "0xb69e8", "header_bytes": 40,
                "transform": "0xb6d44(payload pointer cell, signed length minus40,1)",
                "callback": "runtime context+0x48 interface+0x20 at0xb2ed8",
                "arguments": ["transformed payload", "signed payload length", "classes.dex",
                              "out pointer cell", "out length cell"],
                "output": "out-buffer/out-length plus context+0x2b8 pointer and +0x2c0 zero",
                "actual_asset_selected_at_runtime": "unresolved descriptor+0x28"}},
        "runtime_key_dependency": {"helper": "0xb6d44", "derived_bytes": 26,
            "formula": "key[i]=static_seed[i+1] XOR runtime_context_pointer160[i+3]",
            "static_seed_offset": "0x10a0d0", "static_seed_bytes": 30,
            "static_seed_initialized_sha256": digest(vm.read(decoded, 0x10A0D0, 30)),
            "runtime_pointer": "context+0x160; writer and actual value unresolved",
            "protected_record_separate_seed_offset": "0x10a140",
            "protected_record_separate_seed_bytes": 30,
            "protected_record_separate_seed_initialized_sha256": digest(vm.read(decoded, 0x10A140, 30)),
            "seed_or_key_bytes_exported": False, "data_transform_executed": False},
        "remaining_wrapper": {"record_token_offset": 1539, "call_table_index": 5,
            "native_address": "0x43308", "calls": "0x10b3e0", "thunks": "0x10b500",
            "records": "0x10b660", "descriptors": "0x10c430", "selector": 2,
            "inputs": ["original x0 pointer", "original w1 low byte"],
            "record_not_parsed_this_batch": True},
        "limits": ["Symbolic stack, memory, branch and callback values; no runtime success proof",
            "No actual asset-name selection, callback identity or runtime key writer established",
            "No plaintext DEX, container transform or SDK charging-pause packet recovered",
            "DETool.dowork data protection is a separate boundary, not this ClassLoader record",
            "Protector handler/table indices do not map to station protocol opcodes",
            "No device-model or firmware applicability established"]}


def negative_parser_checks(record: bytes, dispatch: list[int]) -> list[str]:
    """Deliberately malformed copies; no VM stack or native calls evaluated."""
    checks = []
    def reject(label, candidate, table=dispatch, cap=MAX_STATES):
        try:
            parse_record(candidate, table, cap)
        except ValueError:
            checks.append(label)
            return
        raise ValueError("Malformed synthetic input unexpectedly accepted: " + label)
    reject("short_record", record[:-1])
    reject("long_record", record + b"\0")
    changed = bytearray(record)
    changed[0] ^= 1
    reject("header_marker", changed)
    changed = bytearray(record)
    changed[2] ^= 1
    reject("encoded_frame_count", changed)
    wrong = list(dispatch)
    wrong[0x2D] += 4
    reject("dispatch_identity", record, wrong)
    reject("short_dispatch", record, dispatch[:-1])
    reject("state_bound_exhausted", record, cap=1)
    reject("state_bound_zero", record, cap=0)
    reject("state_bound_unbounded", record, cap=MAX_STATES + 1)
    changed = bytearray(record)
    changed[80:83] = (0x7FFFFF).to_bytes(3, "little")
    reject("branch_outside_record", changed)
    changed = bytearray(record)
    changed[250:252] = (len(CALL_ENTRIES)).to_bytes(2, "little")
    reject("unaudited_call_table_index", changed)
    changed = bytearray(record)
    changed[265] = len(THUNK_BOUNDS)
    reject("unaudited_thunk_index", changed)
    return checks


def negative_table_checks(elf, image: bytes, rw: list[tuple[int, int]]) -> list[str]:
    """Malformed metadata adapters; imported native functions remain unused."""
    positions = {}
    for position in range(elf.tags[7], elf.tags[7] + elf.tags[8], 24):
        target, info, addend = struct.unpack("<QQq", elf.read(position, 24))
        if CALLS <= target < CALLS + len(CALL_ENTRIES) * 8:
            positions[target] = (position, info, addend)
    vm.expect(len(positions), len(CALL_ENTRIES), "negative-test relocation inventory")
    class AlteredElf:
        def __init__(self):
            self.tags = dict(elf.tags)
            self.overrides = {}
            self.changed_symbols = None
        def read(self, address, size):
            return self.overrides.get(address, elf.read(address, size))
        def symbols(self):
            return elf.symbols() if self.changed_symbols is None else self.changed_symbols
    checks = []
    def reject(label, changed):
        try:
            call_table(changed, image, rw)
        except ValueError:
            checks.append(label)
            return
        raise ValueError("Malformed relocation metadata unexpectedly accepted: " + label)
    changed = AlteredElf()
    changed.tags[9] = 16
    reject("rela_stride", changed)
    changed = AlteredElf()
    changed.tags[8] -= 1
    reject("rela_table_size", changed)
    imported_target = CALLS + 2 * 8
    position, info, addend = positions[imported_target]
    changed = AlteredElf()
    changed.overrides[position] = struct.pack("<QQq", imported_target, (info & ~0xFFFFFFFF) | 1026, addend)
    reject("import_relocation_type", changed)
    changed = AlteredElf()
    changed.overrides[position] = struct.pack("<QQq", imported_target, (0xFFFFFF << 32) | 257, addend)
    reject("import_symbol_index", changed)
    changed = AlteredElf()
    changed.changed_symbols = [dict(value) for value in elf.symbols()]
    changed.changed_symbols[info >> 32]["name"] = "unsupported_synthetic_import"
    reject("import_name", changed)
    changed = AlteredElf()
    changed.overrides[position] = struct.pack("<QQq", imported_target + 0x10000, info, addend)
    reject("missing_selected_relocation", changed)
    changed = AlteredElf()
    changed.overrides[position] = struct.pack("<QQq", CALLS, info, addend)
    reject("duplicate_selected_relocation", changed)
    return checks


DEPENDENCY_SHA = {
    "inspect_android_loader_carriers.py": "040926a17b4c6e9a45b4c6dc7dafbf38d4a98f447e8bb559a7daa47474078a6e",
    "inspect_android_loader_strings.py": "37a4ad759373a9f0945e0d3f3dd5734311f6506f0139e3496a1757c63e261f2b",
    "inspect_android_loader_vm_boundary.py": "beda9b69d4c806136deb1d945cb99ab487add9bbaf24800aab73283692c49c68",
    "inspect_android_loader_record_two.py": "ed3cccfaa8734e158a795e86c4de71672af21dff2c151fec333a3b7042fe09d3",
    "inspect_android_loader_classloader.py": "2fb49c0e10919498f820d6bd310d2e12ae1cd4d783a79f8b98e98773f8b21c8b",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base-apk", "images-dir", "initialized-image", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    # Preflight every pinned source/input before any analysis or output creation.
    dependencies = (Path(carrier.__file__), Path(strings.__file__), Path(vm.__file__),
                    Path(record_two.__file__), Path(boundary.__file__))
    for path in dependencies:
        vm.expect(digest(path.read_bytes()), DEPENDENCY_SHA[path.name], "frozen dependency source")
    if args.base_apk.stat().st_size > 256 * 1024 * 1024:
        raise ValueError("APK exceeds bounded input size")
    apk = args.base_apk.read_bytes()
    vm.expect(digest(apk), carrier.APK_SHA256, "base APK SHA-256")
    image_path = args.images_dir / vm.IMAGE_NAME
    vm.expect(image_path.stat().st_size, vm.IMAGE_SIZE, "original recovered image size")
    vm.expect(args.initialized_image.stat().st_size, vm.IMAGE_SIZE, "prior initialized image size")
    image, decoded = image_path.read_bytes(), args.initialized_image.read_bytes()
    vm.expect(digest(image), vm.IMAGE_SHA, "original recovered image SHA-256")
    vm.expect(digest(decoded), record_two.DECODED_SHA, "prior initialized image SHA-256")
    assets = {}
    with zipfile.ZipFile(BytesIO(apk)) as archive:
        names = archive.namelist()
        for name in (vm.LIBRARY, *ASSETS):
            vm.expect(names.count(name), 1, "one selected archive entry")
        info = archive.getinfo(vm.LIBRARY)
        if info.file_size > 2 * 1024 * 1024:
            raise ValueError("Packed library exceeds bounded size")
        packed = archive.read(vm.LIBRARY)
        vm.expect(digest(packed), carrier.LIBRARIES[vm.LIBRARY], "packed library SHA-256")
        for name, (size, sha) in ASSETS.items():
            vm.expect(archive.getinfo(name).file_size, size, "selected asset declared length")
            data = archive.read(name)
            vm.expect((len(data), digest(data)), (size, sha), "selected asset SHA-256 and length")
            assets[name] = data
    elf = carrier.LoadElf(packed)
    result = inspect(elf, image, decoded, assets)
    rw = [(s[3], s[3] + s[6]) for s in elf.loads if s[1] & 2]
    relocated, _, _, _ = strings.relocate(elf, image, [(0, vm.RX_END), *rw], rw)
    record = vm.read(relocated, START, SIZE)
    dispatch = [vm.pointer(relocated, 0xF3AF0 + index * 8) for index in range(256)]
    result["negative_parser_checks"] = negative_parser_checks(record, dispatch)
    result["negative_relocation_checks"] = negative_table_checks(elf, relocated, rw)
    sources = (Path(__file__), *dependencies)
    manifest = {"tool": Path(__file__).name, "apk_sha256": digest(apk),
        "packed_library_sha256": digest(packed), "image_sha256": digest(image),
        "initialized_image_sha256": digest(decoded), "record_sha256": digest(record),
        "source_sha256": {p.name: digest(p.read_bytes()) for p in sources},
        "runtime": {"python": platform.python_version(), "capstone": capstone.__version__},
        "guest_instructions_executed": 0, "asset_transform_executed": False,
        "raw_code_records_strings_or_keys_exported": False}
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    for suffix, value in (("results", result), ("manifest", manifest)):
        path = args.output_dir / f"android-loader-container-record-{suffix}.json"
        path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
        path.chmod(0o600)
    print(json.dumps({"static_states": result["protected_record"]["visited_states"],
        "covered_body_bytes": result["protected_record"]["covered_body_bytes"],
        "native_call_tokens": len(NATIVE_CALL_TOKENS),
        "negative_checks": len(result["negative_parser_checks"]) + len(result["negative_relocation_checks"]),
        "guest_instructions_executed": 0}))


if __name__ == "__main__":
    main()
