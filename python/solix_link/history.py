"""Opt-in, private cached telemetry history and explicitly estimated AC energy."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
import fcntl
import math
import os
from pathlib import Path
import sqlite3
import stat
from threading import RLock
import time
from typing import Any


_MODELS = {"c300", "c1000", "c1000_gen2", "c2000_gen2"}
_PROTOCOLS = {"legacy", "prime", "native_mqtt"}
_MAX_STATIONS = 32
_MAX_BATCH = 64
_MAX_QUERY_SECONDS = 31 * 86400
_FUTURE_SKEW_SECONDS = 5
_TOTAL_KEYS = (
    "ac_input_energy_kwh_estimate", "ac_output_energy_kwh_estimate",
    "ac_input_coverage_seconds", "ac_output_coverage_seconds", "gap_count",
)


def _valid_name(value: Any) -> bool:
    return (isinstance(value, str) and 1 <= len(value) <= 64
            and value.isprintable() and bool(value.strip()))


def _number(value: Any, lower: float, upper: float) -> float | None:
    if type(value) not in (int, float) or not lower <= value <= upper or not math.isfinite(value):
        return None
    return float(value)


def _epoch(value: Any) -> float:
    result = _number(value, 0, 253402300799)
    if result is None:
        raise ValueError("History timestamps must be finite UNIX seconds")
    return result


@dataclass(frozen=True)
class _Reading:
    timestamp: float
    model: str
    protocol: str
    battery: float | None
    input_w: float | None
    output_w: float | None


@dataclass
class _Station:
    model: str
    protocol: str
    highwater: float | None
    totals: tuple[float, float, float, float, int]
    gap_open: bool
    has_samples: bool
    collection_start: float | None = None
    previous: _Reading | None = None
    signature: _Reading | None = None
    restarted: bool = False


class HistoryStore:
    """Serialize SQLite access; callers supply the complete cached station list.

    No station requests are made. Values are AC input/output watts, not total
    port power or battery energy. Unchanged fresh timestamps are ignored.
    """

    def __init__(self, path: str | Path, *, retention_days: int = 7,
                 max_gap_seconds: float = 15, max_samples: int = 500000,
                 max_points: int = 2000, max_power_w: float = 10000,
                 clock: Callable[[], float] = time.time) -> None:
        if type(retention_days) is not int or not 1 <= retention_days <= 365:
            raise ValueError("History retention must be 1–365 whole days")
        if type(max_samples) is not int or not 1 <= max_samples <= 2000000:
            raise ValueError("History row limit must be 1–2000000")
        if type(max_points) is not int or not 1 <= max_points <= 2000:
            raise ValueError("History point limit must be 1–2000")
        gap = _number(max_gap_seconds, 0.001, 60)
        power = _number(max_power_w, 1, 10000)
        if gap is None or power is None:
            raise ValueError("History gap/power limits are outside their supported range")
        self.retention_days, self.max_samples, self.max_points = retention_days, max_samples, max_points
        self.max_gap_seconds, self.max_power_w = gap, power
        self._clock, self._lock = clock, RLock()
        self._closed = False
        self._last_now: float | None = None
        self._clock_floor: float | None = None
        self._reset_required = False
        self._last_maintenance: float | None = None
        self._path, self._descriptor = self._private_path(path)
        try:
            fcntl.flock(self._descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._db = sqlite3.connect(str(self._path), check_same_thread=False, timeout=5)
        except BaseException:
            os.close(self._descriptor)
            self._closed = True
            raise
        self._db.row_factory = sqlite3.Row
        try:
            opened, current = os.fstat(self._descriptor), self._path.lstat()
            if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                raise ValueError("History file changed while opening")
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("Unsupported history database version")
            tables = {row[0] for row in self._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if (version == 0 and tables) or (version == 1 and tables != {"stations", "samples"}):
                raise ValueError("Unrecognized history database")
            self._db.execute("PRAGMA journal_mode=DELETE")
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.execute("PRAGMA auto_vacuum=INCREMENTAL")
            with self._db:
                self._db.executescript("""
                    CREATE TABLE IF NOT EXISTS stations (
                        name TEXT PRIMARY KEY, model TEXT NOT NULL, protocol TEXT NOT NULL,
                        highwater REAL, input_kwh REAL NOT NULL DEFAULT 0,
                        output_kwh REAL NOT NULL DEFAULT 0, input_seconds REAL NOT NULL DEFAULT 0,
                        output_seconds REAL NOT NULL DEFAULT 0, gaps INTEGER NOT NULL DEFAULT 0,
                        gap_open INTEGER NOT NULL DEFAULT 1, has_samples INTEGER NOT NULL DEFAULT 0,
                        collection_start REAL
                    );
                    CREATE TABLE IF NOT EXISTS samples (
                        id INTEGER PRIMARY KEY, name TEXT NOT NULL REFERENCES stations(name),
                        timestamp REAL NOT NULL, battery REAL, input_w REAL, output_w REAL,
                        gap INTEGER NOT NULL, gap_delta INTEGER NOT NULL, interval_start REAL,
                        input_kwh REAL NOT NULL, output_kwh REAL NOT NULL,
                        input_seconds REAL NOT NULL, output_seconds REAL NOT NULL,
                        source_interval REAL
                    );
                    CREATE INDEX IF NOT EXISTS samples_station_time ON samples(name, timestamp, id);
                    CREATE INDEX IF NOT EXISTS samples_time ON samples(timestamp, id);
                    PRAGMA user_version=1;
                """)
                columns = {row[1] for row in self._db.execute("PRAGMA table_info(stations)")}
                if "collection_start" not in columns:
                    self._db.execute("ALTER TABLE stations ADD COLUMN collection_start REAL")
            self._states: dict[str, _Station] = {}
            for row in self._db.execute("SELECT * FROM stations"):
                if (not _valid_name(row["name"]) or row["model"] not in _MODELS
                        or row["protocol"] not in _PROTOCOLS or len(self._states) >= _MAX_STATIONS):
                    raise ValueError("Invalid history station metadata")
                state = _Station(row["model"], row["protocol"], row["highwater"],
                                 (row["input_kwh"], row["output_kwh"], row["input_seconds"],
                                  row["output_seconds"], row["gaps"]), bool(row["gap_open"]),
                                 bool(row["has_samples"]), row["collection_start"],
                                 restarted=bool(row["has_samples"]))
                latest = self._db.execute(
                    "SELECT * FROM samples WHERE name=? AND timestamp=? AND battery IS NOT NULL "
                    "ORDER BY id DESC LIMIT 1", (row["name"], state.highwater),
                ).fetchone()
                if latest is not None:
                    state.signature = _Reading(latest["timestamp"], state.model, state.protocol,
                                               latest["battery"], latest["input_w"], latest["output_w"])
                self._states[row["name"]] = state
            self._rows = self._db.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
            with self._db:
                self._maintain(_epoch(self._clock()), force=True)
        except BaseException:
            self._db.close()
            os.close(self._descriptor)
            self._closed = True
            raise

    @staticmethod
    def _private_path(value: str | Path) -> tuple[Path, int]:
        path = Path(value).expanduser().absolute()
        if ".." in path.parts:
            raise ValueError("History path must not contain parent traversal")
        for parent in reversed((path.parent, *path.parent.parents)):
            try:
                info = parent.lstat()
            except FileNotFoundError:
                parent.mkdir(mode=0o700)
                info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise ValueError("History path must use real directories")
        owner = os.getuid()
        parent_info = path.parent.lstat()
        if parent_info.st_uid != owner or parent_info.st_mode & 0o077:
            raise ValueError("History requires an owner-only directory")
        for candidate in (path, path.with_name(path.name + "-journal"),
                          path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
            try:
                info = candidate.lstat()
            except FileNotFoundError:
                continue
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner
                    or info.st_mode & 0o077 or info.st_nlink != 1):
                raise ValueError("History files must be owner-only regular files")
        try:
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != owner or info.st_mode & 0o077 or info.st_nlink != 1:
                raise ValueError("History files must be owner-only regular files")
        except BaseException:
            os.close(descriptor)
            raise
        return path, descriptor

    def _check_open(self) -> None:
        if self._closed:
            raise ValueError("History store is closed")

    def _limits(self) -> dict[str, int | float]:
        return {"retention_days": self.retention_days, "max_gap_seconds": self.max_gap_seconds,
                "max_power_w": self.max_power_w, "max_samples": self.max_samples,
                "max_points": self.max_points, "max_query_seconds": _MAX_QUERY_SECONDS}

    def _stats(self) -> dict[str, Any]:
        starts = [state.collection_start for state in self._states.values() if state.collection_start is not None]
        return {"samples": self._rows, "stations": len(self._states),
                "collection_start": min(starts) if starts else None,
                "retention_days": self.retention_days, "max_gap_seconds": self.max_gap_seconds,
                "max_samples": self.max_samples, "max_points": self.max_points, "estimated": True}

    def stats(self) -> dict[str, Any]:
        with self._lock:
            self._check_open()
            return self._stats()

    def _save_station(self, name: str, state: _Station) -> None:
        self._db.execute("""INSERT INTO stations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET model=excluded.model, protocol=excluded.protocol,
            highwater=excluded.highwater, input_kwh=excluded.input_kwh, output_kwh=excluded.output_kwh,
            input_seconds=excluded.input_seconds, output_seconds=excluded.output_seconds,
            gaps=excluded.gaps, gap_open=excluded.gap_open, has_samples=excluded.has_samples,
            collection_start=excluded.collection_start""",
            (name, state.model, state.protocol, state.highwater, *state.totals,
             int(state.gap_open), int(state.has_samples), state.collection_start))

    def _insert(self, name: str, timestamp: float, reading: _Reading | None, *, gap: bool,
                gap_delta: int = 0, interval_start: float | None = None,
                input_kwh: float = 0, output_kwh: float = 0,
                input_seconds: float = 0, output_seconds: float = 0,
                source_interval: float | None = None) -> None:
        self._db.execute("""INSERT INTO samples
            (name, timestamp, battery, input_w, output_w, gap, gap_delta, interval_start,
             input_kwh, output_kwh, input_seconds, output_seconds, source_interval)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, timestamp, reading.battery if reading else None,
             reading.input_w if reading else None, reading.output_w if reading else None,
             int(gap), gap_delta, interval_start, input_kwh, output_kwh,
             input_seconds, output_seconds, source_interval))
        self._rows += 1

    def _break(self, name: str, state: _Station, now: float, *, marker: bool = True) -> int:
        counted = int(state.has_samples and not state.gap_open)
        if counted:
            state.totals = (*state.totals[:4], state.totals[4] + 1)
            if marker:
                # Unknown coverage starts after the last accepted report. A
                # poll-time marker can sort after a newly observed delayed
                # report and falsely put an integrated pair across that gap.
                self._insert(name, state.highwater if state.highwater is not None else now,
                             None, gap=True, gap_delta=1)
        state.previous = None
        state.gap_open = True
        state.restarted = False
        return counted

    def _maintain(self, now: float, *, force: bool = False) -> None:
        if force or self._last_maintenance is None or now - self._last_maintenance >= 60:
            removed = self._db.execute("DELETE FROM samples WHERE timestamp < ?",
                                       (now - self.retention_days * 86400,)).rowcount
            self._rows -= removed
            self._last_maintenance = now
        if self._rows > self.max_samples:
            removed = self._db.execute("DELETE FROM samples WHERE id IN "
                                       "(SELECT id FROM samples ORDER BY timestamp, id LIMIT ?)",
                                       (self._rows - self.max_samples,)).rowcount
            self._rows -= removed

    def record(self, snapshots: Iterable[Mapping[str, Any]], now: float | None = None) -> dict[str, Any]:
        """Record unique reports from a full cache poll; never query a station."""
        with self._lock:
            self._check_open()
            try:
                timestamp = _epoch(self._clock() if now is None else now)
                batch = []
                for snapshot in snapshots:
                    if not isinstance(snapshot, Mapping) or len(batch) >= _MAX_BATCH:
                        raise ValueError("History requires at most 64 snapshot objects")
                    batch.append(snapshot)
            except (TypeError, ValueError):
                self._reset_required = True
                raise ValueError("Invalid history snapshot batch or timestamp") from None
            backup = {name: replace(state) for name, state in self._states.items()}
            old_rows, old_maintenance = self._rows, self._last_maintenance
            try:
                with self._db:
                    clock_changed = (self._last_now is not None and
                                     (timestamp < self._last_now
                                      or timestamp - self._last_now > self.max_gap_seconds))
                    if clock_changed or self._reset_required:
                        for name, state in self._states.items():
                            self._break(name, state, timestamp)
                            self._save_station(name, state)
                    rollback = self._clock_floor is not None and timestamp < self._clock_floor
                    seen: set[str] = set()
                    names = [item.get("name") for item in batch]
                    duplicates = {name for name in names if isinstance(name, str) and names.count(name) > 1}
                    for snapshot in batch:
                        name = snapshot.get("name")
                        if not _valid_name(name):
                            continue
                        if name in duplicates:
                            state = self._states.get(name)
                            if state and name not in seen:
                                self._break(name, state, timestamp)
                                self._save_station(name, state)
                            seen.add(name)
                            continue
                        seen.add(name)
                        self._record_one(name, snapshot, timestamp, rollback)
                    for name, state in self._states.items():
                        if name not in seen:
                            self._break(name, state, timestamp)
                            self._save_station(name, state)
                    self._maintain(timestamp)
            except BaseException:
                self._states, self._rows, self._last_maintenance = backup, old_rows, old_maintenance
                self._reset_required = True
                raise
            self._last_now = timestamp
            self._clock_floor = max(timestamp, self._clock_floor or 0)
            self._reset_required = False
            return self._stats()

    def _record_one(self, name: str, snapshot: Mapping[str, Any], now: float, rollback: bool) -> None:
        model, protocol = snapshot.get("model"), snapshot.get("protocol")
        state = self._states.get(name)
        if (not isinstance(model, str) or not isinstance(protocol, str)
                or model not in _MODELS or protocol not in _PROTOCOLS):
            if state:
                self._break(name, state, now)
                self._save_station(name, state)
            return
        if state is None:
            if len(self._states) >= _MAX_STATIONS:
                raise ValueError("History supports at most 32 stations")
            state = _Station(model, protocol, None, (0, 0, 0, 0, 0), True, False)
            self._states[name] = state
            self._save_station(name, state)
        if state.restarted:
            self._break(name, state, now)
        latest = _number(snapshot.get("last_seen_timestamp"), 0, now + _FUTURE_SKEW_SECONDS)
        freshness = 30 if protocol == "native_mqtt" else 90
        metrics = snapshot.get("metrics")
        if (rollback or snapshot.get("connected") is not True or snapshot.get("available") is not True
                or latest is None or now - latest >= freshness or not isinstance(metrics, Mapping)):
            self._break(name, state, now)
            self._save_station(name, state)
            return
        values = []
        invalid = False
        for key, upper in (("battery_percentage", 100), ("ac_input_power_w", self.max_power_w),
                           ("ac_output_power_w", self.max_power_w)):
            raw = metrics.get(key)
            value = _number(raw, 0, upper) if raw is not None else None
            invalid = invalid or (raw is not None and value is None)
            values.append(value)
        reading = _Reading(latest, model, protocol, *values)
        if state.highwater is not None and latest <= state.highwater:
            if (latest < state.highwater or invalid or reading != state.signature
                    or (model, protocol) != (state.model, state.protocol)):
                self._break(name, state, now)
                self._save_station(name, state)
            return
        previous = state.previous
        context_changed = (model, protocol) != (state.model, state.protocol)
        interval = latest - previous.timestamp if previous else None
        break_pair = invalid or context_changed or (interval is not None and interval > self.max_gap_seconds)
        gap_delta = self._break(name, state, now, marker=False) if break_pair else 0
        if break_pair:
            previous = None
        state.model, state.protocol, state.highwater, state.signature = model, protocol, latest, reading
        pair = previous is not None and interval is not None and 0 < interval <= self.max_gap_seconds
        input_seconds = interval if pair and previous.input_w is not None and reading.input_w is not None else 0
        output_seconds = interval if pair and previous.output_w is not None and reading.output_w is not None else 0
        input_kwh = (previous.input_w + reading.input_w) * input_seconds / 7200000 if input_seconds else 0
        output_kwh = (previous.output_w + reading.output_w) * output_seconds / 7200000 if output_seconds else 0
        self._insert(name, latest, reading, gap=state.gap_open, gap_delta=gap_delta,
                     interval_start=previous.timestamp if pair else None,
                     input_kwh=input_kwh, output_kwh=output_kwh,
                     input_seconds=input_seconds, output_seconds=output_seconds,
                     source_interval=interval if pair else None)
        state.totals = (state.totals[0] + input_kwh, state.totals[1] + output_kwh,
                        state.totals[2] + input_seconds, state.totals[3] + output_seconds, state.totals[4])
        if not state.has_samples:
            state.collection_start = latest
        state.has_samples = True
        state.previous = None if invalid else reading
        state.gap_open = invalid
        self._save_station(name, state)

    def query(self, name: str, *, since: float | None = None, until: float | None = None,
              limit: int | None = None, now: float | None = None) -> dict[str, Any]:
        """Return bounded points and whole-pair estimates for the requested window.

        ``gap`` means break before this point, including gaps hidden by
        downsampling. Null fields are unknown, never zero. Window totals count
        only intervals with both endpoints inside the retained requested window.
        """
        with self._lock:
            self._check_open()
            if not _valid_name(name):
                raise ValueError("Invalid history station name")
            if name not in self._states:
                raise KeyError("Unknown history station")
            current = _epoch(self._clock() if now is None else now)
            end = current if until is None else _epoch(until)
            start = max(0, end - 86400) if since is None else _epoch(since)
            count_limit = min(1000, self.max_points) if limit is None else limit
            if (type(count_limit) is not int or not 1 <= count_limit <= self.max_points
                    or end > current + _FUTURE_SKEW_SECONDS or start > end
                    or end - start > _MAX_QUERY_SECONDS):
                raise ValueError("History query exceeds time/point limits")
            start = max(start, current - self.retention_days * 86400)
            if start > end:
                start = end
            with self._db:
                self._maintain(current)
            oldest = self._db.execute("SELECT MIN(timestamp) FROM samples WHERE name=?", (name,)).fetchone()[0]
            if oldest is not None:
                start = min(end, max(start, oldest))
            bounds = (name, start, end)
            count = self._db.execute("SELECT COUNT(*) FROM samples WHERE name=? AND timestamp BETWEEN ? AND ?",
                                     bounds).fetchone()[0]
            aggregates = self._db.execute("""SELECT
                COALESCE(SUM(CASE WHEN interval_start >= ? THEN input_kwh ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN interval_start >= ? THEN output_kwh ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN interval_start >= ? THEN input_seconds ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN interval_start >= ? THEN output_seconds ELSE 0 END), 0),
                COALESCE(SUM(gap_delta), 0)
                FROM samples WHERE name=? AND timestamp BETWEEN ? AND ?""",
                (start, start, start, start, *bounds)).fetchone()
            size = min(count, count_limit)
            indices = ({count - 1} if size == 1 else
                       {index * (count - 1) // (size - 1) for index in range(size)}) if size else set()
            points = []
            crossed_gap = False
            max_interval: float | None = None
            cursor = self._db.execute("SELECT * FROM samples WHERE name=? AND timestamp BETWEEN ? AND ? "
                                      "ORDER BY timestamp, id", bounds)
            for index, row in enumerate(cursor):
                crossed_gap = crossed_gap or bool(row["gap"])
                if count > count_limit and any(row[key] is None for key in ("battery", "input_w", "output_w")):
                    crossed_gap = True
                if row["source_interval"] is not None:
                    max_interval = max(max_interval or 0, row["source_interval"])
                if index in indices:
                    points.append({"timestamp": row["timestamp"], "battery_percentage": row["battery"],
                                   "ac_input_power_w": row["input_w"], "ac_output_power_w": row["output_w"],
                                   "gap": crossed_gap, "max_source_interval_seconds": max_interval})
                    crossed_gap, max_interval = False, None
            state = self._states[name]
            return {"name": name, "model": state.model, "protocol": state.protocol, "estimated": True,
                    "collection_start": state.collection_start,
                    "points": points, "totals": dict(zip(_TOTAL_KEYS, aggregates)),
                    "lifetime_totals": dict(zip(_TOTAL_KEYS, state.totals)),
                    "window": {"since": start, "until": end}, "limits": self._limits(),
                    "truncated": count > count_limit}

    def summary(self) -> dict[str, Any]:
        """Copy bounded persisted counters without querying or scanning sample rows.

        ``updated_at`` is the last successful complete cache poll, not the HTTP
        request time. ``gap_open`` describes integration continuity; it does
        not establish that every power channel was present in a report.
        Collection start identifies the first accepted report for a public
        name, not the device or a globally unique database reset identifier.
        """
        with self._lock:
            self._check_open()
            stations = []
            for name, state in self._states.items():
                counters = state.totals
                if (len(counters) != 5 or any(_number(value, 0, 1e18) is None for value in counters[:4])
                        or type(counters[4]) is not int or not 0 <= counters[4] <= 2**53):
                    raise ValueError("Invalid history counters")
                for timestamp in (state.collection_start, state.highwater):
                    if timestamp is not None:
                        _epoch(timestamp)
                stations.append({"name": name, "model": state.model, "protocol": state.protocol,
                                 "collection_start": state.collection_start,
                                 "last_seen_timestamp": state.highwater,
                                 "lifetime_totals": dict(zip(_TOTAL_KEYS, counters)),
                                 "gap_open": state.gap_open})
            return {"schema_version": 1, "estimated": True, "updated_at": self._last_now,
                    "limits": self._limits(), "stations": stations}

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._db.close()
                os.close(self._descriptor)
                self._closed = True
