#!/usr/bin/env python3
"""Static ClassLoader and asset-transform boundaries for one exact APK build.

No native initializer, protector VM, JNI/Android or asset code executes.
Public output contains only selected fixed metadata and hashes.
"""
from __future__ import annotations

import argparse
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import struct
import sys
import zipfile

sys.dont_write_bytecode = True
import capstone
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
import inspect_android_loader_carriers as carrier
import inspect_android_loader_strings as strings
import inspect_android_loader_vm_boundary as vm
import inspect_android_loader_record_two as record_two

JNI_SHA = "99e64ebbe749e6df284f852f11b3c73f6ea97baf15120428f40f887fe0616e61"
RECORD_START, RECORD_BYTES, RECORD_TABLE = 0x118B30, 1644, 0x11919C
RECORD_SHA = "11889935baf88763cb254647ed77bd71f343b12714e578d6d007982b8c6dae1e"
MAX_PREFIX_TOKENS = 64
FUNCTIONS = (0x77134, 0x740E0, 0x7A6E8, 0xB20C4, 0xAF374, 0xAEE74,
             0xB1DB4, 0xB6B34, 0xB69E8, 0xB6D44, 0x92104, 0xB20FC)
NEW_HANDLERS = {
    0x2D: (2, 0xC05FC, 0xC075C, "reserve_signed_word_count"),
    0x30: (1, 0xBEA64, 0xBEB20, "push_immediate_byte"),
    0x42: (0, 0xC1D78, 0xC1E44, "load_indirect_byte"),
}
LITERALS = {
    0x109340: "dalvik/system/DexPathList", 0x1093A0: "java/nio/ByteBuffer",
    0x1093F0: "([Ljava/nio/ByteBuffer;Ljava/util/List;)[Ldalvik/system/DexPathList$Element;",
    0x109440: "makeInMemoryDexElements", 0x109460: "([BII)Ljava/nio/ByteBuffer;",
    0x10947C: "wrap", 0x109490: "dalvik/system/InMemoryDexClassLoader",
    0x1094C0: "([Ljava/nio/ByteBuffer;Ljava/lang/ClassLoader;)V",
    0x10A070: "_Unwind_Get_pr0", 0x10A0A8: "classes.dex",
    0x10A0B8: "assets/%s", 0x10A0C4: "%s",
}
JNI_SLOTS = {"FindClass": 0x30, "NewGlobalRef": 0xA8,
             "DeleteLocalRef": 0xB8, "NewObjectArray": 0x560,
             "SetObjectArrayElement": 0x570, "NewByteArray": 0x580,
             "SetByteArrayRegion": 0x680, "NewDirectByteBuffer": 0x728}
# Exact selected native instruction identities; byte reads/disassembly only.
CHECKS = {
    'al_arguments_and_dispatch': [
        (0x77184, 'mov', 'x22, x4'),
        (0x77188, 'mov', 'x21, x3'),
        (0x7718C, 'cmp', 'x8, #0'),
        (0x77190, 'csel', 'w10, w11, w10, eq'),
        (0x77194, 'ldr', 'x10, [x23, w10, uxtw #3]'),
        (0x77198, 'mov', 'x19, x2'),
        (0x7719C, 'mov', 'x20, x0'),
        (0x778D0, 'adrp', 'x3, #0x27000'),
        (0x778D4, 'add', 'x3, x3, #0x285'),
        (0x778D8, 'mov', 'x0, x20'),
        (0x778DC, 'mov', 'x1, x19'),
        (0x778E0, 'mov', 'x2, xzr'),
        (0x778E4, 'bl', '#0x7a6e8'),
        (0x76770, 'blr', 'x8'),
        (0x76774, 'add', 'x3, sp, #0x30'),
        (0x76778, 'mov', 'x0, x19'),
        (0x7677C, 'mov', 'x1, x28'),
        (0x76780, 'mov', 'x2, x20'),
        (0x76784, 'bl', '#0x7a6e8'),
        (0x7A700, 'adrp', 'x24, #0xf8000'),
        (0x7A704, 'ldr', 'x24, [x24, #0xab0]'),
        (0x7A708, 'ldr', 'x8, [x24]'),
        (0x7A70C, 'ldr', 'x9, [x8, #0x128]'),
        (0x7A710, 'cbz', 'x9, #0x7a738'),
        (0x7A714, 'mov', 'w9, #1'),
        (0x7A738, 'mov', 'x20, x3'),
        (0x7A73C, 'mov', 'x21, x2'),
        (0x7A740, 'mov', 'x19, x1'),
        (0x7A744, 'mov', 'x22, x0'),
        (0x7A748, 'bl', '#0xb20c4'),
        (0x7A74C, 'ldr', 'x25, [x24]'),
        (0x7A750, 'ldrb', 'w9, [x25, #0x1df]'),
        (0x7A754, 'ldr', 'w8, [x25, #0xd4]'),
        (0x7A758, 'cbz', 'w9, #0x7a7a0'),
        (0x7A75C, 'cmp', 'w8, #0x1c'),
        (0x7A760, 'b.le', '#0x7a784'),
        (0x7A764, 'ldrb', 'w8, [x25, #0x1e3]'),
        (0x7A768, 'cbnz', 'w8, #0x7a7a8'),
        (0x7A76C, 'mov', 'x0, x22'),
        (0x7A770, 'mov', 'x1, x19'),
        (0x7A774, 'mov', 'x2, x21'),
        (0x7A778, 'mov', 'x3, x20'),
        (0x7A77C, 'bl', '#0xaf374'),
        (0x7A780, 'b', '#0x7a71c'),
        (0x7A7A0, 'cmp', 'w8, #0x19'),
        (0x7A7A4, 'b.le', '#0x7a7c8'),
        (0x7A7A8, 'ldrb', 'w8, [x25, #0x360]'),
        (0x7A7AC, 'cbz', 'w8, #0x7a82c'),
        (0x7A7B0, 'mov', 'x0, x22'),
        (0x7A7B4, 'mov', 'x1, x19'),
        (0x7A7B8, 'mov', 'x2, x21'),
        (0x7A7BC, 'mov', 'x3, x20'),
        (0x7A7C0, 'bl', '#0xb1db4'),
        (0x7A7C4, 'b', '#0x7a71c'),
        (0x7A82C, 'mov', 'x0, x22'),
        (0x7A830, 'mov', 'x1, x19'),
        (0x7A834, 'mov', 'x2, x21'),
        (0x7A838, 'mov', 'x3, x20'),
        (0x7A83C, 'bl', '#0xaee74'),
    ],
    'protected_preparation_wrapper': [
        (0xB20C4, 'stp', 'x29, x30, [sp, #-0x10]!'),
        (0xB20C8, 'mov', 'x29, sp'),
        (0xB20CC, 'bl', '#0xe2d48'),
        (0xB20D0, 'adrp', 'x1, #0x118000'),
        (0xB20D4, 'adrp', 'x2, #0x118000'),
        (0xB20D8, 'adrp', 'x3, #0x118000'),
        (0xB20DC, 'adrp', 'x4, #0x119000'),
        (0xB20E0, 'add', 'x1, x1, #0xa40'),
        (0xB20E4, 'add', 'x2, x2, #0xac0'),
        (0xB20E8, 'add', 'x3, x3, #0xb30'),
        (0xB20EC, 'add', 'x4, x4, #0x19c'),
        (0xB20F0, 'mov', 'w5, wzr'),
        (0xB20F4, 'ldp', 'x29, x30, [sp], #0x10'),
        (0xB20F8, 'b', '#0xe2e00'),
    ],
    'byte_array_loader_strategy': [
        (0xAF3A8, 'ldr', 'x8, [x0]'),
        (0xAF3AC, 'str', 'x1, [sp]'),
        (0xAF3B0, 'adrp', 'x1, #0x109000'),
        (0xAF3B4, 'add', 'x1, x1, #0x3a0'),
        (0xAF3B8, 'ldr', 'x8, [x8, #0x30]'),
        (0xAF3BC, 'blr', 'x8'),
        (0xAF3C0, 'adrp', 'x28, #0xf8000'),
        (0xAF3C4, 'ldr', 'x28, [x28, #0xab0]'),
        (0xAF3C8, 'ldr', 'x9, [x19]'),
        (0xAF3CC, 'mov', 'x2, x0'),
        (0xAF3D0, 'mov', 'x0, x19'),
        (0xAF3D4, 'ldr', 'x8, [x28]'),
        (0xAF3D8, 'mov', 'x3, xzr'),
        (0xAF3DC, 'ldr', 'w23, [x8, #0x190]'),
        (0xAF3E0, 'ldr', 'x8, [x9, #0x560]'),
        (0xAF3E4, 'mov', 'w1, w23'),
        (0xAF3E8, 'blr', 'x8'),
        (0xAF4A0, 'ldr', 'x9, [x8, #0x128]'),
        (0xAF4A4, 'ldr', 'x0, [x8, #0x10]'),
        (0xAF4A8, 'ldr', 'x9, [x9, #0x58]'),
        (0xAF4AC, 'ldr', 'x8, [x0]'),
        (0xAF4B0, 'ldr', 'x9, [x9, x22, lsl #3]'),
        (0xAF4B4, 'ldr', 'x8, [x8, #0x580]'),
        (0xAF4B8, 'ldr', 'x27, [x9, #0x28]'),
        (0xAF4BC, 'ldr', 'w1, [x27, #0x20]'),
        (0xAF4C0, 'blr', 'x8'),
        (0xAF4C4, 'ldr', 'x8, [x19]'),
        (0xAF4C8, 'ldr', 'w3, [x27, #0x20]'),
        (0xAF4CC, 'mov', 'x26, x0'),
        (0xAF4D0, 'mov', 'x0, x19'),
        (0xAF4D4, 'ldr', 'x8, [x8, #0x680]'),
        (0xAF4D8, 'mov', 'x1, x26'),
        (0xAF4DC, 'mov', 'w2, wzr'),
        (0xAF4E0, 'mov', 'x4, x27'),
        (0xAF4E4, 'blr', 'x8'),
        (0xAF4E8, 'ldr', 'x8, [x28]'),
        (0xAF4EC, 'ldr', 'w7, [x27, #0x20]'),
        (0xAF4F0, 'add', 'x1, sp, #0x10'),
        (0xAF4F4, 'mov', 'x0, x19'),
        (0xAF4F8, 'ldr', 'x8, [x8, #0x20]'),
        (0xAF4FC, 'mov', 'x2, x23'),
        (0xAF500, 'mov', 'x3, x24'),
        (0xAF504, 'mov', 'x4, x25'),
        (0xAF508, 'ldr', 'x8, [x8, #0x80]'),
        (0xAF50C, 'mov', 'x5, x26'),
        (0xAF510, 'mov', 'w6, wzr'),
        (0xAF514, 'blr', 'x8'),
        (0xAF518, 'ldr', 'x8, [x19]'),
        (0xAF51C, 'ldr', 'x3, [sp, #0x10]'),
        (0xAF520, 'mov', 'x0, x19'),
        (0xAF524, 'mov', 'x1, x21'),
        (0xAF528, 'ldr', 'x8, [x8, #0x570]'),
        (0xAF52C, 'mov', 'w2, w22'),
        (0xAF530, 'blr', 'x8'),
        (0xAF56C, 'ldr', 'x8, [x8, #0x20]'),
        (0xAF570, 'ldr', 'x4, [sp]'),
        (0xAF574, 'adrp', 'x1, #0x109000'),
        (0xAF578, 'adrp', 'x2, #0x109000'),
        (0xAF57C, 'ldr', 'x8, [x8, #0xb0]'),
        (0xAF580, 'add', 'x1, x1, #0x490'),
        (0xAF584, 'add', 'x2, x2, #0x4c0'),
        (0xAF588, 'mov', 'x0, x19'),
        (0xAF58C, 'mov', 'x3, x21'),
        (0xAF590, 'blr', 'x8'),
        (0xAF594, 'mov', 'x20, x0'),
        (0xAF598, 'cbz', 'x0, #0xaf5b8'),
        (0xAF59C, 'ldr', 'x8, [x19]'),
        (0xAF5A0, 'mov', 'x0, x19'),
        (0xAF5A4, 'mov', 'x1, x20'),
        (0xAF5A8, 'ldr', 'x8, [x8, #0xa8]'),
        (0xAF5AC, 'blr', 'x8'),
        (0xAF5B0, 'ldr', 'x8, [x28]'),
        (0xAF5B4, 'str', 'x0, [x8, #0x1e8]'),
    ],
    'direct_buffer_strategy': [
        (0xAEFA0, 'ldr', 'x8, [x24, #0x128]'),
        (0xAEFA4, 'mov', 'x0, x23'),
        (0xAEFA8, 'ldr', 'x8, [x8, #0x58]'),
        (0xAEFAC, 'ldr', 'x8, [x8, x25, lsl #3]'),
        (0xAEFB0, 'ldr', 'x1, [x8, #0x28]'),
        (0xAEFB4, 'ldr', 'x8, [x23]'),
        (0xAEFB8, 'ldr', 'w2, [x1, #0x20]'),
        (0xAEFBC, 'ldr', 'x8, [x8, #0x728]'),
        (0xAEFC0, 'blr', 'x8'),
        (0xAEFC4, 'ldr', 'x8, [x23]'),
        (0xAEFC8, 'mov', 'x26, x0'),
        (0xAEFCC, 'mov', 'x0, x23'),
        (0xAEFD0, 'mov', 'x1, x20'),
        (0xAEFD4, 'ldr', 'x8, [x8, #0x570]'),
        (0xAEFD8, 'mov', 'w2, w25'),
        (0xAEFDC, 'mov', 'x3, x26'),
        (0xAEFE0, 'blr', 'x8'),
        (0xAEFE4, 'ldr', 'x8, [x23]'),
        (0xAEFE8, 'mov', 'x0, x23'),
        (0xAEFEC, 'mov', 'x1, x26'),
        (0xAEFF0, 'ldr', 'x8, [x8, #0xb8]'),
        (0xAEFF4, 'blr', 'x8'),
        (0xAF068, 'adrp', 'x2, #0x109000'),
        (0xAF06C, 'adrp', 'x3, #0x109000'),
        (0xAF070, 'adrp', 'x4, #0x109000'),
        (0xAF074, 'ldr', 'x8, [x8, #0x20]'),
        (0xAF078, 'add', 'x2, x2, #0x340'),
        (0xAF07C, 'add', 'x3, x3, #0x3f0'),
        (0xAF080, 'add', 'x4, x4, #0x440'),
        (0xAF084, 'ldr', 'x8, [x8, #0x80]'),
        (0xAF088, 'sub', 'x1, x29, #0x10'),
        (0xAF08C, 'mov', 'x0, x23'),
        (0xAF090, 'mov', 'x5, x20'),
        (0xAF094, 'mov', 'x6, x21'),
        (0xAF098, 'blr', 'x8'),
    ],
    'asset_transform_candidate': [
        (0xB6B64, 'add', 'x0, sp, #0x18'),
        (0xB6B68, 'sub', 'x1, x29, #0xc'),
        (0xB6B6C, 'mov', 'w2, wzr'),
        (0xB6B70, 'stur', 'x8, [x29, #-8]'),
        (0xB6B74, 'stur', 'wzr, [x29, #-0xc]'),
        (0xB6B78, 'str', 'xzr, [sp, #0x18]'),
        (0xB6B7C, 'bl', '#0xb69e8'),
        (0xB6B80, 'mov', 'w22, wzr'),
        (0xB6B84, 'tbz', 'w0, #0, #0xb6c40'),
        (0xB6B88, 'ldr', 'x25, [sp, #0x18]'),
        (0xB6B8C, 'ldur', 'w8, [x29, #-0xc]'),
        (0xB6B90, 'add', 'x0, sp, #0x10'),
        (0xB6B94, 'mov', 'w2, #1'),
        (0xB6B98, 'add', 'x22, x25, #0x2c'),
        (0xB6B9C, 'sub', 'w1, w8, #0x2c'),
        (0xB6BA0, 'str', 'x22, [sp, #0x10]'),
        (0xB6BA4, 'bl', '#0xb6d44'),
        (0xB6BA8, 'ldr', 'w0, [x25, #8]'),
        (0xB6BAC, 'mov', 'w1, #0x1000'),
        (0xB6BB0, 'bl', '#0x80038'),
        (0xB6BB4, 'mov', 'x1, x0'),
        (0xB6BB8, 'mov', 'w2, #3'),
        (0xB6BBC, 'mov', 'w3, #0x21'),
        (0xB6BC0, 'mov', 'w4, #-1'),
        (0xB6BC4, 'mov', 'x0, xzr'),
        (0xB6BC8, 'mov', 'x5, xzr'),
        (0xB6BCC, 'bl', '#0xeead0'),
        (0xB6BD0, 'ldr', 'w8, [x25, #8]'),
        (0xB6BD4, 'add', 'x1, sp, #8'),
        (0xB6BD8, 'mov', 'x2, x22'),
        (0xB6BDC, 'mov', 'x23, x0'),
        (0xB6BE0, 'str', 'x8, [sp, #8]'),
        (0xB6BE4, 'ldr', 'w3, [x25, #4]'),
        (0xB6BE8, 'bl', '#0x92104'),
        (0xB6BEC, 'cmp', 'w0, #0'),
        (0xB6BF0, 'cset', 'w22, eq'),
        (0xB6BF4, 'cbnz', 'w0, #0xb6c40'),
        (0xB6BF8, 'str', 'x23, [x21]'),
        (0xB6BFC, 'ldr', 'w8, [x25, #8]'),
        (0xB6C00, 'adrp', 'x9, #0xf8000'),
        (0xB6C04, 'ldr', 'x9, [x9, #0xab0]'),
        (0xB6C08, 'str', 'w8, [x20]'),
        (0xB6C0C, 'ldr', 'x9, [x9]'),
        (0xB6C10, 'str', 'x23, [x9, #0x2b8]'),
        (0xB6C14, 'str', 'w8, [x9, #0x2c0]'),
        (0xB6A54, 'ldr', 'x8, [x8, #0x2d0]'),
        (0xB6A58, 'mov', 'x0, sp'),
        (0xB6A5C, 'mov', 'w1, #0x40'),
        (0xB6A60, 'ldr', 'x3, [x8, #0x28]'),
        (0xB6A64, 'bl', '#0xae000'),
        (0xB6A68, 'ldr', 'x8, [x23]'),
        (0xB6A6C, 'ldrb', 'w10, [x8, #0x1df]'),
        (0xB6A70, 'ldr', 'x9, [x8, #0x48]'),
        (0xB6A74, 'cbz', 'w21, #0xb6a9c'),
        (0xB6A78, 'cbz', 'w10, #0xb6abc'),
        (0xB6A7C, 'ldr', 'x9, [x9, #0x80]'),
        (0xB6A80, 'ldr', 'x0, [x8, #0x80]'),
        (0xB6A84, 'mov', 'x1, sp'),
        (0xB6A88, 'mov', 'x2, x20'),
        (0xB6A8C, 'mov', 'x3, x19'),
        (0xB6A90, 'mov', 'w4, w21'),
        (0xB6A94, 'blr', 'x9'),
        (0xB6D8C, 'ldr', 'x13, [x11, #0x160]'),
        (0xB6D90, 'add', 'x14, x12, x10'),
        (0xB6D94, 'ldrb', 'w15, [x14, #1]'),
        (0xB6D98, 'add', 'x13, x13, x10'),
        (0xB6D9C, 'ldrb', 'w13, [x13, #3]'),
        (0xB6DA0, 'add', 'x10, x10, #1'),
        (0xB6DA4, 'cmp', 'x10, #0x1a'),
        (0xB6DA8, 'eor', 'w13, w13, w15'),
        (0xB6DAC, 'strb', 'w13, [x14]'),
        (0xB6DB0, 'b.ne', '#0xb6d8c'),
        (0x9214C, 'adrp', 'x1, #0x27000'),
        (0x92150, 'add', 'x1, x1, #0x285'),
        (0x92154, 'add', 'x0, sp, #8'),
        (0x92158, 'mov', 'w2, #0x70'),
        (0x9215C, 'stp', 'xzr, xzr, [sp, #0x48]'),
        (0x92160, 'bl', '#0x8e250'),
        (0x92164, 'mov', 'w19, w0'),
        (0x92168, 'cbnz', 'w0, #0x9219c'),
        (0x9216C, 'add', 'x0, sp, #8'),
        (0x92170, 'mov', 'w1, #4'),
        (0x92174, 'bl', '#0x8e3e4'),
        (0x92178, 'cmp', 'w0, #1'),
        (0x9217C, 'b.ne', '#0x921c4'),
        (0x92180, 'ldr', 'x8, [sp, #0x30]'),
        (0x92184, 'add', 'x0, sp, #8'),
        (0x92188, 'str', 'x8, [x20]'),
        (0x9218C, 'bl', '#0x8ffd8'),
        (0x92190, 'mov', 'w19, w0'),
        (0x92194, 'b', '#0x9219c'),
    ],
    'new_prefix_widths': [
        (0xC05FC, 'ldr', 'x8, [sp, #0x820]'),
        (0xC0600, 'ldrb', 'w8, [x8]'),
        (0xC0604, 'ldr', 'x9, [sp, #0x820]'),
        (0xC0608, 'ldrb', 'w9, [x9, #1]'),
        (0xC060C, 'ldr', 'x10, [sp, #0x38]'),
        (0xC0610, 'bfi', 'w8, w9, #8, #0x18'),
        (0xC0614, 'ldr', 'x10, [x10]'),
        (0xC0618, 'mov', 'x9, #-1'),
        (0xC061C, 'stp', 'x10, x9, [sp, #0x88]'),
        (0xC0620, 'ldr', 'x10, [sp, #0x88]'),
        (0xC0624, 'strh', 'w8, [sp, #0x9c]'),
        (0xC0628, 'ldrsh', 'x8, [sp, #0x9c]'),
        (0xC062C, 'adrp', 'x9, #0x2b000'),
        (0xC0630, 'lsl', 'x8, x8, #2'),
        (0xC0634, 'str', 'x8, [sp, #0xa0]'),
        (0xC0638, 'ldr', 'x8, [sp, #0x90]'),
        (0xC063C, 'ldr', 'w9, [x9, #0x28c]'),
        (0xC0640, 'cmn', 'x8, #1'),
        (0xC0644, 'cinc', 'w8, w9, eq'),
        (0xC0648, 'ldr', 'x8, [x24, w8, sxtw #3]'),
        (0xC072C, 'eor', 'w8, w13, w8'),
        (0xC0730, 'eor', 'w8, w8, w11'),
        (0xC0748, 'ldr', 'x10, [sp, #0x820]'),
        (0xC074C, 'ldrb', 'w8, [sp, #0xb0]'),
        (0xC0750, 'ldrb', 'w9, [sp, #0xb4]'),
        (0xC0754, 'add', 'x10, x10, #3'),
        (0xC0758, 'b', '#0xbd8ac'),
        (0xBEA64, 'ldr', 'x8, [sp, #0x820]'),
        (0xBEA68, 'adrp', 'x10, #0x2b000'),
        (0xBEA6C, 'ldrb', 'w8, [x8]'),
        (0xBEA70, 'ldr', 'x9, [sp, #8]'),
        (0xBEA74, 'ldr', 'x9, [x9]'),
        (0xBEA78, 'str', 'w8, [x9], #4'),
        (0xBEA7C, 'ldr', 'x8, [sp, #8]'),
        (0xBEA80, 'str', 'x9, [x8]'),
        (0xBEA84, 'ldr', 'x8, [sp, #0x820]'),
        (0xBEA88, 'ldrb', 'w9, [sp, #0x82c]'),
        (0xBEA8C, 'ldrb', 'w8, [x8, #1]'),
        (0xBEAF4, 'eor', 'w8, w13, w8'),
        (0xBEAF8, 'eor', 'w8, w8, w11'),
        (0xBEB10, 'ldr', 'x10, [sp, #0x820]'),
        (0xBEB14, 'ldrb', 'w8, [sp, #0xf4]'),
        (0xBEB18, 'ldrb', 'w9, [sp, #0xf8]'),
        (0xBEB1C, 'b', '#0xbd8a8'),
        (0xC1D78, 'ldr', 'x8, [sp, #8]'),
        (0xC1D7C, 'adrp', 'x10, #0x2b000'),
        (0xC1D80, 'ldr', 'x8, [x8]'),
        (0xC1D84, 'ldur', 'x9, [x8, #-8]'),
        (0xC1D88, 'ldrb', 'w9, [x9]'),
        (0xC1D8C, 'sturb', 'w9, [x8, #-8]'),
        (0xC1D90, 'ldr', 'x8, [sp, #8]'),
        (0xC1D94, 'ldr', 'x8, [x8]'),
        (0xC1D98, 'ldr', 'x9, [sp, #8]'),
        (0xC1D9C, 'sub', 'x8, x8, #4'),
        (0xC1DA0, 'str', 'x8, [x9]'),
        (0xC1DA4, 'ldrb', 'w8, [sp, #0x82c]'),
        (0xC1DA8, 'ldr', 'x9, [sp, #0x820]'),
        (0xC1E14, 'eor', 'w8, w13, w8'),
        (0xC1E18, 'eor', 'w8, w8, w11'),
        (0xC1E30, 'ldr', 'x10, [sp, #0x820]'),
        (0xC1E34, 'ldrb', 'w8, [sp, #0x200]'),
        (0xC1E38, 'ldrb', 'w9, [sp, #0x204]'),
        (0xC1E3C, 'add', 'x10, x10, #1'),
        (0xC1E40, 'b', '#0xbd8ac'),
    ],
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def header_slots(data: bytes) -> dict:
    vm.expect(digest(data), JNI_SHA, "local JNI header SHA-256")
    body = data.decode().split("struct JNINativeInterface_ {", 1)[1].split("\n};", 1)[0]
    names = re.findall(r"\(JNICALL \*(\w+)\)", body)
    vm.expect(len(re.findall(r"void \*reserved\d+;", body)), 4, "JNI reserved slots")
    result = {name: (4 + names.index(name)) * 8 for name in JNI_SLOTS}
    vm.expect(result, JNI_SLOTS, "selected 64-bit JNI table slots")
    return {name: hex(offset) for name, offset in result.items()}


def protected_prefix(record: bytes, image: bytes) -> dict:
    vm.expect(len(record), RECORD_BYTES, "container record length")
    vm.expect(record[0], 0x3B, "container marker")
    key = record[1] ^ 0x5F
    words = (vm.byte_transform(record[2]) ^ key) | ((vm.byte_transform(record[3]) ^ key) << 8)
    vm.expect(words, 0, "container initial frame words")
    handlers = dict(record_two.HANDLERS)
    handlers.update(NEW_HANDLERS)
    position, key, last_opcode = 4, record[1], None
    tokens = []
    while len(tokens) < MAX_PREFIX_TOKENS:
        raw = record_two.read_record(record, position, 1)[0]
        special = (last_opcode is not None and ((last_opcode + 0x7B) & 255) < 5 and raw == 0x3B)
        opcode = 0x3B if special else vm.byte_transform(raw) ^ key ^ 0x5F
        if opcode not in handlers:
            raise ValueError("Unsupported handler in the bounded prefix")
        width, handler, _, meaning = handlers[opcode]
        vm.expect(vm.pointer(image, 0xF3AF0 + opcode * 8), handler, "prefix handler target")
        operand_data = record_two.read_record(record, position + 1, width)
        operand = int.from_bytes(operand_data, "little") if width else None
        tokens.append({"offset": position, "handler_index": hex(opcode),
                       "operand_bytes": width, "operand": operand, "meaning": meaning})
        if opcode in (1, 0x85, 0x86):
            vm.expect((position, opcode, operand), (79, 0x86, 19), "prefix terminal branch")
            destination = position + int.from_bytes(operand_data, "little", signed=True)
            record_two.read_record(record, destination, 1)
            end = position + 1 + width
            record_two.read_record(record, end, 1)
            return {"tokens": tokens, "covered_body_bytes": end - 4,
                    "prefix_end_exclusive": end, "prefix_sha256": digest(record[:end]),
                    "branch_successors": {"byte_nonzero": destination, "byte_zero": end},
                    "condition": "symbolic runtime context byte at +0x370 equals zero",
                    "remaining_body_bytes_not_parsed": len(record) - end}
        key = operand if opcode == 0x3B else opcode
        last_opcode = opcode
        position += 1 + width
    raise ValueError("Static prefix exceeds token bound")


def inspect(image: bytes, decoded: bytes, elf, jni: bytes) -> dict:
    slots = header_slots(jni)
    rw = [(s[3], s[3] + s[6]) for s in elf.loads if s[1] & 2]
    relocated, _, _, _ = strings.relocate(elf, image, [(0, vm.RX_END), *rw], rw)
    bounds = strings.function_bounds(elf, relocated, vm.RX_END)
    engine = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    functions = []
    for start in FUNCTIONS:
        if start not in bounds:
            raise ValueError("Selected function lacks supported FDE bounds")
        lo, hi = bounds[start]
        data = vm.read(relocated, lo, hi - lo)
        code = list(engine.disasm(data, lo))
        vm.expect(sum(i.size for i in code), len(data), "complete function decode")
        functions.append({"start": hex(lo), "end_exclusive": hex(hi), "bytes": len(data),
                          "sha256": digest(data), "direct_calls": sum(i.mnemonic == "bl" for i in code),
                          "indirect_calls": sum(i.mnemonic == "blr" for i in code),
                          "system_call_instructions": sum(i.mnemonic == "svc" for i in code)})
    if not CHECKS:
        raise ValueError("Static instruction identities are not finalized")
    for label, checks in CHECKS.items():
        for address, mnemonic, operands in checks:
            code = list(engine.disasm(vm.read(relocated, address, 4), address))
            vm.expect(len(code), 1, label + " instruction count")
            vm.expect((code[0].mnemonic, code[0].op_str), (mnemonic, operands), label)
    literals = []
    for offset, expected in LITERALS.items():
        data = expected.encode() + b"\0"
        vm.expect(vm.read(decoded, offset, len(data)), data, "selected loader literal")
        literals.append({"offset": hex(offset), "value": expected})
    vm.expect(struct.unpack("<ii", vm.read(relocated, RECORD_TABLE, 8)), (0, RECORD_BYTES), "container descriptor")
    record = vm.read(relocated, RECORD_START, RECORD_BYTES)
    vm.expect(digest(record), RECORD_SHA, "container record SHA-256")
    prefix = protected_prefix(record, relocated)
    vm.expect(vm.pointer(relocated, 0x118A40), 0xFD220, "context pointer table entry")
    widths = []
    for index, (width, start, end, meaning) in NEW_HANDLERS.items():
        if not 0xBD624 <= start < end <= 0xC472C:
            raise ValueError("Handler leaves interpreter bounds")
        data = vm.read(relocated, start, end - start)
        code = list(engine.disasm(data, start))
        vm.expect(sum(i.size for i in code), len(data), "handler complete decode")
        widths.append({"handler_index": hex(index), "operand_bytes": width, "start": hex(start),
                       "end_exclusive": hex(end), "meaning": meaning, "sha256": digest(data)})
    return {"scope": "Static boundaries only; no VM/JNI/native execution", "guest_instructions_executed": 0,
            "jni_table_slots": slots, "selected_native_functions": functions, "selected_literals": literals,
            "instruction_checks": {label: {"count": len(checks), "addresses": [hex(x[0]) for x in checks]} for label, checks in CHECKS.items()},
            "protected_preparation": {"wrapper": "0xb20c4", "calls": "0x118a40", "thunks": "0x118ac0",
                "records": "0x118b30", "descriptor_table": "0x11919c", "selector": 0,
                "record_bytes": RECORD_BYTES, "record_sha256": RECORD_SHA, "prefix": prefix, "new_handler_widths": widths},
            "buffer_routes": [
                {"function": "0xaf374", "byte_array_allocation": "0xaf4c0", "copy_call": "0xaf4e4",
                 "source": "context+0x128 -> +0x58 -> record[i]+0x28",
                 "length": "uint32 source+0x20; not validated as DEX file_size",
                 "buffer_helper": "context+0x20 helper+0x80 at0xaf514; ByteBuffer.wrap([BII)",
                 "loader_helper": "context+0x20 helper+0xb0 at0xaf590",
                 "arguments": ["ByteBuffer[]", "original parent ClassLoader"],
                 "constructor_signature": LITERALS[0x1094C0], "global_reference_destination": "context+0x1e8"},
                {"function": "0xaee74", "buffer_call": "0xaefc0",
                 "operation": "NewDirectByteBuffer(native source, uint32 source+0x20)",
                 "source": "same runtime native record-list path", "later_helper": "makeInMemoryDexElements"}],
            "asset_transform_candidate": {"caller": "0xb6b34", "reader": "0xb69e8",
                "filename": "format assets/%s or %s with runtime descriptor+0x28; actual field unresolved",
                "reader_interfaces": "runtime context+0x48 callbacks; raw-reader identity unproved",
                "payload_offset": "0x2c", "transform": "0xb6d44(payload,len,1)",
                "key_input": "runtime context+0x160; 26 derived bytes; provenance unresolved",
                "inflate_like_call": "0x92104(output,&length,payload,uint32 header+0x04)",
                "output_size_source": "uint32 header+0x08", "native_result": "context+0x2b8/+0x2c0",
                "limits": "Candidate native route only; no protected-record call path or successful plaintext result established"},
            "limits": ["No Android/JNI/VM/native method or initializer executed", "Record not parsed past branch at79",
                       "No runtime descriptor/callback/helper identities or key provenance resolved",
                       "No successful buffer creation, plaintext DEX or SDK charging-pause serializer recovered", "No model/firmware equivalence"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base-apk", "images-dir", "initialized-image", "jni-header", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    if args.base_apk.stat().st_size > 256 * 1024 * 1024:
        raise ValueError("APK exceeds input bound")
    apk = args.base_apk.read_bytes()
    vm.expect(digest(apk), carrier.APK_SHA256, "APK SHA-256")
    image_path = args.images_dir / vm.IMAGE_NAME
    vm.expect(image_path.stat().st_size, vm.IMAGE_SIZE, "carrier image size")
    vm.expect(args.initialized_image.stat().st_size, vm.IMAGE_SIZE, "initialized image size")
    if args.jni_header.stat().st_size > 256 * 1024:
        raise ValueError("JNI header exceeds input bound")
    image, decoded, jni = image_path.read_bytes(), args.initialized_image.read_bytes(), args.jni_header.read_bytes()
    vm.expect(digest(image), vm.IMAGE_SHA, "carrier image SHA-256")
    vm.expect(digest(decoded), record_two.DECODED_SHA, "initialized image SHA-256")
    vm.expect(digest(jni), JNI_SHA, "JNI header SHA-256")
    with zipfile.ZipFile(BytesIO(apk)) as archive:
        info = archive.getinfo(vm.LIBRARY)
        if info.file_size > 2 * 1024 * 1024:
            raise ValueError("Packed library exceeds input bound")
        packed = archive.read(vm.LIBRARY)
    vm.expect(digest(packed), carrier.LIBRARIES[vm.LIBRARY], "packed library SHA-256")
    result = inspect(image, decoded, carrier.LoadElf(packed), jni)
    sources = (Path(__file__), Path(vm.__file__), Path(strings.__file__), Path(carrier.__file__), Path(record_two.__file__))
    manifest = {"tool": Path(__file__).name, "apk_sha256": digest(apk), "packed_library_sha256": digest(packed),
                "image_sha256": digest(image), "initialized_image_sha256": digest(decoded), "jni_header_sha256": digest(jni),
                "source_sha256": {p.name: digest(p.read_bytes()) for p in sources},
                "runtime": {"python": platform.python_version(), "capstone": capstone.__version__}, "guest_instructions_executed": 0}
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    for suffix, value in (("results", result), ("manifest", manifest)):
        p = args.output_dir / f"android-loader-classloader-{suffix}.json"
        p.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
        p.chmod(0o600)
    print(json.dumps({"selected_functions": len(FUNCTIONS), "instruction_checks": sum(map(len, CHECKS.values())),
                      "prefix_tokens": len(result["protected_preparation"]["prefix"]["tokens"]), "guest_instructions_executed": 0}))


if __name__ == "__main__":
    main()
