#!/usr/bin/env python3
"""Pure validation of private observation/restart evidence; no service actions."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

MODELS = {"c1000", "c1000_gen2", "c2000_gen2"}
ENERGY = ("input_kwh", "output_kwh", "input_seconds", "output_seconds")


def number(value, lower=0, upper=1e18):
    if type(value) not in (int, float) or not lower <= value <= upper or not math.isfinite(value):
        raise ValueError("Invalid evidence number")
    return value


def verify_observation(document):
    """Require the original complete, stable three-station 48-hour capture."""
    if not isinstance(document, dict):
        raise ValueError("Missing observation evidence")
    if (document.get("completed") is not True or document.get("read_only") is not True
            or document.get("duration_hours") != 48 or document.get("interval_seconds") != 30
            or document.get("stop_reason") != "duration_reached"
            or type(document.get("request_errors")) is not int or document["request_errors"] != 0):
        raise ValueError("Observation not successfully completed")
    count = document.get("samples")
    if type(count) is not int or not 5000 <= count <= 5761:
        raise ValueError("Insufficient observation samples")
    start = number(document.get("started_at"), upper=253402300799)
    finish = number(document.get("finished_at"), upper=253402300799)
    if not 48 * 3600 - 60 <= finish - start <= 48 * 3600 + 120:
        raise ValueError("Observation clock/duration mismatch")
    stations, baseline = document.get("stations"), document.get("baseline")
    if (not isinstance(stations, dict) or set(stations) != MODELS
            or not isinstance(baseline, dict) or set(baseline) != MODELS):
        raise ValueError("Unexpected station set")
    for model in MODELS:
        state, settings = stations[model], baseline[model]
        if (not isinstance(state, dict) or state.get("samples") != count
                or not isinstance(settings, dict) or type(settings.get("ac_output_enabled")) is not int
                or settings["ac_output_enabled"] != 1):
            raise ValueError("Incomplete station observation")
        for key in ("unavailable_samples", "empty_settings_samples", "nonempty_changed_samples",
                    "ac_not_enabled_samples"):
            if type(state.get(key)) is not int or state[key] != 0:
                raise ValueError("Station changed or became unavailable")
        if number(state.get("max_report_age_seconds")) > 30:
            raise ValueError("Station telemetry was stale")
    return {"completed": True, "samples": count, "station_count": 3,
            "station_commands_sent": 0}


def verify_restart(before, after):
    """Compare atomic SQLite snapshots taken while stopped and after recovery.

    The caller supplies ALL rows newer than before.max_sample_id, not merely a
    decimated HTTP history. A first measurement must open a fresh zero-energy
    segment. Lifetime increases must exactly match the captured new rows.
    """
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ValueError("Missing restart evidence")
    old_rows, new_rows = before.get("stations"), after.get("stations")
    samples = after.get("new_samples")
    if (not isinstance(old_rows, list) or not isinstance(new_rows, list)
            or len(old_rows) != 3 or len(new_rows) != 3
            or not isinstance(samples, list) or not 3 <= len(samples) <= 512):
        raise ValueError("Incomplete restart evidence")
    def index(rows):
        result = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("name"), str) or not row["name"]:
                raise ValueError("Invalid station row")
            if row["name"] in result:
                raise ValueError("Duplicate station row")
            result[row["name"]] = row
        if {s.get("model") for s in result.values()} != MODELS:
            raise ValueError("Unexpected model set")
        return result
    old, new = index(old_rows), index(new_rows)
    if set(old) != set(new):
        raise ValueError("History station names changed")
    last_id = before.get("max_sample_id")
    if type(last_id) is not int or last_id < 3:
        raise ValueError("Missing previous sample index")
    final_id = after.get("max_sample_id")
    if type(final_id) is not int or final_id != last_id + len(samples):
        raise ValueError("New sample rows are missing")
    grouped = {name: [] for name in old}
    for sample in samples:
        if not isinstance(sample, dict) or sample.get("name") not in grouped:
            raise ValueError("Unexpected sample station")
        row_id = sample.get("id")
        if type(row_id) is not int or row_id != last_id + 1:
            raise ValueError("Sample rows are incomplete or reordered")
        last_id = row_id
        number(sample.get("timestamp"), upper=253402300799)
        for field in ENERGY:
            number(sample.get(field))
        grouped[sample["name"]].append(sample)
    for name, prior in old.items():
        current = new[name]
        if (current.get("model") != prior.get("model")
                or current.get("protocol") != prior.get("protocol")
                or current.get("collection_start") != prior.get("collection_start")):
            raise ValueError("History identity or collection epoch changed")
        number(prior.get("collection_start"), upper=253402300799)
        highwater = number(prior.get("highwater"), upper=253402300799)
        if number(current.get("highwater"), upper=253402300799) <= highwater:
            raise ValueError("No new station measurement")
        rows = grouped[name]
        measurements = [s for s in rows if s.get("battery") is not None]
        if not measurements:
            raise ValueError("Station history did not recover")
        previous_timestamp = highwater
        for measurement in measurements:
            number(measurement["battery"], upper=100)
            if measurement["timestamp"] <= previous_timestamp:
                raise ValueError("Station measurements are reordered")
            previous_timestamp = measurement["timestamp"]
        if current["highwater"] != previous_timestamp:
            raise ValueError("Latest station measurement was omitted")
        first = measurements[0]
        number(first["battery"], upper=100)
        if (type(first.get("gap")) is not int or first["gap"] != 1 or first.get("interval_start") is not None
                or first["timestamp"] <= highwater or any(first[k] != 0 for k in ENERGY)):
            raise ValueError("Energy was bridged across the gateway restart")
        for field in ENERGY:
            a, b = number(prior.get(field)), number(current.get(field))
            increase = math.fsum(number(s[field]) for s in rows)
            if b < a or not math.isclose(b - a, increase, rel_tol=1e-7, abs_tol=1e-9):
                raise ValueError("Lifetime counters were reset or samples are missing")
        a, b = prior.get("gaps"), current.get("gaps")
        deltas = [row.get("gap_delta") for row in rows]
        if (type(a) is not int or type(b) is not int or a < 0 or b <= a
                or any(type(delta) is not int or delta < 0 for delta in deltas)
                or b - a != sum(deltas)):
            raise ValueError("Restart continuity break was not recorded")
    return {"station_count": 3, "history_epochs_preserved": True,
            "lifetime_counters_preserved": True, "restart_energy_not_bridged": True,
            "station_commands_sent": 0}


def read(path):
    content = path.read_bytes()
    if len(content) > 1_048_576:
        raise ValueError("Evidence file too large")
    def unique(pairs):
        data = {}
        for key, value in pairs:
            if key in data:
                raise ValueError("Duplicate evidence key")
            data[key] = value
        return data
    return json.loads(content, object_pairs_hook=unique,
                      parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("Invalid number")))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observation", type=Path, required=True)
    parser.add_argument("--before", type=Path)
    parser.add_argument("--after", type=Path)
    args = parser.parse_args()
    if (args.before is None) != (args.after is None):
        parser.error("Supply both restart snapshots")
    try:
        result = {"observation": verify_observation(read(args.observation))}
        if args.before:
            result["restart"] = verify_restart(read(args.before), read(args.after))
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        parser.exit(1, "Observation/restart evidence did not pass; inspect the private files.\n")
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
