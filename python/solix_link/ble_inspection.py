"""Bounded BLE discovery and GATT inventory without a SOLIX login."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
import math
from typing import Any
from uuid import UUID

from .protocol import COMMAND_UUID, IDENTIFIER_UUID, Model, SERVICE_UUID, TELEMETRY_UUID


INSPECTION_MODELS = (Model.C1000, Model.C1000_GEN2)
_PROPERTIES = frozenset(("read", "write", "write-without-response", "notify", "indicate"))
_ROLES = {
    COMMAND_UUID: "solix_command",
    TELEMETRY_UUID: "solix_telemetry",
    IDENTIFIER_UUID: "station_identifier",
    "00002a26-0000-1000-8000-00805f9b34fb": "firmware_revision",
    "00002a27-0000-1000-8000-00805f9b34fb": "hardware_revision",
    "00002a28-0000-1000-8000-00805f9b34fb": "software_revision",
}


def _timeout(value: float, name: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not 1 <= value <= 30 or not math.isfinite(value)):
        raise ValueError(f"{name} must be 1–30 seconds")
    return float(value)


def _uuid(value: str) -> str | None:
    try:
        return str(UUID(value))
    except (AttributeError, TypeError, ValueError):
        return None


def _inventory(services: Iterable[Any]) -> list[dict[str, Any]]:
    """Keep UUIDs/properties only, including no characteristic values or handles."""
    result = []
    for service in services:
        service_uuid = _uuid(service.uuid)
        if service_uuid is None:
            continue
        characteristics = []
        for characteristic in service.characteristics:
            characteristic_uuid = _uuid(characteristic.uuid)
            if characteristic_uuid is None:
                continue
            item = {
                "uuid": characteristic_uuid,
                "properties": sorted(_PROPERTIES.intersection(characteristic.properties)),
            }
            if characteristic_uuid in _ROLES:
                item["role"] = _ROLES[characteristic_uuid]
            characteristics.append(item)
        result.append({
            "uuid": service_uuid,
            "characteristics": sorted(characteristics, key=lambda item: item["uuid"]),
        })
    return sorted(result, key=lambda item: item["uuid"])


async def inspect_ble_features(model: Model, *, scan_timeout: float = 10,
                               connect: bool = False, connect_timeout: float = 15) -> dict[str, Any]:
    """Inspect one C1000 model without profiles, pairing, reads, writes or login.

    Default behavior is advertisement discovery only. With ``connect=True``,
    exactly one named model match is required before connecting to enumerate
    GATT services. Service-only advertisements are reported, never guessed.
    Error messages, device names, addresses and characteristic values are not
    returned. GATT properties do not establish supported SOLIX commands.
    """
    model = Model(model)
    if model not in INSPECTION_MODELS:
        raise ValueError("BLE inspection is limited to the two C1000 models")
    scan_timeout = _timeout(scan_timeout, "scan_timeout")
    connect_timeout = _timeout(connect_timeout, "connect_timeout")
    if type(connect) is not bool:
        raise ValueError("connect must be a boolean")

    from bleak import BleakClient, BleakScanner

    report = {
        "model": model.value,
        "mode": "gatt_inventory" if connect else "advertisement_discovery",
        "result": "scanning",
        "matching_advertisements": 0,
        "unclassified_solix_advertisements": 0,
        "connection_attempted": False,
        "solix_handshake_started": False,
        "characteristic_reads": 0,
        "characteristic_writes": 0,
    }
    try:
        found = await asyncio.wait_for(
            BleakScanner.discover(timeout=scan_timeout, return_adv=True), scan_timeout + 5)
    except Exception as error:
        return {**report, "result": "scan_failed", "error_type": type(error).__name__}

    matches = []
    for device, advertisement in found.values():
        try:
            detected = Model.from_name(advertisement.local_name or device.name)
        except ValueError:
            if SERVICE_UUID in (uuid.lower() for uuid in advertisement.service_uuids):
                report["unclassified_solix_advertisements"] += 1
            continue
        if detected == model:
            matches.append(device)
    report["matching_advertisements"] = len(matches)
    if not matches:
        return {**report, "result": "not_found"}
    if not connect:
        return {**report, "result": "discovered"}
    if len(matches) != 1:
        return {**report, "result": "ambiguous"}

    client = None
    report["connection_attempted"] = True
    try:
        client = BleakClient(matches[0], timeout=connect_timeout, pair=False)
        async with asyncio.timeout(connect_timeout):
            await client.connect()
            services = _inventory(client.services)
        report.update(result="inspected", services=services)
    except Exception as error:
        report.update(result="connection_failed", error_type=type(error).__name__)
    finally:
        if client is not None:
            try:
                await asyncio.wait_for(client.disconnect(), 5)
            except Exception as error:
                report.update(result="disconnect_failed", disconnect_error_type=type(error).__name__)
    return report
