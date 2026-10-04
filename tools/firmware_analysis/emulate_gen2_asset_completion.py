#!/usr/bin/env python3
"""Bounded A1763 asset final receipt and subsequent completion polling.

Selected final/status callbacks, descriptors, fixed serializers, timer wrapper
and transfer reset execute in synthetic RAM. Queue execution, timer services,
logging, libc, UART delivery and application callbacks are substitutes. No
receiver application, file/flash backend, firmware boot or station executes.
Reply frames are host-seeded callback inputs, without ingress/CRC validation.
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
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3

from emulate_gen2_asset_chunk_consumer import APP_CALLBACK, Machine as ChunkMachine, REPLY
from emulate_gen2_asset_transfer_start import FLAGS, SETTINGS, TIMERS, TRANSFER
from replay_io import FIRMWARE_SHA256, firmware_image

ALLOW = ((0x08017bd0, 0x08017c42), (0x08017b4c, 0x08017b94),
         (0x0801c9b4, 0x0801c9e2), (0x080265dc, 0x080265f8),
         (0x0802c2f8, 0x0802c33a))


class Machine(ChunkMachine):
    def __init__(self, *, callback: bool = True, queue_result: int = 1,
                 watchdog_timer: int = 9, prior_period: int = 0) -> None:
        assert 0 <= watchdog_timer <= 255 and 0 <= prior_period <= 65535
        super().__init__()
        self.queue_result = queue_result
        self.uc.mem_write(TRANSFER, struct.pack("<I", APP_CALLBACK | 1 if callback else 0))
        self.uc.mem_write(TIMERS + 4, bytes((watchdog_timer,)))
        self.uc.mem_write(TIMERS + 26, struct.pack("<H", prior_period))

    def step(self, uc, address, size, data) -> None:
        r0, r1, r2, r3 = [uc.reg_read(reg) for reg in
                          (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)]
        if address == 0x0800e59c:
            raw = bytes(uc.mem_read(r0, 20))
            callback, serializer, pointer, timeout, point = struct.unpack("<IIIIH", raw[:18])
            if point == 22:
                assert r1 == 2 and callback == 0x08017b4d
                assert serializer == 0x080265dd and pointer == 0 and timeout == 10
                self.entry_instructions += 1
                self.visited.add(address)
                self.descriptors.append({"callback": callback, "serializer": serializer,
                                         "pointer": pointer, "timeout_argument": timeout,
                                         "point_raw": point})
                self.back(self.queue_result)
                return
        elif address == APP_CALLBACK:
            assert (r0, r1) in ((1, 0), (0, 5))
            self.entry_instructions += 1
            self.visited.add(address)
            self.app_calls.append({"success_argument": r0, "reason_argument": r1})
            self.back()
            return
        elif address == 0x080105cc:
            assert r0 == 20000 and r1 == 0x08017add and r3 == TIMERS + 4
            self.entry_instructions += 1
            self.visited.add(address)
            self.write_hook(uc, 0, r3, 1, 0, None)
            uc.mem_write(r3, b"\x0b")
            self.timer_calls.append({"operation": "create", "period_argument": r0,
                                     "callback": f"{r1:08x}", "synthetic_id": 11})
            self.back(1)
            return
        elif address == 0x08010928:
            assert 0 < r0 <= 255 and r1 == 20000
            self.entry_instructions += 1
            self.visited.add(address)
            self.timer_calls.append({"operation": "set_period", "id": r0,
                                     "period_argument": r1})
            self.back(1)
            return
        elif any(lo <= address < hi for lo, hi in ALLOW):
            self.entry_instructions += 1
            self.visited.add(address)
            return
        super().step(uc, address, size, data)

    def response(self, point: int, *, valid: bool = True, malformed: str | None = None,
                 state: int = 1, retry: int | None = None, trailing: int = 0xee) -> dict:
        assert point in (20, 22) and 0 <= state <= 255 and 0 <= trailing <= 255
        frame = bytearray(16)
        frame[10:14] = bytes((point, 0, 0xaa if point == 20 else state, trailing))
        if malformed is not None:
            assert malformed in ("point", "status", "marker", "trailing")
            frame[{"point": 10, "status": 11, "marker": 12, "trailing": 13}[malformed]] ^= 1
        self.uc.mem_write(REPLY, bytes(frame))
        if retry is not None:
            assert 0 <= retry <= 255 and point == 20
            self.uc.mem_write(TIMERS + 21, bytes((retry,)))
        self.uc.reg_write(UC_ARM_REG_R1, REPLY if valid else 0)
        start = len(self.descriptors)
        self.run(0x08017bd0 if point == 20 else 0x08017b4c, int(valid))
        self.serialize_new(start)
        result = self.summary()
        result.update(final_retry_byte=self.uc.mem_read(TIMERS + 21, 1)[0],
                      watchdog_period_raw=int.from_bytes(self.uc.mem_read(TIMERS + 26, 2), "little"),
                      timer_service_substitutes=list(self.timer_calls))
        return result


def suite() -> dict:
    firmware_image()
    rows = []
    for name, options, kwargs, expected_point, expected_retry, active in (
        ("final_received", {}, {}, 22, 0, 1),
        ("final_received_without_application_callback", {"callback": False}, {}, 22, 0, 1),
        ("final_status_queue_rejected", {"queue_result": 0}, {}, 22, 0, 1),
        ("final_no_reply", {}, {"valid": False, "retry": 0}, 20, 1, 1),
        ("final_wrong_point", {}, {"malformed": "point"}, 20, 1, 1),
        ("final_error_status", {}, {"malformed": "status"}, 20, 1, 1),
        ("final_bad_marker", {}, {"malformed": "marker"}, 20, 1, 1),
        ("final_bad_trailing_marker", {}, {"malformed": "trailing"}, 20, 1, 1),
        ("final_fourth_missing", {}, {"valid": False, "retry": 3}, None, 0, 0),
        ("final_fourth_malformed", {}, {"malformed": "status", "retry": 3}, None, 0, 0),
        ("final_fourth_without_callback", {"callback": False}, {"valid": False, "retry": 3}, None, 0, 0),
        ("final_retry_u8_wrap", {}, {"valid": False, "retry": 255}, 20, 0, 1),
    ):
        m = Machine(**options)
        result = m.response(20, **kwargs)
        assert result["queued_points_raw"] == ([] if expected_point is None else [expected_point])
        assert result["final_retry_byte"] == expected_retry and result["transfer_active"] == active
        assert result["application_callback_substitutes"] == (
            [{"success_argument": 0, "reason_argument": 5}] if active == 0 and options.get("callback", True) else [])
        assert bool(result["timer_service_substitutes"]) == (active == 0)
        rows.append({"case": name, **result})
    for name, options, kwargs, success in (
        ("completion_confirmed", {}, {}, True),
        ("completion_without_callback", {"callback": False}, {}, True),
        ("completion_ignores_byte13", {}, {"trailing": 0x23}, True),
        ("completion_no_reply", {}, {"valid": False}, False),
        ("completion_wrong_point", {}, {"malformed": "point"}, False),
        ("completion_error_status", {}, {"malformed": "status"}, False),
        ("completion_state_zero", {}, {"state": 0}, False),
        ("completion_state_two", {}, {"state": 2}, False),
        ("completion_state_255", {}, {"state": 255}, False),
        ("completion_creates_watchdog", {"watchdog_timer": 0}, {"state": 0}, False),
        ("completion_reuses_watchdog_period", {"prior_period": 2000}, {"state": 0}, False),
        ("completion_queue_rejected", {"queue_result": 0}, {"state": 0}, False),
    ):
        m = Machine(**options)
        result = m.response(22, **kwargs)
        assert result["transfer_active"] == int(not success)
        assert result["queued_points_raw"] == ([] if success else [22])
        assert result["application_callback_substitutes"] == (
            [{"success_argument": 1, "reason_argument": 0}] if success and options.get("callback", True) else [])
        if not success:
            assert result["watchdog_period_raw"] == 2000
            operations = [r["operation"] for r in result["timer_service_substitutes"]]
            assert operations == (["start"] if options.get("prior_period") == 2000 else
                                  (["create"] if options.get("watchdog_timer") == 0 else [])
                                  + ["stop", "set_period", "start"])
        rows.append({"case": name, **result})
    m = Machine()
    m.chunk(total=1)
    m.reply()
    received = m.response(20)
    assert received["application_callback_substitutes"] == [] and received["transfer_active"] == 1
    confirmed = m.response(22)
    assert confirmed["queued_points_raw"] == [19, 20, 22]
    assert confirmed["application_callback_substitutes"] == [{"success_argument": 1, "reason_argument": 0}]
    assert confirmed["transfer_active"] == 0 and confirmed["radio_083e_ack_statuses"] == [0]
    rows.append({"case": "host_delivered_chunk_final_completion_chain", **confirmed})
    return {"model": "A1763", "main_version": "1.1.4.9", "firmware_sha256": FIRMWARE_SHA256,
            "cases": len(rows), "results": rows, "instruction_limit_per_entry": 100000,
            "negative_guards": negative_guards(), "station_commands_sent": 0,
            "receiver_application_verified": False, "physical_transport_verified": False,
            "complete_settings_export": False, "limits": __doc__.strip()}


def negative_guards() -> list[str]:
    rejected = []
    for name, action in (
        ("application_body_excluded", lambda m: m.run(0x08029e18, 1)),
        ("watchdog_callback_excluded", lambda m: m.run(0x08017adc, 0)),
        ("firmware_startup_excluded", lambda m: m.run(0x080309c8, 0)),
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
        p = args.output_dir / f"gen2-asset-completion-{suffix}.json"
        p.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
        p.chmod(0o600)
    print(json.dumps({"synthetic_cases": result["cases"],
                      "negative_guards": len(result["negative_guards"]), "station_commands_sent": 0}))


if __name__ == "__main__":
    main()
