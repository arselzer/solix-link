"""Reconnect and fan out local telemetry from one or more power stations."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import time
from typing import Any

from .client import SolixMonitor
from .config import DeviceConfig
from .protocol import Model
from .c1000_capabilities import original_prime_operation_supported
from .commands import validate_command, commands_for_transport
from .tou import power_flow


class MonitorService:
    """Own one BLE monitor per configured device and publish status updates."""

    def __init__(self, devices: list[DeviceConfig], reconnect_delay: float = 5.0) -> None:
        if not devices:
            raise ValueError("Configure at least one device")
        if any(device.protocol == "prime" and not device.client_id for device in devices):
            raise ValueError("Prime devices need a saved client_id; run solix-link pair first")
        self.devices = {device.name: device for device in devices}
        self.reconnect_delay = reconnect_delay
        self._status: dict[str, dict[str, Any]] = {
            device.name: {
                "name": device.name,
                "address": device.address.upper(),
                "model": device.model.value,
                "protocol": device.protocol,
                "connected": False,
                "last_seen": None,
                "last_seen_timestamp": None,
                "error": None,
                "metrics": {},
            }
            for device in devices
        }
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._tasks: list[asyncio.Task[None]] = []
        self._monitors: dict[str, SolixMonitor] = {}
        self._control_locks = {device.name: asyncio.Lock() for device in devices}

    def snapshot(self, name: str) -> dict[str, Any]:
        status = self._status[name]
        latest = status["last_seen_timestamp"]
        return {
            **status,
            "metrics": status["metrics"].copy(),
            "available": bool(status["connected"] and latest is not None and time.time() - latest < 90),
            "power_flow": power_flow(status["metrics"]) if status["connected"] and latest and time.time() - latest < 90 else "unknown",
        }

    def snapshots(self) -> list[dict[str, Any]]:
        return [self.snapshot(name) for name in self.devices]

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=20)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def _publish(self, name: str) -> None:
        event = self.snapshot(name)
        for queue in tuple(self._subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)

    def _on_update(self, name: str, metrics: dict[str, int | str]) -> None:
        status = self._status[name]
        status["metrics"] = metrics.copy()
        now = time.time()
        status["last_seen_timestamp"] = now
        status["last_seen"] = datetime.fromtimestamp(now, timezone.utc).isoformat()
        status["error"] = None
        self._publish(name)

    async def start(self) -> None:
        if self._tasks:
            return
        self._tasks = [asyncio.create_task(self._run_device(device)) for device in self.devices.values()]

    async def stop(self) -> None:
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def apply_setting(self, name: str, setting: str, **values: int | bool) -> dict[str, int | str]:
        """Apply a model-supported setting through the service's existing BLE link."""
        device = self.devices.get(name)
        if device is None:
            raise ValueError("Unknown device")
        if device.model == Model.C1000 and device.protocol == "prime" and not original_prime_operation_supported(setting):
            raise ValueError("This control is not verified for original C1000 Prime firmware")
        legacy_setting = (device.protocol == "legacy" and device.model in (Model.C300, Model.C1000) and setting in (
            "display_timeout", "ac_charging_power", "ac_output", "light_mode",
        ))
        legacy_setting = legacy_setting or (device.model == Model.C1000 and device.protocol == "legacy"
                                             and setting in ("device_timeout", "temperature_unit", "fast_charge",
                                                             "ac_power_saving", "dc_power_saving"))
        if setting == "charge_cap" and device.model != Model.C2000_GEN2:
            raise ValueError("Charge-cap setting is verified only on C2000 Gen 2 Prime")
        prime_setting = device.protocol == "prime" and (
            (device.model == Model.C1000 and original_prime_operation_supported(setting))
            or (device.model == Model.C1000_GEN2 and setting in ("charge_limits", "ac_charging_power", "display_timeout", "fast_charge", "device_timeout"))
            or (device.model == Model.C2000_GEN2 and setting in ("charge_cap", "ac_charging_power", "display_timeout"))
        )
        if not legacy_setting and not prime_setting:
            raise ValueError("This setting is not verified for the selected model")
        expected = {
            "charge_limits": {"upper": int, "lower": int},
            "charge_cap": {"upper": int},
            "ac_charging_power": {"watts": int},
            "display_timeout": {"seconds": int},
            "display_brightness": {"level": int},
            "fast_charge": {"enabled": bool},
            "ac_output": {"enabled": bool},
            "light_mode": {"mode": int},
            "device_timeout": {"minutes": int},
            "temperature_unit": {"fahrenheit": bool},
            "ac_power_saving": {"enabled": bool},
            "dc_power_saving": {"enabled": bool},
        }.get(setting)
        if expected is None:
            raise ValueError("Unsupported setting")
        if set(values) != set(expected) or any(type(values[key]) is not kind for key, kind in expected.items()):
            raise ValueError("Invalid setting fields or types")
        async with self._control_locks[name]:
            monitor = self._monitors.get(name)
            if monitor is None or not monitor.connected:
                raise ConnectionError("Station Bluetooth connection is unavailable")
            if setting == "charge_limits":
                return await monitor.set_charge_limits(values["upper"], values["lower"])
            if setting == "charge_cap":
                return await monitor.set_charge_cap(values["upper"])
            if setting == "ac_charging_power":
                return await monitor.set_ac_charging_power(values["watts"])
            if setting == "display_timeout":
                return await monitor.set_display_timeout(values["seconds"])
            if setting == "display_brightness":
                return await monitor.set_c1000_setting("display_brightness", values["level"])
            if setting == "device_timeout":
                return await monitor.set_device_timeout(values["minutes"])
            if setting == "temperature_unit":
                return await monitor.set_temperature_unit(values["fahrenheit"])
            if setting == "ac_power_saving":
                return await monitor.set_ac_power_saving_enabled(values["enabled"])
            if setting == "dc_power_saving":
                return await monitor.set_dc_power_saving_enabled(values["enabled"])
            if setting == "ac_output":
                return await monitor.set_ac_output_enabled(values["enabled"])
            if setting == "light_mode":
                return await monitor.set_light_mode(values["mode"])
            return await monitor.set_fast_charge_enabled(values["enabled"])

    def supported_commands(self, name: str) -> list[str]:
        device = self.devices[name]
        return list(commands_for_transport(device.model, device.protocol))

    async def command(self, name: str, command: str, **values) -> dict:
        validate_command(command, values)
        if command not in self.supported_commands(name):
            raise ValueError("Command is unsupported by this Bluetooth profile")
        if not self.snapshot(name)["available"]:
            raise ConnectionError("Fresh Bluetooth telemetry is unavailable")
        setting = {"set-charge-power": "ac_charging_power", "set-charge-cap": "charge_cap",
                   "set-display-timeout": "display_timeout", "set-display-brightness": "display_brightness", "set-fast-charge": "fast_charge",
                   "set-light": "light_mode", "set-device-timeout": "device_timeout",
                   "set-temperature-unit": "temperature_unit", "set-ac-power-saving": "ac_power_saving",
                   "set-dc-power-saving": "dc_power_saving"}[command]
        await self.apply_setting(name, setting, **values)
        return self.snapshot(name)

    async def _run_device(self, device: DeviceConfig) -> None:
        status = self._status[device.name]
        while True:
            monitor = SolixMonitor(
                device.address,
                model=device.model,
                owner_user_id=device.client_id,
                protocol=device.protocol,
                timezone_name=device.timezone_name,
                on_update=lambda metrics: self._on_update(device.name, metrics),
            )
            try:
                await monitor.connect(timeout=25)
                self._monitors[device.name] = monitor
                status["connected"] = True
                status["error"] = None
                self._publish(device.name)
                while monitor.connected:
                    await asyncio.sleep(30)
                    if not status["last_seen_timestamp"] or time.time() - status["last_seen_timestamp"] > 30:
                        await monitor.request_status()
                raise ConnectionError("Bluetooth connection closed")
            except asyncio.CancelledError:
                raise
            except Exception as error:
                status["error"] = f"{type(error).__name__}: {error}"
            finally:
                self._monitors.pop(device.name, None)
                status["connected"] = False
                self._publish(device.name)
                await monitor.disconnect()
            await asyncio.sleep(self.reconnect_delay)
