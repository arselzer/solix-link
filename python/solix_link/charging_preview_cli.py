"""Read bounded local preview inputs without connecting to stations."""
from __future__ import annotations

import json
from pathlib import Path

from .charging_policy import ChargingPolicyRequestError, MAX_PREVIEW_BYTES, preview_charging_policy


def read_document(path: Path, limit: int) -> object:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
            raise ValueError
        with path.open("rb") as stream:
            content = stream.read(limit + 1)
        if len(content) > limit:
            raise ValueError
        return json.loads(content, object_pairs_hook=unique_object)
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise ChargingPolicyRequestError() from None


def offline_preview(snapshot_file: Path, request_file: Path, *, adaptive: bool = False) -> dict:
    if adaptive:
        from .adaptive_policy import preview_adaptive_policy
        preview = preview_adaptive_policy
    else:
        preview = preview_charging_policy
    return preview(read_document(snapshot_file, 1_048_576),
                   read_document(request_file, MAX_PREVIEW_BYTES))


def native_preview(directory: Path, name: str | None, request_file: Path, *, adaptive: bool = False) -> dict:
    from .ap_service_config import load_ap_service
    from .ap_service_monitor import APServiceMonitor
    config = load_ap_service(directory / "ap_service.json")
    monitor = APServiceMonitor(config, directory)
    selected = name or config.name
    if selected not in monitor.devices:
        raise ValueError("Unknown configured station")
    if adaptive:
        from .adaptive_policy import preview_adaptive_policy
        preview = preview_adaptive_policy
    else:
        preview = preview_charging_policy
    return preview(monitor.snapshot(selected), read_document(request_file, MAX_PREVIEW_BYTES))
