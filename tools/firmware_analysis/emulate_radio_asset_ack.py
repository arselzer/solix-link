#!/usr/bin/env python3
"""Bounded radio 003e send queue, received ACK and asset callback replay.

Actual frame builder, enqueue object constructor, receive validation/processing,
payload classification, resend ACK helper, selected send worker and asset ACK
callback run on synthetic RAM. OS queues/allocation/mutex/time/delay, libc,
physical send, logging and session/GCM primitives are host substitutes. A host
pause/resume delivers one synthetic reply during a substituted delay; no real
scheduler, station, network, controller, HTTP task or firmware boot executes.
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

import cryptography
import unicorn
from unicorn import riscv_const as R

from emulate_radio_factory_routes import (
    IMAGE_NAME, IMAGE_SHA256, Machine as RoutesMachine, STOP, packet,
)

STACK = 0x3fd0f000
TX_QUEUE, RX_QUEUE = 0x3fc830dc, 0x3fc830d0
ACK_WORD, WAIT_WORD, ACK_FLAG = 0x3fc9070c, 0x3fc82a50, 0x3fc90604
ALLOW = ((0x42053f70, 0x4205401e), (0x420543bc, 0x4205464c),
         (0x4203c8fa, 0x4203c90a), (0x4203c9e0, 0x4203ca9e))


class Machine(RoutesMachine):
    def __init__(self, image: bytes) -> None:
        if not __debug__:
            raise RuntimeError("Replay assertions are required; do not run Python with -O")
        if len(image) != 1482800 or hashlib.sha256(image).hexdigest() != IMAGE_SHA256:
            raise ValueError("Radio image hash/size mismatch")
        self.tx_queue = []
        self.tx_objects = []
        self.physical_send_substitutes = []
        self.callback_arguments = []
        self.delay_arguments = []
        self.pause_context = None
        self.pause_next_delay = False
        self.stopped = False
        self.maximum_entry_instructions = 0
        super().__init__(image)
        assert self.registrations["10"] == ["4203c9e0"]
        self.uc.mem_write(ACK_WORD, b"\xff\xff")
        self.uc.mem_write(WAIT_WORD, b"\xff\xff")
        self.uc.mem_write(ACK_FLAG, b"\0")
        self.protected_regions = ((0x3fc89eb4, 0x3fc8a12c), (0x3fc8ab70, 0x3fc8aeb8))
        self.protected_before = [self.read(lo, hi - lo) for lo, hi in self.protected_regions]
        for address, size in ((0x3c130000, 0x40000), (0x42000000, 0x200000),
                              (0x40380000, 0x10000), (0x40000000, 0x1000), (STOP, 0x1000)):
            self.uc.mem_protect(address, size, unicorn.UC_PROT_READ | unicorn.UC_PROT_EXEC)

    def write_guard(self, uc, access, address, size, value, data) -> None:
        if any(lo <= address < address + size <= hi for lo, hi in (
                (0x3fc905ec, 0x3fc905f4), (ACK_FLAG, ACK_FLAG + 1),
                (0x3fc90709, 0x3fc9070e), (WAIT_WORD, WAIT_WORD + 2))):
            return
        super().write_guard(uc, access, address, size, value, data)

    def back(self, value: int = 0) -> None:
        self.uc.reg_write(R.UC_RISCV_REG_A0, value & 0xffffffff)
        self.uc.reg_write(R.UC_RISCV_REG_PC, self.uc.reg_read(R.UC_RISCV_REG_RA))

    def step(self, uc, address, size, data) -> None:
        args = [uc.reg_read(R.UC_RISCV_REG_A0 + i) for i in range(8)]
        a0, a1, a2, a3 = args[:4]
        if address == 0x4205200c and a0 == TX_QUEUE:
            self.instructions += 1
            self.tx_queue.append(a1)
            raw = self.read(a1, 0x30)
            length = int.from_bytes(raw[0x2c:0x2e], "little")
            assert 10 <= length <= 128 and self.u32(a1 + 0x18) == 0x4203c8fa
            self.tx_objects.append({"port": self.u32(a1 + 8), "active_raw": raw[14],
                                    "timeout_raw": int.from_bytes(raw[16:18], "little"),
                                    "command_raw": int.from_bytes(raw[18:20], "little"),
                                    "callback": f"{self.u32(a1 + 24):08x}",
                                    "frame_bytes": length,
                                    "frame_sha256": hashlib.sha256(self.read(a1 + 0x2e, length)).hexdigest()})
            self.back(1)
        elif address == 0x4205201e and a0 == TX_QUEUE:
            self.instructions += 1
            if not self.tx_queue:
                self.queue_finished = True
                uc.emu_stop()
                return
            self.write_guard(uc, 0, a1, 4, 0, None)
            uc.mem_write(a1, struct.pack("<I", self.tx_queue.pop(0)))
            self.back(1)
        elif address == 0x42052030:
            self.instructions += 1
            assert a0 in (5, 10, 30)
            self.delay_arguments.append(a0)
            if a0 == 5 and self.pause_next_delay:
                self.pause_next_delay = False
                self.pause_context = uc.context_save()
                self.queue_finished = True
                uc.emu_stop()
            else:
                self.back()
        elif address == 0x4205421a:
            self.instructions += 1
            assert a0 == 2 and 10 <= a2 <= 128 and a3 == 0
            wire = self.read(a1, a2)
            assert wire == packet(wire[9:-1], int.from_bytes(wire[7:9], "big"), wire[6], wire[5])
            self.physical_send_substitutes.append({"port": a0, "bytes": len(wire),
                                                   "sha256": hashlib.sha256(wire).hexdigest()})
            self.back()
        elif address == 0x4204f202:
            self.instructions += 1
            self.back(1000)
        elif address == 0x4203c8fa:
            self.instructions += 1
            self.callback_arguments.append({"command_argument": a0, "status_argument": a1})
            return  # Execute the callback, including its actual ACK flag store.
        elif address in (0x40000354, 0x40000358):
            self.instructions += 1
            assert a2 <= 8192
            self.write_guard(uc, 0, a0, a2, 0, None)
            uc.mem_write(a0, bytes((a1 & 255,)) * a2 if address == 0x40000354 else self.read(a1, a2))
            self.back(a0)
        elif any(lo <= address < hi for lo, hi in ALLOW):
            self.instructions += 1
            return
        else:
            super().step(uc, address, size, data)

    def run(self, address: int, *args: int, stop_at: int = STOP) -> None:
        start = self.instructions
        super().run(address, *args, stop_at=stop_at)
        self.maximum_entry_instructions = max(self.maximum_entry_instructions, self.instructions - start)

    def prepare(self, *, timeout: int = 1000) -> None:
        assert 1 <= timeout <= 2000
        body = b"\xa1\4" + bytes(4) + b"\xa2\4\x00\x04\x00\x00\xa3\x01\x00\x5a"
        body_pointer = self.alloc(body)
        descriptor = self.alloc(struct.pack("<B3xIHHI", 1, 2, timeout, 0, 0x4203c8fa))
        self.run(0x4204fcae, 16, 0x3e, body_pointer, len(body), descriptor)
        assert len(self.tx_objects) == len(self.tx_queue) == 1
        assert self.tx_objects[0]["command_raw"] == 0x3e

    def worker(self, reply: bytes | None, *, port: int = 2) -> None:
        self.pause_next_delay = reply is not None
        self.run(0x42054526)
        if reply is not None:
            assert self.pause_context is not None
            self.uc.reg_write(R.UC_RISCV_REG_SP, STACK - 0x400)
            self.receive(reply, port=port)
            self.uc.context_restore(self.pause_context)
            self.back()  # Complete the substituted delay before resuming worker instructions.
            self.queue_finished = False
            start = self.instructions
            self.uc.emu_start(self.uc.reg_read(R.UC_RISCV_REG_PC), 0, count=100000)
            assert self.queue_finished or self.uc.reg_read(R.UC_RISCV_REG_PC) == STOP
            self.maximum_entry_instructions = max(self.maximum_entry_instructions, self.instructions - start)
        assert not self.tx_queue and len(self.callback_arguments) == 1

    def summary(self) -> dict:
        assert [self.read(lo, hi - lo) for lo, hi in self.protected_regions] == self.protected_before
        return {"queued_objects": self.tx_objects, "physical_send_substitutes": self.physical_send_substitutes,
                "callback_arguments": self.callback_arguments, "ack_flag_raw": self.read(ACK_FLAG, 1)[0],
                "last_ack_command_raw": int.from_bytes(self.read(ACK_WORD, 2), "little"),
                "waiting_command_raw": int.from_bytes(self.read(WAIT_WORD, 2), "little"),
                "delay_calls": len(self.delay_arguments),
                "delay_argument_counts": {str(n): self.delay_arguments.count(n) for n in (5, 10, 30)},
                "protected_radio_state_preserved": True,
                "maximum_entry_instructions": self.maximum_entry_instructions}


def suite(image: bytes) -> dict:
    rows = []
    for name, body, command, function, port, checksum_valid, accepted in (
        ("main_success_tlv", b"\xa1\1\0", 0x83e, 16, 2, True, True),
        ("main_failure_tlv", b"\xa1\1\1", 0x83e, 16, 2, True, True),
        ("raw_zero_status", b"\0", 0x83e, 16, 2, True, True),
        ("raw_failure_status", b"\1", 0x83e, 16, 2, True, True),
        ("empty_body", b"", 0x83e, 16, 2, True, True),
        ("arbitrary_body", b"\xdf\1\xff", 0x83e, 16, 2, True, True),
        ("wrong_opcode", b"\xa1\1\0", 0x83f, 16, 2, True, False),
        ("wrong_function", b"\xa1\1\0", 0x83e, 23, 2, True, True),
        ("nonreply_opcode", b"\xa1\1\0", 0x3e, 16, 2, True, False),
        ("bad_outer_checksum", b"\xa1\1\0", 0x83e, 16, 2, False, False),
        ("port_zero", b"\xa1\1\0", 0x83e, 16, 0, True, True),
    ):
        m = Machine(image)
        m.prepare()
        wire = packet(body, command, function=function)
        if not checksum_valid:
            wire = wire[:-1] + bytes((wire[-1] ^ 1,))
        m.worker(wire, port=port)
        result = m.summary()
        assert result["ack_flag_raw"] == (1 if accepted else 2), name
        assert result["callback_arguments"] == [{"command_argument": 0x3e,
                                                  "status_argument": 0 if accepted else 1}], name
        assert len(result["physical_send_substitutes"]) == (1 if accepted else 3), name
        assert result["waiting_command_raw"] == (0xffff if accepted else 0x3e)
        rows.append({"case": name, "incoming_command_raw": command, "incoming_function_raw": function,
                     "incoming_port": port, "incoming_body_bytes": len(body),
                     "incoming_body_sha256": hashlib.sha256(body).hexdigest(), **result})
    for timeout in (1, 5, 1000, 2000):
        m = Machine(image)
        m.prepare(timeout=timeout)
        m.worker(None)
        result = m.summary()
        assert result["ack_flag_raw"] == 2 and len(result["physical_send_substitutes"]) == 3
        assert result["callback_arguments"] == [{"command_argument": 0x3e, "status_argument": 1}]
        rows.append({"case": "no_response", "timeout_fixture_raw": timeout, **result})
    return {"model": "A1763", "radio_version": "0.3.3.0", "radio_sha256": IMAGE_SHA256,
            "cases": len(rows), "results": rows, "negative_guards": negative_guards(image),
            "instruction_limit_per_entry": 100000, "station_commands_sent": 0,
            "physical_transport_verified": False, "account_free_setup_verified": False,
            "receiver_application_verified": False, "limits": __doc__.strip()}


def negative_guards(image: bytes) -> list[str]:
    rejected = []
    for name, action in (
        ("http_task_engine_excluded", lambda m: m.run(0x42012a98)),
        ("station_setting_handler_excluded", lambda m: m.run(0x4203f4ee)),
        ("timeout_fixture_bounds", lambda m: m.prepare(timeout=2001)),
        ("mmio_write_excluded", lambda m: m.write_guard(m.uc, 0, 0x40000000, 1, 0, None)),
        ("protected_radio_state_write_excluded", lambda m: m.write_guard(m.uc, 0, 0x3fc89eb4, 1, 0, None)),
    ):
        try:
            action(Machine(image))
        except (AssertionError, RuntimeError):
            rejected.append(name)
        else:
            raise AssertionError(f"Accepted {name}")
    return rejected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=Path(__file__).resolve().parents[2]
                        / "firmware/c1000_gen2/1.1.4.9" / IMAGE_NAME)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    image = args.image.read_bytes()
    result = suite(image)
    directory = Path(__file__).resolve().parent
    sources = sorted({Path(m.__file__).resolve() for m in tuple(sys.modules.values())
                      if getattr(m, "__file__", None) and Path(m.__file__).resolve().parent == directory})
    manifest = {"radio_sha256": IMAGE_SHA256, "python": platform.python_version(),
                "unicorn": unicorn.__version__, "cryptography": cryptography.__version__,
                "sources": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}}
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    for suffix, value in (("results", result), ("manifest", manifest)):
        p = args.output_dir / f"radio-asset-ack-{suffix}.json"
        p.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
        p.chmod(0o600)
    print(json.dumps({"synthetic_cases": result["cases"],
                      "negative_guards": len(result["negative_guards"]), "station_commands_sent": 0}))


if __name__ == "__main__":
    main()
