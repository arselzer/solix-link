#!/usr/bin/env python3
"""Bounded A1763 radio asset admission, callbacks and chunk serialization.

Actual RISC-V handler, TLV lookup/builders, download-helper setup and selected
progress/completion paths execute with synthetic parsed requests. Allocation,
libc, mutexes, tasks, time, HTTP cancellation, outer framing/delivery, ACKs and
logging are explicit substitutes. No ingress parser/session, HTTP task, DNS,
TLS, filesystem, controller, firmware boot, device or network runs. Callback
buffer length and padding are synthetic assumptions, not an electrical test.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import struct

import unicorn
from unicorn import riscv_const as R

from emulate_radio_update_status import Machine as Base, SHA256, STACK, STOP

GLOBAL = 0x3fc905f4
CHUNK, LIMIT = 0x3fc82a24, 0x3fc82a20
ALLOW = (
    (0x4203f4ee, 0x4203fa5e),  # internal 003d admission
    (0x4204f95c, 0x4204f9a6),  # parsed TLV lookup
    (0x4203bab4, 0x4203bc6e),  # download-helper setup, task boundary excluded
    (0x4203f088, 0x4203f4ee),  # completion/progress callbacks
    (0x4203ef1a, 0x4203f088),  # pending/current locator handover
    (0x4203e15c, 0x4203e1a4),  # asynchronous 083d status descriptor
    (0x4204dd4c, 0x4204ddf4),  # short TLV builder and wrapper
    (0x4204e04a, 0x4204e0a0),  # long TLV builder
    (0x4202d746, 0x4202d754),  # mutex-initialized getter
)


class Machine(Base):
    def __init__(self, image: bytes, *, task_result: int = 0, mutex_result: int = 0,
                 ack: int = 1, allocation_failure: bool = False) -> None:
        super().__init__(image)
        self.uc.mem_map(0x20001000, 0x1f000)
        self.heap = 0x20000000
        self.task_result, self.mutex_result = task_result, mutex_result
        self.ack, self.allocation_failure = ack, allocation_failure
        self.tick, self.total_length = 1000, 2048
        self.tasks, self.frames, self.cancellations = [], [], []
        self.allocations, self.delays = [], []
        self.visited, self.reads = set(), set()
        self.input_region: tuple[int, int] | None = None
        self.input_read_extent = 0
        self.context = 0
        self.uc.mem_write(GLOBAL, bytes(28))
        self.uc.mem_write(LIMIT, struct.pack("<IH", 180000, 1024))
        self.protected_regions = ((0x3fc89eb4, 0x3fc8a12c), (0x3fc8ab70, 0x3fc8aeb8))
        self.protected_before = [self.read(lo, hi - lo) for lo, hi in self.protected_regions]
        for address, size in ((0x3c130000, 0x40000), (0x42000000, 0x200000),
                              (0x40380000, 0x10000), (0x40000000, 0x1000), (STOP, 0x1000)):
            self.uc.mem_protect(address, size, unicorn.UC_PROT_READ | unicorn.UC_PROT_EXEC)
        self.uc.hook_add(unicorn.UC_HOOK_MEM_READ, self.track_read)

    def alloc(self, value: bytes | int) -> int:
        raw = bytes(value) if type(value) is int else value
        assert len(raw) <= 8192
        address = self.heap
        self.heap += max(16, (len(raw) + 15) & ~15)
        assert self.heap <= 0x20020000
        self.uc.mem_write(address, raw)
        return address

    def text(self, address: int, maximum: int = 513) -> bytes:
        # The admitted locator has at most 512 bytes plus its copied terminator.
        raw = self.read(address, maximum)
        assert b"\0" in raw, "Unterminated bounded text substitute"
        return raw.split(b"\0", 1)[0]

    def track_read(self, uc, access, address, size, value, data) -> None:
        self.reads.update(range(address, address + size))
        if self.input_region:
            lo, length = self.input_region
            if lo <= address < lo + length + 2048:
                self.input_read_extent = max(self.input_read_extent, address + size - lo)

    def write_guard(self, uc, access, address, size, value, data) -> None:
        allowed = ((0x20000000, 0x20020000), (STACK - 0x2000, STACK),
                   (GLOBAL, GLOBAL + 28), (LIMIT, CHUNK + 2),
                   (0x3fc90598, 0x3fc9059c), (0x3fc8b154, 0x3fc8b168))
        assert any(lo <= address < address + size <= hi for lo, hi in allowed), (
            f"Unexpected write {address:08x} at {uc.reg_read(R.UC_RISCV_REG_PC):08x}")

    def step(self, uc, address, size, data) -> None:
        self.visited.add(address)
        args = [uc.reg_read(R.UC_RISCV_REG_A0 + index) for index in range(8)]
        a0, a1, a2, a3, a4 = args[:5]
        if address == STOP:
            self.stopped = True
            uc.emu_stop()
            return
        if address in (0x4202d3b0, 0x420232b8, 0x42023636, 0x421271ac, 0x421270b8):
            result = 0
        elif address == 0x40000360:
            assert a2 <= 512
            left, right = self.read(a0, a2), self.read(a1, a2)
            result = (left > right) - (left < right)
        elif address == 0x40000404:
            assert a1 <= 512
            raw = self.read(a0, a1)
            result = len(raw.split(b"\0", 1)[0])
        elif address == 0x40000358:
            assert a2 <= 2048
            self.write_guard(uc, 0, a0, a2, 0, None)
            if self.input_region:
                lo, length = self.input_region
                if lo <= a1 < lo + length + 2048:
                    self.input_read_extent = max(self.input_read_extent, a1 + a2 - lo)
            uc.mem_write(a0, self.read(a1, a2))
            result = a0
        elif address == 0x42017e00:
            assert a0 <= 4096
            self.allocations.append(a0)
            result = 0 if self.allocation_failure else self.alloc(a0)
        elif address == 0x42017e4e:
            result = 0  # Allocated memory remains mapped; no real allocator.
        elif address == 0x4202d6d2:
            assert a0 == GLOBAL + 4
            uc.mem_write(a0, struct.pack("<I", 1))
            result = 0
        elif address in (0x4202d6f8, 0x4202d71c):
            result = self.mutex_result if address == 0x4202d6f8 else 0
        elif address == 0x42013a2a:
            assert args[:3] == [20, 1, 1]
            assert a3 >= 0 and a4 == 0 and args[5:] == [1, 0, 0]
            callbacks = struct.unpack("<5I", self.read(0x3fc8b154, 20))
            assert callbacks[:2] == (0x4203f2a6, 0x4203f088)
            locator = self.text(self.word(0x3fc90598))
            self.tasks.append({"task_id_raw": a0, "start_offset": a3,
                               "callback_targets": [f"{p:08x}" for p in callbacks[:2]],
                               "locator_bytes": len(locator),
                               "locator_sha256": hashlib.sha256(locator).hexdigest()})
            result = self.task_result
        elif address == 0x4202df98:
            result = self.tick
        elif address == 0x42016ef6:
            result = self.total_length
        elif address == 0x4204fa68:
            assert a0 == self.context and a2 == 1
            self.responses.append(self.read(a1, a2)[0])
            result = 0
        elif address == 0x4204fcae:
            assert a0 == 16 and a1 in (0x3e, 0x83d) and 0 < a3 <= 4096
            descriptor = self.read(a4, 16)
            assert struct.unpack_from("<I", descriptor, 4)[0] == 2
            body = self.read(a2, a3)
            if a1 == 0x3e:
                assert body[:2] == b"\xa1\4" and body[6:8] == b"\xa2\4" and body[12] == 0xa3
                assert int.from_bytes(body[13:15], "little") == a3 - 15
                assert int.from_bytes(descriptor[8:10], "little") == 1000
                assert int.from_bytes(descriptor[12:16], "little") == 0x4203c8fa
            else:
                assert a3 == 1
                assert int.from_bytes(descriptor[8:10], "little") == 300
                assert descriptor[12:16] == bytes(4)
            self.frames.append({"command": a1, "body": body})
            result = 0
        elif address == 0x4202df9c:
            self.delays.append(a0)
            assert a0 in (3, 500)
            uc.mem_write(GLOBAL + 16, bytes((self.ack,)))
            result = 0
        elif address == 0x42013848:
            assert a0 == 20
            self.cancellations.append("task_message_20")
            result = 0
        elif address == 0x4203bc6e:
            self.cancellations.append("http_cancel_substitute")
            result = 0
        elif any(lo <= address < hi for lo, hi in ALLOW):
            return
        else:
            raise AssertionError(f"Unexpected instruction {address:08x}")
        uc.reg_write(R.UC_RISCV_REG_A0, result & 0xffffffff)
        uc.reg_write(R.UC_RISCV_REG_PC, uc.reg_read(R.UC_RISCV_REG_RA))

    def run_entry(self, address: int, *args: int) -> int:
        for index, value in enumerate(args):
            self.uc.reg_write(R.UC_RISCV_REG_A0 + index, value)
        self.uc.reg_write(R.UC_RISCV_REG_SP, STACK)
        self.run(address)
        return self.uc.reg_read(R.UC_RISCV_REG_A0)

    def request(self, locator: bytes = b"https://example.invalid/asset", *, chunk: int = 1024,
                offset: int = 0, omit: int | None = None, timeout_seconds: int | None = None) -> None:
        assert len(locator) <= 513 and 0 <= chunk <= 65535
        body = b"\xa1\4" + struct.pack("<I", offset) + b"\xa2\2" + struct.pack("<H", chunk)
        body += b"\xa3" + struct.pack("<H", len(locator)) + locator
        if timeout_seconds is not None:
            body += b"\xa4\2" + struct.pack("<H", timeout_seconds)
        body += bytes(32)  # Synthetic initialized trailer; not a truncation test.
        pointer = self.alloc(body)
        self.context = self.alloc(24 + 255 * 6)
        for index, (tag, length, relative) in enumerate(((0xa1, 4, 2), (0xa2, 2, 8))):
            if omit != tag:
                self.uc.mem_write(self.context + 24 + index * 6,
                                  struct.pack("<BBI", tag, length, pointer + relative))
        self.run_entry(0x4203f4ee, self.context)

    def progress(self, length: int, *, kind: int = 0, elapsed: int = 0, canceled: bool = False) -> int:
        assert 0 <= length <= 4096
        raw = bytes((i * 7 + 11) & 255 for i in range(length)) + b"\x5a" * 2048
        pointer = self.alloc(raw)
        self.input_region = pointer, length
        self.input_read_extent = 0
        self.tick = 1000 + elapsed
        self.uc.mem_write(GLOBAL, struct.pack("<I", 1000))
        self.uc.mem_write(GLOBAL + 17, bytes((int(canceled),)))
        return self.run_entry(0x4203f2a6, pointer, length, 0, kind)

    def summary(self) -> dict:
        assert [self.read(lo, hi - lo) for lo, hi in self.protected_regions] == self.protected_before
        return {"reply_statuses": self.responses, "download_task_substitutes": self.tasks,
                "stored_chunk_size": int.from_bytes(self.read(CHUNK, 2), "little"),
                "stored_timeout_argument": self.word(LIMIT), "stored_forward_offset": self.word(GLOBAL + 20),
                "pending_locator_present": bool(self.word(GLOBAL + 8)),
                "current_locator_present": bool(self.word(GLOBAL + 12)),
                "async_status_bytes": [row["body"][0] for row in self.frames if row["command"] == 0x83d],
                "chunk_frames": [{"body_bytes": len(row["body"]),
                                  "offset_u32_le": int.from_bytes(row["body"][2:6], "little"),
                                  "total_u32_le": int.from_bytes(row["body"][8:12], "little"),
                                  "chunk_length_u16_le": int.from_bytes(row["body"][13:15], "little"),
                                  "body_sha256": hashlib.sha256(row["body"]).hexdigest()}
                                 for row in self.frames if row["command"] == 0x3e],
                "callback_input_read_extent": self.input_read_extent,
                "cancellation_substitutes": self.cancellations, "delay_substitute_calls": len(self.delays),
                "synthetic_identity_and_wifi_regions_preserved": True,
                "distinct_instruction_addresses": len(self.visited)}


def suite(image: bytes) -> dict:
    validate_image(image)
    rows = []
    for name, kwargs, expected in (
        ("https", {}, 0), ("http", {"locator": b"http://example.invalid/a"}, 0),
        ("unqualified_filename", {"locator": b"sysPara"}, 1),
        ("ftp_scheme", {"locator": b"ftp://example.invalid/a"}, 1),
        ("upper_case_scheme", {"locator": b"HTTPS://example.invalid/a"}, 1),
        ("prefix_only_validation", {"locator": b"http:abc"}, 0),
        ("short_locator", {"locator": b"http:ab"}, 1),
        ("locator_512", {"locator": b"http:" + b"x" * 507}, 0),
        ("locator_513", {"locator": b"http:" + b"x" * 508}, 1),
        ("missing_a1", {"omit": 0xa1}, 1), ("missing_a2", {"omit": 0xa2}, 1),
        ("chunk_zero", {"chunk": 0}, 1), ("chunk_300", {"chunk": 300}, 1),
        ("chunk_512", {"chunk": 512}, 0), ("chunk_2048", {"chunk": 2048}, 0),
        ("chunk_4096", {"chunk": 4096}, 1),
        ("start_offset", {"offset": 3072}, 0),
        ("timeout_override", {"timeout_seconds": 7}, 0),
    ):
        m = Machine(image)
        m.request(**kwargs)
        result = m.summary()
        assert result["reply_statuses"] == [expected], (name, result)
        assert len(result["download_task_substitutes"]) == int(expected == 0)
        if expected == 0:
            assert result["download_task_substitutes"][0]["start_offset"] == kwargs.get("offset", 0)
            assert result["stored_timeout_argument"] == kwargs.get("timeout_seconds", 180) * 1000
        rows.append({"case": name, **result})
    for name, options, expected in (("task_failure", {"task_result": 5}, 1),
                                    ("mutex_failure", {"mutex_result": 1}, 1),
                                    ("allocation_failure", {"allocation_failure": True}, 1)):
        m = Machine(image, **options)
        m.request()
        result = m.summary()
        assert result["reply_statuses"] == [expected]
        rows.append({"case": name, **result})
    for length in (0, 1024, 2048, 1025):
        m = Machine(image)
        m.request()
        m.total_length = length
        m.progress(length)
        result = m.summary()
        assert len(result["chunk_frames"]) == (length + 1023) // 1024
        assert result["stored_forward_offset"] == ((length + 1023) // 1024) * 1024
        raw = bytes((i * 7 + 11) & 255 for i in range(length)) + b"\x5a" * 2048
        for index, frame in enumerate(result["chunk_frames"]):
            offset = index * 1024
            expected_body = (b"\xa1\4" + struct.pack("<I", offset) + b"\xa2\4"
                             + struct.pack("<I", length) + b"\xa3\0\4" + raw[offset:offset + 1024])
            assert frame["body_sha256"] == hashlib.sha256(expected_body).hexdigest()
            assert frame["body_bytes"] == 1039 and frame["chunk_length_u16_le"] == 1024
        assert result["callback_input_read_extent"] == ((length + 1023) // 1024) * 1024
        rows.append({"case": "progress", "synthetic_callback_length": length, **result})
    for name, kwargs in (("progress_timeout", {"elapsed": 180001}),
                          ("progress_cancel_flag", {"canceled": True}), ("progress_kind3", {"kind": 3})):
        m = Machine(image)
        m.request()
        m.progress(1024, **kwargs)
        result = m.summary()
        assert not result["chunk_frames"]
        assert len(result["download_task_substitutes"]) == (2 if name == "progress_kind3" else 1)
        assert result["cancellation_substitutes"] == ([] if name == "progress_kind3"
                                                      else ["http_cancel_substitute", "task_message_20"])
        rows.append({"case": name, **result})
    for status in (0, 1, 0x6000a, 0x6000d):
        m = Machine(image)
        m.request()
        returned = m.run_entry(0x4203f088, status)
        result = m.summary()
        assert result["async_status_bytes"] == ([2] if status == 0 else [])
        assert returned == int(status == 0)
        assert result["current_locator_present"] == (status != 0)
        assert len(result["download_task_substitutes"]) == (2 if status == 1 else 1)
        rows.append({"case": "completion", "synthetic_status_argument": status,
                     "returned": returned, **result})
    for name, canceled, elapsed in (("completion_cancel_flag", True, 0),
                                    ("completion_timeout", False, 180001)):
        m = Machine(image)
        m.request()
        m.tick = 1000 + elapsed
        m.uc.mem_write(GLOBAL + 17, bytes((int(canceled),)))
        returned = m.run_entry(0x4203f088, 1)
        result = m.summary()
        assert returned == 1 and not result["current_locator_present"]
        assert result["async_status_bytes"] == ([] if canceled else [3])
        assert result["cancellation_substitutes"] == ([] if canceled else ["task_message_20"])
        assert len(result["download_task_substitutes"]) == 1
        rows.append({"case": name, "returned": returned, **result})
    negatives = negative_guards(image)
    return {"model": "A1763", "radio_version": "0.3.3.0", "radio_sha256": SHA256,
            "cases": len(rows), "results": rows, "negative_guards": negatives,
            "complete_settings_export": False,
            "physical_transport_verified": False, "station_commands_sent": 0,
            "per_entry_instruction_limit": 10000, "limits": __doc__.strip()}


def validate_image(image: bytes) -> None:
    if len(image) != 1482800 or hashlib.sha256(image).hexdigest() != SHA256:
        raise ValueError("Radio input hash/size mismatch")


def negative_guards(image: bytes) -> list[str]:
    rejected = []
    for name, altered in (("short_image", image[:-1]),
                           ("altered_image", bytes((image[0] ^ 1,)) + image[1:])):
        try:
            validate_image(altered)
        except ValueError:
            rejected.append(name)
        else:
            raise AssertionError(f"Accepted {name}")
    m = Machine(image)
    for name, action in (
        ("instruction_outside_allowed_spans", lambda: m.run_entry(0x42000020)),
        ("write_outside_download_state", lambda: m.write_guard(m.uc, 0, GLOBAL + 28, 1, 0, None)),
        ("unterminated_bounded_locator", lambda: m.text(m.alloc(b"x" * 513))),
    ):
        try:
            action()
        except AssertionError:
            rejected.append(name)
        else:
            raise AssertionError(f"Accepted {name}")
    return rejected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=Path(__file__).resolve().parents[2]
                        / "firmware/c1000_gen2/1.1.4.9/c1000-radio-validated.bin")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if not __debug__:
        raise RuntimeError("Assertions are required")
    os.umask(0o077)
    image = args.image.read_bytes()
    result = suite(image)
    package = Path(__file__).resolve().parent
    sources = (Path(__file__).name, "emulate_radio_update_status.py")
    manifest = {"radio_sha256": SHA256, "python": platform.python_version(), "unicorn": unicorn.__version__,
                "sources": {name: hashlib.sha256((package / name).read_bytes()).hexdigest() for name in sources}}
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    for suffix, value in (("results", result), ("manifest", manifest)):
        output = args.output_dir / f"radio-asset-download-{suffix}.json"
        output.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
        output.chmod(0o600)
    print(json.dumps({"synthetic_cases": result["cases"],
                      "negative_guards": len(result["negative_guards"]), "station_commands_sent": 0}))


if __name__ == "__main__":
    main()
