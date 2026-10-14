"""Private, explicitly authorized C2000 AC recovery; not an HA control.

0101/A2 passed one authorized A1783/main-2.1.6.4 OFF/ON recovery on 2026-10-14.
An idempotent ON acknowledgment alone cannot establish interruption/restoration.
Reported settings and server recovery are distinct from waveform measurements.
"""

from __future__ import annotations

import asyncio

from .protocol import Model, decode_telemetry, tlv


def _baseline(reply: bytes) -> tuple[dict, bytes, bytes]:
    if not reply or reply[0] != 0:
        raise RuntimeError("C2000 recovery status failed")
    metrics, fields = decode_telemetry(reply[1:], Model.C2000_GEN2)
    a4, d9 = fields.get(0xA4, b""), fields.get(0xD9, b"")
    if (metrics.get("software_version") != "2.1.6.4"
            or len(a4) != 34 or a4[0] != 4
            or len(d9) < 26 or d9[0] != 4 or d9[6] > 6 or len(d9) != 26 + 3 * d9[6]
            or any(len(fields.get(tag, b"")) < size or fields[tag][0] != 4
                   for tag, size in ((0xA7, 7), (0xB2, 4)))
            or any(type(metrics.get(k)) is not int or metrics[k] not in (0, 1)
                   for k in ("ac_output_enabled", "dc_output_enabled", "ac_input_connected"))):
        raise ValueError("C2000 recovery requires main 2.1.6.4 and complete fresh state")
    return metrics, a4, d9


async def recovery_output(server, enabled: bool, *, acknowledge_power_interruption: bool) -> dict:
    """One operator request, one write, two fresh confirmations; never retries.

OFF deliberately removes every AC load. A failed/uncertain OFF must be followed
by a separate explicit ON recovery, even if no OFF confirmation was received.
"""
    if (server.config.model != Model.C2000_GEN2 or type(enabled) is not bool
            or type(acknowledge_power_interruption) is not bool):
        raise ValueError("C2000 recovery requires explicit booleans and the exact model")
    if not enabled and not acknowledge_power_interruption:
        raise PermissionError("Explicit authorization of AC interruption is required")
    async with server._control_lock:
        connection = server._control_connection()
        before, a4_before, d9_before = _baseline(await connection.request(server.commands.status()))
        if not enabled and (
                before["ac_output_enabled"] != 1 or before["ac_input_connected"] != 1
                or before["dc_output_enabled"] != 0 or before.get("usage_mode") != "standard"
                or before.get("tou_schedule_slot_count") != 0
                or type(before.get("battery_percentage")) is not int or before["battery_percentage"] < 20
                or before.get("ac_output_timer_remaining_seconds") != 0
                or before.get("dc_output_timer_remaining_seconds") != 0):
            raise ValueError("C2000 interruption requires powered Standard baseline and inactive timers")
        # Deliberate bounded recovery request, not a generic raw-packet API.
        request = server.commands._request("0101", tlv(0xA2, bytes((1, int(enabled)))), milliseconds=True)
        await connection.request(request)
        for sample in range(2):
            if sample:
                await asyncio.sleep(1)
            metrics, a4, d9 = _baseline(await connection.request(server.commands.status()))
            expected = bytearray(a4_before)
            expected[22] = a4[22]  # Runtime LCD activity is not a saved preference.
            if (metrics["ac_output_enabled"] != int(enabled) or a4 != bytes(expected)
                    or d9[2:] != d9_before[2:]
                    or any(metrics[k] != before[k] for k in ("dc_output_enabled", "ac_input_connected"))
                    or not enabled and metrics.get("ac_output_power_w") != 0):
                raise RuntimeError("C2000 recovery readback failed; output may have changed")
        return {"settings_confirmed": True, "candidate_scope": "authorized C2000 AC recovery",
                "electrical_behavior_verified": False,
                "metrics": {k: metrics[k] for k in ("software_version", "ac_output_enabled", "dc_output_enabled",
                    "ac_input_connected", "ac_output_power_w", "ac_input_power_w") if k in metrics}}
