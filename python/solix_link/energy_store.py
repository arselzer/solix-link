"""Per-station private persistence for passive energy uploads, with epoch warnings."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import stat
import time

from .ap_service_config import private_write
from .energy_report import REPORT_NAME
from .energy_values import MAX_INTEGER, SOURCE, energy_groups, validate_native_energy


class NativeEnergyStore:
    """Persist latest counters only; never infer wraps, lifetime energy or resets."""

    def __init__(self, model: str, path: Path | None = None) -> None:
        self.model, self.path = model, path
        self.state: dict | None = None
        self.persistence_error = False
        if path is not None and (path.exists() or path.is_symlink()):
            try:
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_size > 16384:
                    raise ValueError("Unsafe energy state file")
                self.state = validate_native_energy(json.loads(path.read_text()), model=model)
                if self.state is None:
                    raise ValueError("Invalid energy state file")
            except (OSError, ValueError):
                # Preserve the original evidence; do not overwrite corrupt state.
                self.persistence_error = True

    def ingest(self, reports: list[dict], *, reported_at: float | None = None,
               firmware_version: str | None = None) -> dict:
        if self.persistence_error:
            raise ValueError("Energy persistence unavailable")
        if self.model not in ("c1000_gen2", "c2000_gen2") or type(reports) is not list or not 1 <= len(reports) <= 32:
            raise ValueError("Invalid native energy reports")
        groups = []
        for report in reports:
            if (type(report) is not dict or report.get("protobuf_name") != REPORT_NAME
                    or report.get("units_verified") is not False):
                raise ValueError("Invalid native energy report")
            counters = energy_groups(report.get("groups"))
            if counters is None:
                raise ValueError("Invalid native energy counters")
            groups.append(counters)
        previous = self.state
        receipt = time.time() if reported_at is None else reported_at
        if type(receipt) not in (int, float) or not 0 < receipt < 253402300800 or not math.isfinite(receipt):
            raise ValueError("Invalid energy receipt time")
        if previous is not None and receipt < previous["reported_at"]:
            raise ValueError("Energy receipt time regressed")
        # Upload event ordering/measurement timestamps are not established.
        # Keep only the last member as a received snapshot, never integrate a batch.
        raw = groups[-1]
        decreased = previous is not None and any(
            key in previous["groups"].get(group, {}).get("raw", {})
            and value < previous["groups"][group]["raw"][key]
            for group, counters in raw.items() for key, value in counters.items())
        boundary = decreased or len(groups) > 1
        state = {"schema_version": 1, "source": SOURCE, "model": self.model,
            "firmware_version": firmware_version, "units_verified": False, "reported_at": receipt,
            "received_reports": (previous["received_reports"] if previous else 0) + len(groups),
            "batch_reports": len(groups), "counter_epoch": (previous["counter_epoch"] if previous else 1) + int(boundary and previous is not None),
            "counter_epoch_started_at": receipt if previous is None or boundary else previous["counter_epoch_started_at"],
            "continuity": "batch_order_unknown" if len(groups) > 1 else "counter_decreased" if decreased else "increasing" if previous else "first_report",
            "groups": {group: {"raw": counters} for group, counters in raw.items()}}
        result = validate_native_energy(state, model=self.model, now=receipt)
        if result is None or result["received_reports"] > MAX_INTEGER:
            raise ValueError("Invalid native energy state")
        if self.path is not None:
            private_write(self.path, json.dumps(result, separators=(",", ":")).encode())
        self.state = result
        return self.snapshot(now=receipt)

    def snapshot(self, *, now: float | None = None) -> dict | None:
        return validate_native_energy(self.state, model=self.model, now=now)
