"""Adapt namespace-worker snapshots and guarded controls to the HTTP/SSE server."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import time

from .ap_service_config import APServiceConfig, load_ap_service_profiles
from .commands import native_commands_for_model, validate_command
from .ap_service import ap_service_request
from .protocol import Model
from .energy_values import validate_native_energy
from .original_counters import validate_original_counters
from .wifi_signal import POLL_INTERVAL_SECONDS, validate_wifi_signal


class APServiceMonitor:
    """Monitor-service interface backed by the private native MQTT status file."""

    def __init__(self, config: APServiceConfig, directory: Path, *, wifi_rssi: bool = False) -> None:
        self.config, self.directory = config, directory
        profiles = load_ap_service_profiles(directory, config)
        self.devices = {name: item for name, (item, _) in profiles.items()}
        self.directories = {name: path for name, (_, path) in profiles.items()}
        self._subscribers: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None
        self._radio_task: asyncio.Task | None = None
        self._wifi_rssi = wifi_rssi

    def snapshot(self, name: str) -> dict:
        if name not in self.devices:
            raise KeyError(name)
        try:
            path = self.directories[name] / "status.json"
            status = json.loads(path.read_text())
            fresh = time.time() - path.stat().st_mtime < 15
            latest = status.get("last_seen_timestamp")
            status["connected"] = bool(fresh and status.get("connected"))
            status["available"] = bool(status["connected"] and latest and time.time() - latest < 30)
            status["native_energy"] = validate_native_energy(status.get("native_energy"), model=self.devices[name].model.value)
            status["original_counters"] = validate_original_counters(status.get("original_counters"), model=self.devices[name].model.value, protocol="native_mqtt")
            status["wifi_signal"] = validate_wifi_signal(status.get("wifi_signal"),
                model=self.devices[name].model.value, protocol="native_mqtt")
            if not status["available"] or self.devices[name].model == Model.C1000:
                status["power_flow"] = "unknown"
            return status
        except (OSError, ValueError):
            return {"name": name, "model": self.devices[name].model.value, "protocol": "native_mqtt",
                    "connected": False, "available": False, "last_seen_timestamp": None,
                    "error": "AP service status unavailable", "metrics": {}}

    def supported_commands(self, name: str) -> list[str]:
        if not self.snapshot(name).get("control_enabled"):
            return []
        return list(native_commands_for_model(self.devices[name].model))

    async def check_setup(self) -> dict:
        """Check saved AP files without polling or contacting the worker."""
        from .ap_service_check import check_ap_service
        return await asyncio.to_thread(check_ap_service, self.directory)

    async def command(self, name: str, command: str, **values) -> dict:
        validate_command(command, values)
        if command not in self.supported_commands(name):
            raise PermissionError("Native worker controls are disabled")
        if not self.snapshot(name)["available"]:
            raise ConnectionError("Fresh native telemetry is unavailable")
        return await ap_service_request(self.directory, command, name=name, **values)

    def snapshots(self) -> list[dict]:
        return [self.snapshot(name) for name in self.devices]

    def subscribe(self) -> asyncio.Queue:
        queue = asyncio.Queue(maxsize=20)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._poll())
        if self._wifi_rssi:
            self._radio_task = asyncio.create_task(self._poll_wifi_signal())

    async def stop(self) -> None:
        tasks = [task for task in (self._task, self._radio_task) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _query_wifi_signal(self, name: str) -> None:
        """Only the validated model/firmware can reach the read-only socket route."""
        status = self.snapshot(name)
        metrics = status.get("metrics", {})
        if (self.devices[name].model != Model.C1000_GEN2 or not status.get("available")
                or metrics.get("software_version") != "1.1.4.9"
                or metrics.get("software_version_module") != "0.3.3.0"):
            return
        try:
            await ap_service_request(self.directory, "wifi-rssi", name=name)
        except (OSError, ValueError, RuntimeError, TimeoutError):
            # Bounded cadence: no immediate retry, setting write or recovery.
            pass

    async def _poll_wifi_signal(self) -> None:
        while True:
            for name in self.devices:
                await self._query_wifi_signal(name)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    async def _poll(self) -> None:
        previous = None
        while True:
            statuses = {name: self.snapshot(name) for name in self.devices}
            for name, status in statuses.items():
                if previous is None or status != previous.get(name):
                    for queue in self._subscribers:
                        if queue.full():
                            queue.get_nowait()
                        queue.put_nowait(status)
            previous = statuses
            await asyncio.sleep(1)
