"""Bounded GET-only gateway access for terminal history and cached monitoring."""

from __future__ import annotations

import json
import re
import math
import os
from pathlib import Path
import stat
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from .plan_readback import validate_plan_readback


MAX_RESPONSE_BYTES = 1048576
MAX_TIMESTAMP = 253402300799
_MODELS = {"c300", "c1000", "c1000_gen2", "c2000_gen2"}
_PROTOCOLS = {"legacy", "prime", "native_mqtt"}
_TOTALS = ("ac_input_energy_kwh_estimate", "ac_output_energy_kwh_estimate",
           "ac_input_coverage_seconds", "ac_output_coverage_seconds", "gap_count")


class GatewayReadError(ValueError):
    """Fixed user-facing messages without URL, token or response-body contents."""


def _name(value: object) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 64 and value.isprintable() and bool(value.strip())


def _finite(value: object, minimum: float = 0, maximum: float = MAX_TIMESTAMP) -> bool:
    if type(value) not in (int, float) or not minimum <= value <= maximum:
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _validate_json(value: object) -> None:
    pending, visited = [(value, 0)], 0
    while pending:
        node, depth = pending.pop()
        visited += 1
        if depth > 24 or visited > 100000:
            raise ValueError
        if type(node) is float and not math.isfinite(node):
            raise ValueError
        if isinstance(node, dict):
            pending.extend((child, depth + 1) for child in node.values())
        elif isinstance(node, list):
            pending.extend((child, depth + 1) for child in node)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class GatewayClient:
    """Read only explicitly selected HTTP/HTTPS gateways, without redirects."""

    def __init__(self, url: str, token_file: Path | None = None, *, timeout: float = 5, opener=None) -> None:
        try:
            parts = urlsplit(url)
            if (not isinstance(url, str) or not url.isascii() or any(ord(c) < 33 for c in url)
                    or parts.scheme not in ("http", "https") or not parts.hostname
                    or parts.username is not None or parts.password is not None
                    or parts.query or parts.fragment or ".." in parts.path.split("/")
                    or parts.port == 0):
                raise ValueError
            self._base = urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))
        except (AttributeError, TypeError, ValueError):
            raise GatewayReadError("Use an explicit HTTP/HTTPS gateway URL without credentials or query parameters") from None
        if not _finite(timeout, 0.1, 30):
            raise GatewayReadError("Gateway timeout must be 0.1–30 seconds")
        self._timeout = timeout
        self._token = self._read_token(token_file) if token_file is not None else None
        # Explicit gateways bypass environment proxies, which could otherwise
        # forward a local gateway's Authorization header somewhere else.
        self._opener = opener or build_opener(ProxyHandler({}), _NoRedirect())

    @staticmethod
    def _read_token(path: Path) -> str:
        descriptor = None
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_nlink != 1 or not 1 <= info.st_size <= 4096):
                raise ValueError
            content = os.read(descriptor, 4097)
            if len(content) > 4096:
                raise ValueError
            token = content.decode("ascii").strip()
            if not token or any(not 33 <= ord(char) <= 126 for char in token):
                raise ValueError
            return token
        except (OSError, ValueError, UnicodeError):
            raise GatewayReadError("Gateway token file must be an owner-only regular file containing one bounded token") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _get(self, path: str, parameters: dict | None = None) -> dict:
        url = self._base + path + ("?" + urlencode(parameters) if parameters else "")
        headers = {"Accept": "application/json"}
        if self._token is not None:
            headers["Authorization"] = "Bearer " + self._token
        request = Request(url, headers=headers, method="GET")
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise GatewayReadError("Gateway response exceeds the terminal size limit")
            document = json.loads(raw, object_pairs_hook=_unique)
            _validate_json(document)
            if type(document) is not dict:
                raise ValueError
            return document
        except HTTPError as error:
            if error.code == 401:
                message = "Gateway authentication failed"
            elif 300 <= error.code < 400:
                message = "Gateway redirects are disabled"
            elif error.code == 404:
                message = "Gateway resource unavailable; check the exact station name and history configuration"
            else:
                message = "Gateway request failed"
            error.close()
            raise GatewayReadError(message) from None
        except (URLError, OSError):
            raise GatewayReadError("Gateway connection unavailable") from None
        except (ValueError, UnicodeError, RecursionError) as error:
            if isinstance(error, GatewayReadError):
                raise
            raise GatewayReadError("Gateway returned an invalid bounded JSON response") from None

    @staticmethod
    def _station(document: object, name: str | None = None) -> dict:
        if (type(document) is not dict or not _name(document.get("name"))
                or type(document.get("model")) is not str or document["model"] not in _MODELS
                or type(document.get("protocol")) is not str or document["protocol"] not in _PROTOCOLS
                or name is not None and document["name"] != name
                or type(document.get("metrics")) is not dict
                or type(document.get("connected")) is not bool or type(document.get("available")) is not bool
                or document.get("last_seen_timestamp") is not None and not _finite(document["last_seen_timestamp"])):
            raise GatewayReadError("Gateway returned an invalid station snapshot")
        # Remaining metrics are filtered by the terminal's existing public
        # measurement allowlist before presentation or policy evaluation.
        from .energy_values import validate_native_energy
        from .wifi_signal import validate_wifi_signal
        from .original_counters import validate_original_counters
        from .commands import COMMAND_FIELDS
        controls = document.get("controls", [])
        preview_controls = [c for c in controls if type(c) is str and c in COMMAND_FIELDS] if type(controls) is list else []
        return {"name": document["name"], "model": document["model"], "protocol": document["protocol"],
                "connected": document["connected"], "available": document["available"],
                "last_seen_timestamp": document.get("last_seen_timestamp"), "metrics": document["metrics"],
                "native_energy": validate_native_energy(document.get("native_energy"), model=document["model"]) if document["protocol"] == "native_mqtt" else None,
                "original_counters": validate_original_counters(document.get("original_counters"), model=document["model"], protocol=document["protocol"]),
                "wifi_signal": validate_wifi_signal(document.get("wifi_signal"), model=document["model"], protocol=document["protocol"]),
                "tou_plan_readback": validate_plan_readback(document.get("tou_plan_readback")) if (
                    document["model"] in ("c1000_gen2", "c2000_gen2") and document["protocol"] == "native_mqtt") else None,
                "power_flow": document.get("power_flow") if document.get("power_flow") in (
                    "unknown", "grid", "battery", "transitioning") else "unknown", "control_enabled": False,
                "preview_controls": preview_controls,
                "error": "ConnectionError" if document.get("error") is not None else None}

    def devices(self) -> list[dict]:
        devices = self._get("/devices").get("devices")
        if type(devices) is not list or len(devices) > 32:
            raise GatewayReadError("Gateway returned an invalid station list")
        result = [self._station(item) for item in devices]
        if len({item["name"] for item in result}) != len(result):
            raise GatewayReadError("Gateway returned duplicate station names")
        return result

    def snapshot(self, name: str) -> dict:
        if not _name(name):
            raise GatewayReadError("Use an exact bounded public station name")
        return self._station(self._get("/devices/" + quote(name, safe="")), name)

    def energy(self, name: str) -> dict | None:
        """Return cached native counters, never query a station or cloud endpoint."""
        return self.snapshot(name)["native_energy"]

    def control_availability(self, name: str) -> dict:
        if not _name(name):
            raise GatewayReadError("Use an exact bounded public station name")
        from .control_availability import parse_control_availability
        try:
            return parse_control_availability(self._get("/devices/" + quote(name, safe="") + "/control-availability"))
        except ValueError as error:
            if isinstance(error, GatewayReadError):
                raise
            raise GatewayReadError("Gateway returned invalid cached control explanations") from None

    def history(self, name: str, *, since: float | None = None, until: float | None = None,
                limit: int = 200) -> dict:
        if (not _name(name) or type(limit) is not int or not 1 <= limit <= 2000
                or since is not None and not _finite(since)
                or until is not None and not _finite(until)
                or since is not None and until is not None and (since > until or until - since > 31 * 86400)):
            raise GatewayReadError("History name, time bounds or point limit are invalid")
        parameters = {"limit": limit}
        parameters.update({key: value for key, value in (("since", since), ("until", until)) if value is not None})
        result = self._get("/devices/" + quote(name, safe="") + "/history", parameters)
        try:
            if (result.get("name") != name or result.get("estimated") is not True
                    or type(result.get("model")) is not str or result["model"] not in _MODELS
                    or type(result.get("protocol")) is not str or result["protocol"] not in _PROTOCOLS
                    or type(result.get("points")) is not list or len(result["points"]) > limit
                    or type(result.get("truncated")) is not bool):
                raise ValueError
            points, previous = [], None
            for item in result["points"]:
                timestamp = item["timestamp"]
                if (not _finite(timestamp) or previous is not None and timestamp < previous
                        or type(item["gap"]) is not bool):
                    raise ValueError
                point = {"timestamp": timestamp, "gap": item["gap"]}
                for key, maximum in (("battery_percentage", 100), ("ac_input_power_w", 10000),
                                     ("ac_output_power_w", 10000), ("max_source_interval_seconds", 60)):
                    value = item.get(key)
                    if value is not None and not _finite(value, 0, maximum):
                        raise ValueError
                    point[key] = value
                points.append(point)
                previous = timestamp
            totals = {}
            for field in ("totals", "lifetime_totals"):
                source = result[field]
                if type(source) is not dict or any(not _finite(source.get(key), 0, 1e18) for key in _TOTALS):
                    raise ValueError
                if type(source["gap_count"]) is not int:
                    raise ValueError
                totals[field] = {key: source[key] for key in _TOTALS}
            window = result["window"]
            if (type(window) is not dict or not _finite(window.get("since")) or not _finite(window.get("until"))
                    or not 0 <= window["until"] - window["since"] <= 31 * 86400):
                raise ValueError
            start = result.get("collection_start")
            if start is not None and not _finite(start):
                raise ValueError
            limits = result["limits"]
            bounds = {"retention_days": (1, 365), "max_gap_seconds": (0.001, 60),
                      "max_power_w": (1, 10000), "max_samples": (1, 2000000),
                      "max_points": (1, 2000), "max_query_seconds": (1, 31 * 86400)}
            if type(limits) is not dict or any(not _finite(limits.get(key), *domain) for key, domain in bounds.items()):
                raise ValueError
            if (until is not None and window["until"] != until or since is not None and window["since"] < since
                    or any(not window["since"] <= point["timestamp"] <= window["until"] for point in points)
                    or any(point["max_source_interval_seconds"] is not None
                           and point["max_source_interval_seconds"] > limits["max_gap_seconds"] for point in points)):
                raise ValueError
            return {"name": name, "model": result["model"], "protocol": result["protocol"],
                    "estimated": True, "collection_start": start, "points": points, **totals,
                    **({"generation": result["generation"]} if type(result.get("generation")) is str
                        and re.fullmatch(r"[0-9a-f]{32}", result["generation"]) else {}),
                    "window": {key: window[key] for key in ("since", "until")},
                    "truncated": result["truncated"], "limits": {key: limits[key] for key in bounds}}
        except (KeyError, TypeError, ValueError):
            raise GatewayReadError("Gateway returned invalid history values") from None
