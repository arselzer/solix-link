"""Guided private calibration records from cached GET or saved snapshots."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time

from .charging_preview_cli import read_document
from .energy_calibration import BOUNDARIES, MODELS, append_observation, calibration_report, new_session, validate_session


def add_parser(subcommands) -> None:
    command = subcommands.add_parser("energy-calibration", help="Record independent meter/cached-counter evidence; never changes stations or conversions")
    actions = command.add_subparsers(dest="calibration_action", required=True)
    create = actions.add_parser("create", help="Create a new private comparison session")
    create.add_argument("--session", type=Path, required=True)
    create.add_argument("--model", choices=MODELS, required=True)
    create.add_argument("--counter", required=True, help="Original: counter_1_raw…counter_8_raw; Gen 2: standard.ac_input_energy_raw, etc.")
    create.add_argument("--reference", choices=("ac_input", "ac_output"), required=True, help="Independent meter's physical location")
    record = actions.add_parser("record", help="Add one fresh upload and independent meter reading")
    record.add_argument("--session", type=Path, required=True)
    source = record.add_mutually_exclusive_group(required=True)
    source.add_argument("--snapshot-file", type=Path)
    source.add_argument("--gateway-url")
    record.add_argument("--gateway-token-file", type=Path)
    record.add_argument("--name", help="Exact station name for cached gateway GET only")
    record.add_argument("--meter-kwh", type=float)
    record.add_argument("--meter-timestamp", type=float, help="UNIX time when the independent reading was taken")
    record.add_argument("--guided", action="store_true", help="Prompt for the independent meter reading in a terminal")
    record.add_argument("--boundary", choices=BOUNDARIES, default="none", help="Record an observed boundary; never restarts a station")
    report = actions.add_parser("report", help="Compare intervals; do not apply a fitted scale")
    report.add_argument("--session", type=Path, required=True)
    report.add_argument("--max-report-gap", type=int, default=3600)
    report.add_argument("--max-timing-offset", type=int, default=30)
    report.add_argument("--candidate-wh-per-unit", type=float)


@contextmanager
def session_lock(path: Path):
    import fcntl
    if path.parent.is_symlink():
        raise ValueError("Calibration directory must not be a symlink")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = path.parent.stat()
    if parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise ValueError("Use a private owner-only calibration directory")
    lock = path.with_name(path.name + ".lock")
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
            raise ValueError("Unsafe calibration lock")
        fcntl.flock(fd, fcntl.LOCK_EX)
        if path.exists() or path.is_symlink():
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
                raise ValueError("Calibration session must be an owner-only regular file")
        yield
    finally:
        os.close(fd)


def save(path: Path, value: dict) -> None:
    fd, name = tempfile.mkstemp(prefix=".calibration-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def run(args) -> dict:
    if args.calibration_action == "create":
        with session_lock(args.session):
            if args.session.exists():
                raise ValueError("Calibration session already exists")
            save(args.session, new_session(args.model, args.counter, args.reference, now=time.time()))
        return {"created": True, "observations": 0, "next": "Record an independent meter reading with a fresh cached upload; repeat after a stable-load interval."}
    if args.calibration_action == "report":
        with session_lock(args.session):
            session = read_document(args.session, 1_048_576)
        return calibration_report(session, max_report_gap=args.max_report_gap,
            max_timing_offset=args.max_timing_offset, candidate_wh_per_unit=args.candidate_wh_per_unit)
    if args.snapshot_file:
        if args.gateway_token_file or args.name:
            raise ValueError("Gateway arguments require --gateway-url")
        snapshot = read_document(args.snapshot_file, 1_048_576)
    else:
        if not args.name:
            raise ValueError("Gateway capture requires --name")
        from .gateway_client import GatewayClient
        snapshot = GatewayClient(args.gateway_url, args.gateway_token_file).snapshot(args.name)
    reading, timestamp = args.meter_kwh, args.meter_timestamp
    if args.guided:
        if not sys.stdin.isatty():
            raise ValueError("Guided calibration requires a terminal")
        print("Read the independent AC meter now. No station command will be sent.", file=sys.stderr)
        if reading is None:
            reading = float(input("Meter cumulative kWh: "))
        if timestamp is None:
            entered = input("Reading UNIX timestamp (blank = now): ").strip()
            timestamp = float(entered) if entered else time.time()
    if reading is None or timestamp is None:
        raise ValueError("Supply --meter-kwh and --meter-timestamp, or use --guided")
    with session_lock(args.session):
        session = validate_session(read_document(args.session, 1_048_576))
        session = append_observation(session, snapshot, meter_kwh=reading, meter_timestamp=timestamp,
                                     now=time.time(), boundary=args.boundary)
        save(args.session, session)
    return {"observations": len(session["observations"]), "station_commands": 0,
            "next": "Repeat after a stable-load interval, then run energy-calibration report."}
