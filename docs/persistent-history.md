# Persistent power and battery history

History is **optional**. The gateway creates no history database unless a
history file is configured. Recording uses cached snapshots, so it introduces
no Bluetooth requests, native MQTT requests or settings writes. The existing
browser session graph remains available without persistent history.

```sh
solix-link serve --history-file ~/.local/share/solix-link/history/readings.sqlite3 \
  --history-retention-days 7
```

`ap-service-serve` accepts the same options for native MQTT monitoring. Gateway
retention choices are 1–365 days. `GET /history` reports whether recording is
enabled; `GET /devices/{name}/history?limit=1000` returns that station's history.
Authenticated `GET /history/summary` returns at most 32 cached lifetime counter
records without scanning samples. Its sampler timestamp describes the last
successful complete recording poll, independently of the HTTP request time.
See [Home Assistant diagnostics](home-assistant-history.md),
[terminal views](terminal-history-preview.md) and
[restart verification](runtime-observation-restart.md).
These endpoints use the gateway's existing authentication. Disabling recording
does not delete a previously created database.

## Storage and limits

The private filesystem checks and process lock target Linux/POSIX. Choose a
dedicated private path, such as
`~/.local/share/solix-link/history/readings.sqlite3`. The store creates missing
directories with mode `0700` and the database with mode `0600`. Existing files
and the immediate parent must be owned by the current user and inaccessible to
other users. Symlinks, hard links, nonregular files and unrelated SQLite
databases are rejected. One process owns a database at a time; calls from that
process's worker threads are serialized.

Defaults retain seven days, at most 500,000 rows and 32 public station names.
Rows are pruned periodically and when the row cap is exceeded. SQLite can keep
freed pages for reuse, so pruning does not promise an immediate file-size
reduction. Lifetime estimates survive row pruning. Changing a station's name
starts a separate history; history contains no hardware identity to match it
to an old name.
SQLite schema version 2 adds a random persisted `generation` per station.
Version 1 migration preserves existing totals, timestamps and samples. Normal
restarts and pruning preserve generations; recreating a database produces new
ones. Before downgrading to an older gateway, restore its pre-migration backup.
Names preserve their exact public spelling, including spaces, Unicode and
quotes; no aliases or normalization are applied. They must contain 1–64
printable characters and cannot be blank or contain control characters/NUL.

Only public name/model/transport, selected readings, timestamps, gaps and
derived totals are saved. Addresses, pairing/account IDs, certificates, errors,
raw metrics and captures are excluded. Keep the database out of Git and public
backups: power and battery history can reveal usage patterns.

## What energy estimates mean

Input and output histories use **AC input watts**, **AC output watts** and
battery percentage. They do not substitute total port power when AC readings
are missing. SOC must be finite and within 0–100%; power must be finite and
within the configured 0–10,000 W domain. Invalid values are unknown, not zero.

For consecutive valid readings, estimated AC energy is:

`(previous_watts + current_watts) / 2 × interval_seconds / 3,600,000 kWh`

Both endpoints must belong to the same model/transport, with increasing
telemetry timestamps no more than 15 seconds apart by default. Each channel's
coverage counts only intervals with valid power at both ends. Unchanged fresh
cached reports are ignored; conflicting duplicates, unavailable/stale reports,
out-of-order timestamps, clock rollback, missing stations and process restarts
break continuity. A new valid reading establishes a baseline before integration
resumes. Freshness is 30 seconds for native MQTT and 90 seconds for BLE, with
at most five seconds of future clock skew accepted.

These are **derived estimates**, not meter readings. AC input includes grid
power passing through to loads; AC output can come from grid or battery.
Neither total, nor their difference, establishes energy stored in the battery,
battery capacity, solar production or conversion efficiency. Unknown intervals
are excluded rather than filled with the last known watts. Coverage seconds
and gap counts must accompany energy values.

## Query and chart contract

`HistoryStore.record(snapshots, now=None)` accepts the complete cached station
list. `query(name, since=None, until=None, limit=None, now=None)` defaults to
the last 24 hours and at most 1,000 points. Explicit queries are limited to
31 days and 2,000 points. All timestamps are **UNIX seconds**, using the
gateway's telemetry reception timestamp, not the history poll time.

Responses contain:

- `points`: timestamp, battery percentage, AC input/output watts, `gap` and
  `max_source_interval_seconds`; missing readings are `null`.
- `totals`: estimated input/output kWh, per-channel coverage seconds and gap
  count for whole intervals inside the retained requested window.
- `lifetime_totals`, `collection_start` and `generation`: persisted estimates since this
  named station's first saved report, including intervals whose rows expired.
- `window`, `limits`, `estimated: true` and `truncated`.

**`gap: true` means break before this point.** A null gap sentinel can share
the preceding report's timestamp: it marks where unknown coverage begins,
and is not another measurement. Downsampling propagates hidden gaps and
missing fields to the next selected point. Saved graphs may connect selected
points farther than 15 seconds apart only when `gap` is false; the reported
maximum source interval still describes the original valid readings.

Invalid arguments raise `ValueError`; an unknown valid station raises
`KeyError`; file/database failures raise `OSError` or `sqlite3.Error`. Error
messages do not include rejected private values. `stats()` has a fixed bounded
summary; `close()` is idempotent. Closing and reopening always resets
integration continuity.

## Verification

`PYTHONPATH=python python3 -m pytest python/tests/test_history.py -q` uses
synthetic readings to check constant/variable power, duplicate and stale
reports, gaps, restart/clock rollback, retention, bounded queries, hidden
chart gaps, privacy and concurrent thread access. It makes no station requests.
The estimates have not been calibrated against an external power meter.
