# Observation and history restart verification

The three-station trial records cached telemetry for 48 hours, every
30 seconds. It sends authenticated local `GET /devices` requests only. Its
private JSONL and summary record freshness, settings changes, availability
and AC-output state. It does not issue controls or recover a station.

## Completion gate

`tools/verify_history_restart.py` validates saved evidence without connecting
to a gateway or changing a service:

```sh
python3 tools/verify_history_restart.py \
  --observation /private/path/observation-summary.json
```

The gate requires completed duration-based capture, 5,000–5,761 samples,
the original C1000 and both Gen 2 models, no request or availability failures,
no empty/changed settings, AC enabled throughout, and fresh reports. It rejects
incomplete, truncated, malformed or inconsistent captures. A failed gate is
an investigation result; it is not permission to restart a service.

## Restart evidence

A private one-shot workflow checks the completed observation, live
settings and disabled HA charging/arming/latch state before stopping and
starting **only the HTTP gateway**. The isolated AP worker, station outputs,
certificates and profiles are preserved. It records an atomic read-only
SQLite snapshot while recording is stopped, then all newly inserted rows
after monitoring recovers. It attempts to restore gateway monitoring if
verification fails. It never restarts the AP or sends a station command.

```sh
python3 tools/verify_history_restart.py \
  --observation /private/path/observation-summary.json \
  --before /private/path/restart-history-before.json \
  --after /private/path/restart-history-after.json
```

The first new measurement for each station must start a gap with zero energy
and covered seconds, without an earlier interval endpoint. Collection epochs
must persist; lifetime counter increases must match **all** captured new rows,
and gap-count changes must match their recorded deltas. New IDs must be
contiguous and reach the snapshot's maximum ID; each station's latest accepted
report must be present. This short trial requires a valid SOC measurement and
no pruning of its new rows. Decimated HTTP history
is insufficient for this check. SQLite reads use `mode=ro` and a consistent
transaction; they do not open a second `HistoryStore` writer.

## Status and limits

The workflow is installed and locally tested; the live 48-hour result and
restart trial are still pending. The earlier observer was stopped/restarted
and then exited unsuccessfully. Its partial capture is retained. The new
observer uses separate attempt directories, preserves interruptions, and
allows at most three attempts of 64 MiB each. Successful completion triggers
the guarded check through systemd `OnSuccess`; it does not use a wall-clock
timer that could fire during a replacement attempt. An attempted restart
marker prevents automatic repetition. Failed completion or policy gates send
no controls and do not authorize a gateway restart.

A lost management connection or an old summary alone is insufficient to
establish current observer health. Check its unit state **and** last-update
age. Preserve all raw evidence in the ignored private directory. The new
code and wheel remain prepared separately; the running gateway/HA component
are held constant during the trial. This validates software continuity and estimated AC energy,
not calibrated metering, physical transfer time, or battery-side energy.

The focused regression gate is:

```sh
PYTHONPATH=python python3 -m pytest python/tests/test_history_restart_evidence.py -q
```
