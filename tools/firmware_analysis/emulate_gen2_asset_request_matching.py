#!/usr/bin/env python3
"""Bounded A1763 pending-request registration, reply matching and expiry.

Actual fifteen-slot registration, function-10 reply dispatch/matcher, expiry
pass and selected 003d asset callback execute in synthetic RAM. Generic
callbacks, queues, timers, libc and logs are substitutes. Parsed contexts and
registration arguments are host seeded; no ingress, authentication, outer
framing, radio receiver, service loop, firmware boot or station executes.
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
                              UC_ARM_REG_R3, UC_ARM_REG_SP, UC_ARM_REG_LR)

from emulate_gen2_asset_transfer_replies import (
    CALLBACK, PAYLOAD, REQUEST, ReplyMachine,
)
from emulate_gen2_asset_transfer_start import FLAGS, SETTINGS, SIZE, TRANSFER
from emulate_clock_semantics import STACK, STOP
from replay_io import FIRMWARE_SHA256, firmware_image

TABLE, SLOTS, STRIDE = 0x20004fe8, 15, 12
SECOND_CALLBACK = CALLBACK + 16
APPLICATION_CALLBACK = CALLBACK + 32
ALLOW = ((0x080291e4, 0x0802924e), (0x08015a58, 0x08015aa8),
         (0x08022614, 0x0802265a), (0x080308e4, 0x08030920))


class Machine(ReplyMachine):
    def __init__(self) -> None:
        super().__init__()
        self.entry_instructions = self.maximum_entry_instructions = 0
        self.callback_deliveries = []
        self.uc.mem_write(TABLE, bytes(SLOTS * STRIDE))
        self.canary = bytes((i * 7 + 1) & 255 for i in range(STRIDE))
        self.uc.mem_write(TABLE + SLOTS * STRIDE, self.canary)
        self.uc.mem_write(REQUEST, bytes(24))
        self.uc.mem_write(REQUEST + 16, struct.pack("<H", 1))
        self.uc.mem_write(REQUEST + 20, struct.pack("<I", PAYLOAD))
        self.uc.mem_write(PAYLOAD, b"\2")
        self.uc.mem_write(TRANSFER, struct.pack("<I", APPLICATION_CALLBACK | 1))

    def run(self, address: int, argument: int) -> None:
        self.stopped = False
        self.entry_instructions = 0
        self.uc.reg_write(UC_ARM_REG_R0, argument)
        self.uc.reg_write(UC_ARM_REG_SP, STACK)
        self.uc.reg_write(UC_ARM_REG_LR, STOP | 1)
        self.uc.emu_start(address | 1, 0, count=3000)
        assert self.stopped, "Instruction bound exceeded before return"
        self.maximum_entry_instructions = max(self.maximum_entry_instructions, self.entry_instructions)

    def step(self, uc, address, size, data) -> None:
        self.entry_instructions += 1
        self.visited.add(address)
        r0, r1 = [uc.reg_read(reg) for reg in (UC_ARM_REG_R0, UC_ARM_REG_R1)]
        if address in (CALLBACK, SECOND_CALLBACK):
            assert (r0, r1) in ((1, REQUEST), (0, 0))
            self.callback_deliveries.append({"callback": f"{address:08x}",
                                             "success_argument": r0,
                                             "context_is_seeded_request": r1 == REQUEST,
                                             "active_slots_during_callback": self.active_slots()})
            self.back()
        elif address == APPLICATION_CALLBACK:
            assert r0 == 0 and r1 == 1
            self.application_calls.append({"success_argument": r0, "reason_argument": r1})
            self.back()
        elif address == 0x08020180:
            assert r1 == 16
            self.back()
        elif any(lo <= address < hi for lo, hi in ALLOW):
            return
        else:
            super().step(uc, address, size, data)

    def active_slots(self) -> list[int]:
        return [i for i in range(SLOTS) if self.uc.mem_read(TABLE + i * STRIDE + 11, 1)[0]]

    def seed(self, slot: int, *, command: int = 0x3d, function: int = 16,
             ticks: int = 5, callback: int = CALLBACK | 1, active: int = 1) -> None:
        assert 0 <= slot <= SLOTS and 0 <= command <= 65535 and 0 <= function <= 255
        assert 0 <= ticks <= 0xffffffff and 0 <= active <= 255
        assert callback in (0, CALLBACK | 1, SECOND_CALLBACK | 1, 0x0803058d)
        self.uc.mem_write(TABLE + slot * STRIDE,
                          struct.pack("<IIHBB", callback, ticks, command, function, active))
        if slot == SLOTS:
            self.canary = bytes(self.uc.mem_read(TABLE + slot * STRIDE, STRIDE))

    def register(self, *, command: int = 0x3d, function: int = 16,
                 ticks: int = 5, callback: int = CALLBACK | 1) -> None:
        assert 0 <= command <= 65535 and 0 <= function <= 255 and 0 <= ticks <= 0xffffffff
        assert callback in (0, CALLBACK | 1, SECOND_CALLBACK | 1, 0x0803058d)
        self.uc.reg_write(UC_ARM_REG_R1, function)
        self.uc.reg_write(UC_ARM_REG_R2, ticks)
        self.uc.reg_write(UC_ARM_REG_R3, callback)
        self.run(0x080291e4, command)

    def receive(self, opcode: int = 0x83d, *, function: int = 16, direct: bool = False,
                context_marker: int = 0, status: int = 2) -> None:
        assert 0 <= opcode <= 65535 and 0 <= function <= 255
        assert 0 <= context_marker <= 255 and 0 <= status <= 255
        self.uc.mem_write(REQUEST, bytes((context_marker,)))
        self.uc.mem_write(PAYLOAD, bytes((status,)))
        if direct:
            self.uc.reg_write(UC_ARM_REG_R1, function)
            self.uc.reg_write(UC_ARM_REG_R2, REQUEST)
            self.run(0x08015a58, opcode)
        else:
            assert function == 16
            self.uc.reg_write(UC_ARM_REG_R1, REQUEST)
            self.run(0x08022614, opcode)

    def expire(self) -> None:
        self.run(0x080308e4, 0)

    def summary(self) -> dict:
        assert bytes(self.uc.mem_read(TABLE + SLOTS * STRIDE, STRIDE)) == self.canary
        assert bytes(self.uc.mem_read(SETTINGS, SIZE)) == self.baseline
        assert bytes(self.uc.mem_read(FLAGS, 8)) == self.outputs
        assert not self.reads.intersection(range(SETTINGS, SETTINGS + SIZE))
        pending = []
        for slot in self.active_slots():
            callback, ticks, command, function, active = struct.unpack(
                "<IIHBB", bytes(self.uc.mem_read(TABLE + slot * STRIDE, STRIDE)))
            pending.append({"slot": slot, "callback": f"{callback:08x}", "countdown_raw": ticks,
                            "command_raw": command, "function_raw": function, "active_raw": active})
        return {"pending": pending, "callback_substitutes": self.callback_deliveries,
                "asset_callback_result": self.result(), "slot_15_canary_preserved": True,
                "maximum_entry_instructions": self.maximum_entry_instructions}


def suite() -> dict:
    firmware_image()
    rows = []
    for name, setup, receive, slots, deliveries in (
        ("registered_003d_matches_083d", [(0, {})], {}, [], 1),
        ("registered_003e_matches_083e", [(0, {"command": 0x3e})], {"opcode": 0x83e}, [], 1),
        ("last_slot_matches", [(14, {})], {}, [], 1),
        ("slot15_is_not_searched", [(15, {})], {}, [], 0),
        ("inactive_is_not_matched", [(0, {"active": 0})], {}, [], 0),
        ("nonzero_active_is_matched", [(0, {"active": 255})], {}, [], 1),
        ("wrong_reply_command", [(0, {})], {"opcode": 0x83e}, [0], 0),
        ("wrong_function", [(0, {})], {"function": 17, "direct": True}, [0], 0),
        ("different_context_marker", [(0, {})], {"context_marker": 255}, [], 1),
        ("null_callback_still_clears", [(0, {"callback": 0})], {}, [], 0),
        ("first_matching_duplicate_only", [(0, {}), (1, {"callback": SECOND_CALLBACK | 1})], {}, [1], 1),
        ("unrelated_slot_preserved", [(0, {"command": 0x3e}), (1, {})], {}, [0], 1),
    ):
        m = Machine()
        for slot, options in setup:
            m.seed(slot, **options)
        m.receive(**receive)
        assert m.active_slots() == slots and len(m.callback_deliveries) == deliveries
        if deliveries:
            matched_slot = 14 if name == "last_slot_matches" else (1 if name == "unrelated_slot_preserved" else 0)
            assert matched_slot in m.callback_deliveries[0]["active_slots_during_callback"]
            assert bytes(m.uc.mem_read(TABLE + matched_slot * STRIDE, STRIDE)) == bytes(STRIDE)
        rows.append({"case": name, **m.summary()})
    for name, count, command, function, expected_slot in (
        ("register_first_free", 0, 0x3d, 16, 0),
        ("register_last_free", 14, 0x3d, 16, 14),
        ("register_full_table", 15, 0x3d, 16, None),
    ):
        m = Machine()
        for i in range(count):
            m.seed(i, command=0x61)
        m.register(command=command, function=function, ticks=7)
        assert m.active_slots() == list(range(count + int(expected_slot is not None)))
        if expected_slot is not None:
            assert m.summary()["pending"][expected_slot]["command_raw"] == command
        rows.append({"case": name, **m.summary()})
    for name, command, function, stored_function, expected_slots in (
        ("duplicate_003d_allocates_second_slot", 0x3d, 16, 16, [0, 1]),
        ("equal_command_function_updates_slot", 16, 16, 16, [0]),
        ("update_branch_does_not_compare_stored_function", 16, 16, 17, [0]),
    ):
        m = Machine()
        m.seed(0, command=command, function=stored_function, ticks=5)
        m.register(command=command, function=function, ticks=7, callback=SECOND_CALLBACK | 1)
        assert m.active_slots() == expected_slots
        last = m.summary()["pending"][-1]
        assert last["countdown_raw"] == 7 and last["callback"] == f"{SECOND_CALLBACK | 1:08x}"
        if len(expected_slots) == 1:
            assert last["function_raw"] == stored_function
        rows.append({"case": name, **m.summary()})
    m = Machine()
    m.register()
    m.register(callback=SECOND_CALLBACK | 1)
    m.receive()
    m.receive()
    assert not m.active_slots() and [r["callback"] for r in m.callback_deliveries] == [
        f"{CALLBACK:08x}", f"{SECOND_CALLBACK:08x}"]
    rows.append({"case": "duplicate_replies_consume_slots_in_order", **m.summary()})
    for name, ticks, callback, active, expected_ticks, deliveries in (
        ("expire_zero", 0, CALLBACK | 1, 1, None, 1),
        ("expire_one", 1, CALLBACK | 1, 1, None, 1),
        ("decrement_two", 2, CALLBACK | 1, 1, 1, 0),
        ("decrement_u32_max", 0xffffffff, CALLBACK | 1, 1, 0xfffffffe, 0),
        ("expire_null_callback", 1, 0, 1, None, 0),
        ("expiry_ignores_inactive", 1, CALLBACK | 1, 0, None, 0),
    ):
        m = Machine()
        m.seed(0, ticks=ticks, callback=callback, active=active)
        m.expire()
        assert m.active_slots() == ([] if expected_ticks is None else [0])
        assert len(m.callback_deliveries) == deliveries
        if deliveries:
            assert m.callback_deliveries[0]["success_argument"] == 0
            assert not m.callback_deliveries[0]["context_is_seeded_request"]
        if expected_ticks is not None:
            assert m.summary()["pending"][0]["countdown_raw"] == expected_ticks
        rows.append({"case": name, **m.summary()})
    for name, status, retry, fail in (("asset_status_zero_requeues", 0, True, False),
                                    ("asset_status_two_waits", 2, False, False),
                                    ("asset_status_one_fails", 1, False, True),
                                    ("asset_status_255_fails", 255, False, True)):
        m = Machine()
        m.seed(0, callback=0x0803058d)
        m.receive(status=status)
        result = m.summary()
        assert not result["pending"] and not result["callback_substitutes"]
        assert len(result["asset_callback_result"]["descriptors"]) == int(retry)
        assert result["asset_callback_result"]["transfer_active"] == int(not fail)
        assert result["asset_callback_result"]["application_callback_substitutes"] == (
            [{"success_argument": 0, "reason_argument": 1}] if fail else [])
        rows.append({"case": name, **result})
    m = Machine()
    m.seed(0, callback=0x0803058d, ticks=1)
    m.expire()
    result = m.summary()
    assert not result["pending"] and len(result["asset_callback_result"]["descriptors"]) == 1
    rows.append({"case": "expired_asset_request_requeues_without_re_registration", **result})
    return {"model": "A1763", "main_version": "1.1.4.9", "firmware_sha256": FIRMWARE_SHA256,
            "cases": len(rows), "results": rows, "instruction_limit_per_entry": 3000,
            "negative_guards": negative_guards(), "station_commands_sent": 0,
            "physical_transport_verified": False, "full_ingress_verified": False,
            "limits": __doc__.strip()}


def negative_guards() -> list[str]:
    rejected = []
    for name, action in (
        ("live_worker_excluded", lambda m: m.run(0x08030800, 0)),
        ("firmware_startup_excluded", lambda m: m.run(0x080309c8, 0)),
        ("function_outside_u8", lambda m: m.register(function=256)),
        ("saved_settings_write_excluded", lambda m: m.write_hook(m.uc, 0, SETTINGS, 1, 0, None)),
        ("output_flags_write_excluded", lambda m: m.write_hook(m.uc, 0, FLAGS, 1, 0, None)),
    ):
        try:
            action(Machine())
        except (AssertionError, RuntimeError):
            rejected.append(name)
        else:
            raise AssertionError(f"Accepted {name}")
    return rejected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    result = suite()
    directory = Path(__file__).resolve().parent
    sources = sorted({Path(m.__file__).resolve() for m in tuple(sys.modules.values())
                      if getattr(m, "__file__", None) and Path(m.__file__).resolve().parent == directory})
    manifest = {"firmware_sha256": FIRMWARE_SHA256, "python": platform.python_version(),
                "unicorn": unicorn.__version__,
                "sources": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}}
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    for suffix, value in (("results", result), ("manifest", manifest)):
        p = args.output_dir / f"gen2-asset-request-matching-{suffix}.json"
        p.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
        p.chmod(0o600)
    print(json.dumps({"synthetic_cases": result["cases"],
                      "negative_guards": len(result["negative_guards"]), "station_commands_sent": 0}))


if __name__ == "__main__":
    main()
