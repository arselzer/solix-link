"""Opt-in C2000 AC control and bounded recovery with durable ON intent.

0101/A2 passed one authorized A1783/main-2.1.6.4 OFF/ON recovery on 2026-10-14.
An idempotent ON acknowledgment alone cannot establish interruption/restoration.
Reported settings and server recovery are distinct from waveform measurements.
"""

from __future__ import annotations

import asyncio

from .protocol import Model, decode_telemetry


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


def _off_baseline(before: dict) -> None:
    if (before["ac_output_enabled"] != 1 or before["ac_input_connected"] != 1
            or before["dc_output_enabled"] != 0 or before.get("usage_mode") != "standard"
            or before.get("tou_schedule_slot_count") != 0
            or type(before.get("battery_percentage")) is not int or before["battery_percentage"] < 20
            or before.get("ac_output_timer_remaining_seconds") != 0
            or before.get("dc_output_timer_remaining_seconds") != 0):
        raise ValueError("C2000 interruption requires powered Standard baseline and inactive timers")


async def _output_locked(server, enabled: bool, *, restoring: bool = False) -> dict:
    """Caller owns the control lock. Restoration cannot be revoked mid-cycle."""
    if not restoring:
        server.ac_output_permission.require()
        connection = server._control_connection()
    else:
        connection = server.connection
        if connection is None:
            raise ConnectionError("Station is not connected for AC restoration")
    before, a4_before, d9_before = _baseline(await connection.request(server.commands.status()))
    if not enabled:
        _off_baseline(before)
    await connection.request(server.commands.ac_output(enabled))
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
            raise RuntimeError("C2000 AC readback failed; output may have changed")
    server.metrics.update(metrics)
    server.changed()
    return {**server.snapshot(), "settings_confirmed": True, "electrical_behavior_verified": False}


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
    server.ac_output_permission.require()
    async with server._control_lock:
        if server.ac_output_permission.pending() and not enabled:
            raise PermissionError("Restore AC ON before another interruption")
        result = await _output_locked(server, enabled)
        if enabled:
            server.ac_output_permission.restored()
            server.changed()
        return {**result, "ac_power_control": server.ac_output_permission.snapshot()}


async def _restore_locked(server) -> dict:
    """Only ON is retried. Unconfirmed restoration keeps the durable intent."""
    last_error = None
    server._ac_restore_connection = server.connection
    async with asyncio.timeout(90):
        for attempt in range(3):
            try:
                result = await _output_locked(server, True, restoring=True)
                server.ac_output_permission.restored()
                server.changed()
                return {**result, "ac_power_control": server.ac_output_permission.snapshot()}
            except (OSError, ValueError, RuntimeError, TimeoutError) as error:
                last_error = error
                if attempt < 2:
                    await asyncio.sleep(3)
    raise RuntimeError("AC ON restoration unconfirmed; saved intent remains pending") from last_error


async def resume_restore(server) -> dict:
    async with server._control_lock:
        if not server.ac_output_permission.pending():
            return server.snapshot()
        return await _restore_locked(server)


async def cycle_output(server, off_seconds: int, *, acknowledge_power_interruption: bool,
                       controller_independent: bool) -> dict:
    from .commands import validate_command
    validate_command("cycle-ac-output", {"off_seconds": off_seconds,
        "acknowledge_power_interruption": acknowledge_power_interruption,
        "controller_independent": controller_independent})
    if server.config.model != Model.C2000_GEN2:
        raise ValueError("AC cycle requires C2000 Gen 2")
    server.ac_output_permission.require()
    if server._ac_power_task is not None and not server._ac_power_task.done():
        raise PermissionError("AC operation is already running")

    async def run():
        async with server._control_lock:
            server.ac_output_permission.require()
            connection = server._control_connection()
            before, _, _ = _baseline(await connection.request(server.commands.status()))
            _off_baseline(before)
            server.ac_output_permission.arm()  # Durable before any possible OFF.
            server.changed()
            try:
                await _output_locked(server, False)
                await asyncio.sleep(off_seconds)  # Hold starts after OFF confirmation.
            finally:
                await _restore_locked(server)
            return server.snapshot()

    server._ac_power_task = asyncio.create_task(run())
    server._ac_power_task.add_done_callback(server._ac_power_done)
    # HTTP/CLI disconnects must not cancel the worker's restoration.
    return await asyncio.shield(server._ac_power_task)
