"""One HTTP writer per station and bounded, restart-aware request deduplication."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import time
import uuid

from .activity import number
from .control_availability import CONTROL_METRICS
from .plan_readback import plan_is_fresh, validate_plan_readback
from .settings_export import export_settings, valid_preference

MAX_REQUESTS = 4096
REQUEST_WINDOW_SECONDS = 3600
REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{16,64}\Z", re.ASCII)
VERSION = re.compile(r"\d{1,3}(?:\.\d{1,3}){1,4}\Z", re.ASCII)
EXTRA_METRICS = {"ac_input_connected", "ac_output_enabled", "dc_output_enabled", "usage_mode",
    "active_tariff", "tou_schedule_slot_count", "clock_screen_enabled", "clock_screen_transfer_status_raw",
    "ac_output_timer_remaining_seconds", "dc_output_timer_remaining_seconds",
    "ac_output_timeout_seconds", "dc_output_timeout_seconds", "disaster_preparation_active", "software_version", "software_version_module"}
PREFERENCE_METRICS = set().union(*CONTROL_METRICS.values())


def valid_metric(key: str, value: object, model: str | None) -> bool:
    if key in ("ac_output_timer_remaining_seconds", "dc_output_timer_remaining_seconds"):
        # Protect inactive timers without trying to compare a running countdown
        # against a value captured several seconds earlier.
        return type(value) is int and value == 0
    if key in PREFERENCE_METRICS:
        return valid_preference(key, value, model)
    if key in ("software_version", "software_version_module"):
        return type(value) is str and len(value) <= 24 and VERSION.fullmatch(value) is not None
    if key == "usage_mode":
        return value in ("standard", "time_of_use")
    if key == "active_tariff":
        return value in ("none", "peak", "mid_peak", "off_peak")
    if key.endswith("_seconds") or key == "clock_screen_transfer_status_raw":
        return type(value) is int and 0 <= value <= 604800
    if key == "tou_schedule_slot_count":
        return type(value) is int and 0 <= value <= 6
    return key in EXTRA_METRICS and type(value) is int and value in (0, 1)


def make_preconditions(snapshot: dict, *, now: float | None = None) -> dict:
    """Guard known cached configuration and power flags, never identities."""
    exported = export_settings(snapshot, now=now)
    metrics = snapshot.get("metrics", {})
    selected = {key: metrics[key] for key in sorted(PREFERENCE_METRICS | EXTRA_METRICS)
                if key in metrics and valid_metric(key, metrics[key], exported["model"])}
    result = {"model": exported["model"], "protocol": exported["protocol"], "metrics": selected}
    if exported["tou_plan_fresh"]:
        result["tou_plan_readback"] = exported["tou_plan_readback"]
    return result


def validate_preconditions(value: object) -> None:
    if (type(value) is not dict or not {"model", "protocol", "metrics"} <= value.keys()
            or set(value) - {"model", "protocol", "metrics", "tou_plan_readback"}
            or value["model"] not in ("c300", "c1000", "c1000_gen2", "c2000_gen2")
            or value["protocol"] not in ("legacy", "prime", "native_mqtt")
            or type(value["metrics"]) is not dict
            or any(key not in PREFERENCE_METRICS | EXTRA_METRICS
                   or not valid_metric(key, metric, value["model"]) for key, metric in value["metrics"].items())):
        raise ValueError("Invalid command preconditions")
    if "tou_plan_readback" in value and (value["model"] not in ("c1000_gen2", "c2000_gen2")
            or value["protocol"] != "native_mqtt" or validate_plan_readback(value["tou_plan_readback"]) is None):
        raise ValueError("Invalid command preconditions")


def check_preconditions(expected: dict | None, snapshot: dict) -> bool:
    """Cached comparison only; firmware guards still obtain raw fresh reports."""
    if expected is None:
        return True
    if not export_settings(snapshot)["snapshot_fresh"]:
        return False
    if any(expected[key] != snapshot.get(key) for key in ("model", "protocol")):
        return False
    actual = snapshot.get("metrics", {})
    if any(not valid_metric(key, actual.get(key), expected["model"]) or actual.get(key) != value
           for key, value in expected["metrics"].items()):
        return False
    if "tou_plan_readback" in expected:
        plan = validate_plan_readback(snapshot.get("tou_plan_readback"))
        before = expected["tou_plan_readback"]
        if (not plan_is_fresh(snapshot) or plan is None or plan["enabled"] != before["enabled"]
                or plan["periods"] != before["periods"]):
            return False
    return True


class CoordinationError(ValueError):
    def __init__(self, code: str, *, changed: bool = False):
        super().__init__(code)
        self.code, self.changed = code, changed


@dataclass
class _Request:
    fingerprint: bytes
    expires_at: float
    response: tuple[int, dict] | None = None


class CommandCoordinator:
    """In-process gateway coordination; direct SDK/worker callers are outside it.

    Completed entries are never evicted within their window to admit new writes.
    A full cache rejects new IDs. Expired tickets and previous gateway instances
    cannot execute. An unknown result is cached exactly like a successful result.
    """

    def __init__(self, *, clock=time.time, max_requests: int = MAX_REQUESTS):
        if type(max_requests) is not int or not 1 <= max_requests <= MAX_REQUESTS:
            raise ValueError("Invalid request-cache bound")
        self.instance = uuid.uuid4().hex
        self.clock, self.max_requests = clock, max_requests
        self.busy: set[str] = set()
        self.requests: dict[tuple[int, str, str], _Request] = {}

    def context(self, name: str) -> dict:
        return {"schema_version": 1, "gateway_instance": self.instance, "issued_at": self.clock(),
            "request_window_seconds": REQUEST_WINDOW_SECONDS, "busy": name in self.busy,
            "preconditions_supported": True}

    def _prune(self, now: float) -> None:
        for key, entry in list(self.requests.items()):
            if entry.response is not None and entry.expires_at < now:
                del self.requests[key]

    def begin(self, principal: object, name: str, payload: dict, ticket: dict | None):
        now = self.clock()
        self._prune(now)
        key = None
        if ticket is not None:
            if (type(ticket) is not dict or set(ticket) != {"request_id", "gateway_instance", "issued_at"}
                    or type(ticket["request_id"]) is not str or not REQUEST_ID.fullmatch(ticket["request_id"])
                    or type(ticket["gateway_instance"]) is not str or not number(ticket["issued_at"])):
                raise CoordinationError("InvalidRequestTicket")
            if ticket["gateway_instance"] != self.instance:
                raise CoordinationError("GatewayInstanceChanged")
            if not -5 <= now - ticket["issued_at"] <= REQUEST_WINDOW_SECONDS:
                raise CoordinationError("RequestTicketExpired")
            key = (id(principal), name, ticket["request_id"])
            fingerprint = hashlib.sha256(json.dumps({"payload": payload, "ticket": ticket},
                sort_keys=True, allow_nan=False, separators=(",", ":")).encode()).digest()
            if key in self.requests:
                entry = self.requests[key]
                if entry.fingerprint != fingerprint:
                    raise CoordinationError("RequestIdConflict")
                if entry.response is None:
                    raise CoordinationError("CommandInProgress", changed=True)
                return key, (entry.response[0], json.loads(json.dumps(entry.response[1])))
            if len(self.requests) >= self.max_requests:
                raise CoordinationError("RequestCacheFull")
        if name in self.busy:
            raise CoordinationError("DeviceBusy")
        self.busy.add(name)
        if key is not None:
            self.requests[key] = _Request(fingerprint, ticket["issued_at"] + REQUEST_WINDOW_SECONDS)
        return key, None

    def finish(self, name: str, key: tuple[int, str, str] | None, status: int, body: dict) -> None:
        self.busy.discard(name)
        if key is not None:
            self.requests[key].response = (status, json.loads(json.dumps(body)))

    def result(self, principal: object, name: str, request_id: str) -> dict | None:
        if type(request_id) is not str or not REQUEST_ID.fullmatch(request_id):
            return None
        self._prune(self.clock())
        entry = self.requests.get((id(principal), name, request_id))
        if entry is None:
            return None
        return {"schema_version": 1, "request_id": request_id,
            "state": "in_progress" if entry.response is None else "finished",
            "http_status": entry.response[0] if entry.response is not None else None,
            "response": json.loads(json.dumps(entry.response[1])) if entry.response is not None else None}
