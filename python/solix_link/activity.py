"""Cached UPS observations and bounded, optional private activity persistence."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import sqlite3
from threading import RLock
import time

from .settings_compare import compare_settings
from .settings_export import export_settings
from .plan_readback import validate_plan_readback


def number(value, low=0, high=253402300799):
    return type(value) in (int, float) and low <= value <= high and math.isfinite(value)


def command_readback(command: str, parameters: dict, snapshot: dict) -> dict:
    """Describe cached values without claiming a fresh request/response pairing."""
    fields = {"set-charge-power": ("ac_charging_power_limit_w", "watts"),
        "set-charge-cap": ("max_charge_percentage", "upper"),
        "set-discharge-floor": ("min_charge_percentage", "lower"),
        "set-backup-reserve": ("backup_reserve_percentage", "reserve"),
        "set-display-timeout": ("display_timeout_seconds", "seconds"),
        "set-display-brightness": ("display_brightness", "level"),
        "set-port-memory": ("port_memory_enabled", "enabled"),
        "set-fast-charge": ("ac_fast_charge_enabled", "enabled"),
        "set-light": ("light_mode", "mode"),
        "set-temperature-unit": ("temperature_unit_fahrenheit", "fahrenheit"),
        "set-off-grid-alert": ("ac_off_grid_alert_enabled", "enabled"),
        "set-device-timeout": ("device_timeout_minutes", "minutes"),
        "set-ac-power-saving": ("ac_power_saving_mode_enabled", "enabled"),
        "set-dc-power-saving": ("dc_power_saving_mode_enabled", "enabled")}
    if command == "set-clock-brightness":
        fields[command] = ("clock_screen_first_brightness_flag_raw" if parameters["window"] == 1
                           else "clock_screen_second_brightness_flag_raw", "high")
    exported = export_settings(snapshot)
    match = None
    if command in fields and exported["snapshot_fresh"]:
        key, parameter = fields[command]
        if key in exported["settings"]:
            match = exported["settings"][key] == parameters[parameter]
    if command == "set-tou-plan" and exported["tou_plan_fresh"]:
        plan = validate_plan_readback(exported["tou_plan_readback"])
        match = (plan["enabled"] == parameters["enabled"] and plan["periods"] == parameters["periods"])
    if command == "return-grid" and exported["snapshot_fresh"]:
        match = snapshot.get("power_flow") == "grid" if snapshot.get("power_flow") in ("grid", "battery", "transitioning") else None
    return {"source": "cached_telemetry", "reported_values_match": match,
        "independent_confirmation": False, "physical_behavior_verified": False}


def ups_state(snapshot: dict, *, now: float | None = None, reserve: int = 20) -> dict:
    now = time.time() if now is None else now
    exported = export_settings(snapshot, now=now)
    fresh = exported["snapshot_fresh"]
    metrics = snapshot.get("metrics", {})
    reported = exported["settings"].get("backup_reserve_percentage")
    threshold = max(reserve, reported or 0)
    mains, battery = metrics.get("ac_input_connected"), metrics.get("battery_percentage")
    return {"schema_version": 1, "telemetry_available": fresh,
        "mains_connected": bool(mains) if fresh and type(mains) is int and mains in (0, 1) else None,
        "battery_reserve_low": battery < threshold if fresh and type(battery) is int and 0 <= battery <= 100 else None,
        "effective_reserve_percentage": threshold, "reserve_hysteresis_percentage": 5}


@dataclass
class _State:
    identity: tuple
    seen: float | None = None
    fresh: bool | None = None
    mains: bool | None = None
    pending: tuple[bool, float, float] | None = None
    low: bool | None = None
    gap: bool = False
    settings: dict | None = None
    public: dict = field(default_factory=dict)


class UpsTracker:
    """Require distinct reports and five seconds before a mains transition.

    Polling the same cached report cannot confirm a transition. Missing data
    means communications unavailable, never mains disconnected.
    """

    def __init__(self, *, reserve: int = 20, debounce: float = 5):
        if type(reserve) is not int or not 1 <= reserve <= 100 or not number(debounce, 0, 60):
            raise ValueError("Invalid UPS observation thresholds")
        self.reserve, self.debounce = reserve, debounce
        self.states: dict[str, _State] = {}
        self._last_now: float | None = None

    def observe(self, snapshots: list[dict], *, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        if not number(now):
            raise ValueError("Invalid observation timestamp")
        if self._last_now is not None and now < self._last_now:
            self.states.clear()
        self._last_now = now
        events = []
        for snapshot in snapshots:
            name = snapshot["name"]
            identity = (snapshot.get("model"), snapshot.get("protocol"))
            state = self.states.get(name)
            if state is None or state.identity != identity:
                state = self.states[name] = _State(identity)
            public = ups_state(snapshot, now=now, reserve=self.reserve)
            seen = snapshot.get("last_seen_timestamp")
            fresh = public["telemetry_available"] and number(seen) and (state.seen is None or seen >= state.seen)
            def emit(kind, **details):
                events.append({"name": name, "timestamp": now, "kind": kind, "details": details})
            if state.fresh is not None and fresh != state.fresh:
                emit("telemetry_restored" if fresh else "telemetry_lost")
                state.gap = True
            state.fresh = bool(fresh)
            if not fresh:
                state.pending = None
                public.update(telemetry_available=False, mains_connected=None, battery_reserve_low=None)
                state.public = public
                continue
            if state.seen is not None and seen <= state.seen:
                public["battery_reserve_low"] = state.low
                state.public = public
                continue
            mains = public["mains_connected"]
            if mains is None:
                state.pending = None
            elif state.mains is None:
                state.mains = mains
            elif mains == state.mains:
                state.pending = None
            elif state.pending is None or state.pending[0] != mains:
                state.pending = (mains, now, seen)
            elif now - state.pending[1] >= self.debounce and seen > state.pending[2]:
                emit("mains_restored" if mains else "mains_lost", interval_unknown=state.gap)
                state.mains, state.pending, state.gap = mains, None, False
            low = public["battery_reserve_low"]
            if low is not None:
                battery = snapshot["metrics"]["battery_percentage"]
                if state.low is True and battery < min(100, public["effective_reserve_percentage"] + 5):
                    low = True
                if (state.low is not None and low != state.low) or (state.low is None and low):
                    emit("battery_low" if low else "battery_recovered", battery_percentage=battery,
                         reserve_percentage=public["effective_reserve_percentage"])
                state.low = low
            else:
                state.low = None
            public["battery_reserve_low"] = state.low
            exported = export_settings(snapshot, now=now)
            if state.settings is not None:
                comparison = compare_settings(state.settings, exported)
                if comparison["changes"]:
                    emit("settings_changed", **comparison)
            state.settings, state.seen, state.public = exported, seen, public
        return events

    def describe(self, snapshot: dict, *, now: float | None = None) -> dict:
        public = ups_state(snapshot, now=now, reserve=self.reserve)
        state = self.states.get(snapshot["name"])
        if state is not None and state.identity == (snapshot.get("model"), snapshot.get("protocol")):
            if not state.fresh or (number(snapshot.get("last_seen_timestamp"))
                    and state.seen is not None and snapshot["last_seen_timestamp"] < state.seen):
                public.update(telemetry_available=False, mains_connected=None, battery_reserve_low=None)
            elif public["telemetry_available"] and state.seen == snapshot.get("last_seen_timestamp"):
                public["battery_reserve_low"] = state.low
        return public


class ActivityStore:
    """Bounded session history by default; SQLite persistence requires opt-in.

    Callers supply sanitized records, never raw requests or exception messages.
    No station or network access occurs here.
    """

    def __init__(self, path: Path | None = None, *, retention_days: int = 7, max_records: int = 10000):
        if type(retention_days) is not int or not 1 <= retention_days <= 365:
            raise ValueError("Activity retention must be 1–365 days")
        if type(max_records) is not int or not 1 <= max_records <= 100000:
            raise ValueError("Invalid activity record limit")
        self.retention_days, self.max_records = retention_days, max_records
        self._lock, self._db, self._descriptor = RLock(), None, None
        self._records, self._next, self._closed = deque(maxlen=max_records), 1, False
        if path is not None:
            try:
                import fcntl
                from .history import HistoryStore
                path, self._descriptor = HistoryStore._private_path(path)
                fcntl.flock(self._descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=5)
                opened, current = os.fstat(self._descriptor), path.lstat()
                if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                    raise ValueError
                version = self._db.execute("PRAGMA user_version").fetchone()[0]
                tables = {row[0] for row in self._db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                if (version == 0 and tables) or version not in (0, 1) or (version == 1 and tables != {"activity"}):
                    raise ValueError
                self._db.execute("PRAGMA journal_mode=DELETE")
                self._db.execute("CREATE TABLE IF NOT EXISTS activity (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                                 "name TEXT NOT NULL, timestamp REAL NOT NULL, record TEXT NOT NULL)")
                self._db.execute("PRAGMA user_version=1")
                self._db.commit()
            except BaseException:
                self.close()
                raise ValueError("Unable to open private activity database") from None

    def append(self, record: dict) -> int:
        raw = json.dumps(record, allow_nan=False, separators=(",", ":"))
        if len(raw.encode()) > 32768 or not number(record.get("timestamp")):
            raise ValueError("Invalid activity record")
        with self._lock:
            if self._closed:
                raise ValueError("Activity store is closed")
            if self._db is None:
                identifier = self._next
                self._next += 1
                self._records.append({"id": identifier, **json.loads(raw)})
            else:
                with self._db:
                    identifier = self._db.execute("INSERT INTO activity(name,timestamp,record) VALUES (?,?,?)",
                        (record["name"], record["timestamp"], raw)).lastrowid
                    self._db.execute("DELETE FROM activity WHERE timestamp < ?", (record["timestamp"]-self.retention_days*86400,))
                    self._db.execute("DELETE FROM activity WHERE id NOT IN "
                                     "(SELECT id FROM activity ORDER BY id DESC LIMIT ?)", (self.max_records,))
            return identifier

    def query(self, names: set[str], *, limit: int = 100, after: int = 0, now: float | None = None) -> dict:
        if type(limit) is not int or not 1 <= limit <= 200 or type(after) is not int or not 0 <= after <= 2**63-1:
            raise ValueError("Invalid activity query")
        cutoff = (time.time() if now is None else now) - self.retention_days*86400
        with self._lock:
            if self._closed:
                raise ValueError("Activity store is closed")
            if self._db is None:
                records = [json.loads(json.dumps(row)) for row in self._records
                           if row["name"] in names and row["id"] > after and row["timestamp"] >= cutoff]
                records = records[:limit] if after else records[-limit:]
            elif not names:
                records = []
            else:
                marks = ",".join("?" for _ in names)
                direction = "ASC" if after else "DESC"
                rows = self._db.execute(f"SELECT id,record FROM activity WHERE name IN ({marks}) "
                    f"AND id > ? AND timestamp >= ? ORDER BY id {direction} LIMIT ?", (*sorted(names), after, cutoff, limit)).fetchall()
                records = [{**json.loads(row[1]), "id": row[0]} for row in rows]
                records.sort(key=lambda row: row["id"])
            return {"schema_version": 1, "persisted": self._db is not None, "retention_days": self.retention_days,
                "max_records": self.max_records, "complete": False, "records": records}

    def close(self):
        with self._lock:
            self._closed = True
            if self._db is not None:
                self._db.close()
                self._db = None
            if self._descriptor is not None:
                os.close(self._descriptor)
                self._descriptor = None
