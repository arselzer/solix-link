"""FastAPI telemetry gateway with optional Vue UI and allowlisted controls."""

from __future__ import annotations

import asyncio
import hmac
import json
import mimetypes
import os
import re
import sqlite3
import time
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
import uvicorn

from .commands import validate_command
from .charging_policy import ChargingPolicyRequestError, MAX_PREVIEW_BYTES, preview_charging_policy
from .adaptive_policy import preview_adaptive_policy
from .diagnostics import gateway_diagnostics
from .manager import MonitorService
from .tou import PowerFlowTimeout
from .plan_readback import validate_plan_readback
from .settings_export import export_settings
from .settings_compare import compare_settings
from .access import AccessPolicy, Principal, unique_object
from .commands import COMMAND_FIELDS
from .activity import ActivityStore, UpsTracker, command_readback

MAX_COMMAND_BYTES = 16384
WEB_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
}


def create_app(service: MonitorService, token: str | None = None, *, allow_control: bool = False,
               web_ui: bool = False, history_file: Path | None = None,
               history_retention_days: int = 7, permissions: AccessPolicy | None = None,
               activity_file: Path | None = None, activity_retention_days: int = 7) -> FastAPI:
    """Create the gateway; its lifespan owns the single monitoring service."""
    if permissions is not None and token is not None:
        raise ValueError("Use scoped permissions or a legacy token, not both")
    if allow_control and not token and permissions is None:
        raise ValueError("HTTP controls require SOLIX_HTTP_TOKEN or an explicit Bearer token")
    legacy_principal = Principal(frozenset(service.devices), tuple(
        (name, frozenset(COMMAND_FIELDS)) for name in service.devices))
    tracker = UpsTracker()
    activity_store = ActivityStore(retention_days=activity_retention_days)
    activity_failed = False
    web_root = Path(__file__).with_name("web")
    assets = {"/" + path.relative_to(web_root).as_posix(): path
              for path in (web_root / "assets").rglob("*") if path.is_file()} if web_ui else {}
    if web_ui:
        if not (web_root / "index.html").is_file():
            raise RuntimeError("Dashboard assets missing; run npm run build:dashboard")
        assets["/"] = web_root / "index.html"

    history_store = None
    history_failed = False

    async def append_activity(record):
        nonlocal activity_failed
        try:
            if activity_file is None:
                return activity_store.append(record)
            return await asyncio.to_thread(activity_store.append, record)
        except Exception:
            activity_failed = True
            return None

    async def record_activity():
        nonlocal activity_failed
        while True:
            try:
                for record in tracker.observe(service.snapshots()):
                    if await append_activity(record) is None:
                        return
            except Exception:
                activity_failed = True
                return
            await asyncio.sleep(2)

    async def record_history():
        nonlocal history_failed
        while True:
            try:
                cached = service.snapshots()
                await asyncio.to_thread(history_store.record, cached)
            except Exception:
                history_failed = True
                return
            await asyncio.sleep(2)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nonlocal history_store, history_failed, activity_store
        recorder = observer = None
        await service.start()
        try:
            if activity_file is not None:
                activity_store.close()
                activity_store = await asyncio.to_thread(ActivityStore, activity_file,
                    retention_days=activity_retention_days)
            observer = asyncio.create_task(record_activity())
            if history_file is not None:
                from .history import HistoryStore
                history_failed = False
                history_store = await asyncio.to_thread(
                    HistoryStore, history_file, retention_days=history_retention_days)
                recorder = asyncio.create_task(record_history())
            yield
        finally:
            if observer is not None:
                observer.cancel()
                await asyncio.gather(observer, return_exceptions=True)
            if recorder is not None:
                recorder.cancel()
                await asyncio.gather(recorder, return_exceptions=True)
            try:
                if history_store is not None:
                    await asyncio.to_thread(history_store.close)
                    history_store = None
            finally:
                activity_store.close()
                await service.stop()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def authorize(request: Request, call_next):
        # Only the bundled shell is public; it contains no station data or token.
        public_asset = request.method in ("GET", "HEAD") and request.url.path in assets
        header = request.headers.get("Authorization", "")
        principal = permissions.authenticate(header) if permissions is not None else legacy_principal
        if not public_asset and ((permissions is not None and principal is None) or (token and not hmac.compare_digest(
                header.encode(), f"Bearer {token}".encode()))):
            return JSONResponse({"error": "Unauthorized"}, status_code=401,
                                headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"})
        request.state.principal = principal
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    def readable(request, name):
        return name in service.devices and name in request.state.principal.read

    def status_with_controls(status: dict, principal: Principal) -> dict:
        commands = getattr(service, "supported_commands", None)
        config = service.devices.get(status["name"])
        private_fields = {"address", "serial_number", "account_id", "owner_id", "owner_user_id", "client_id", "raw_tlvs"}
        public = {key: value for key, value in status.items() if key not in private_fields}
        public["metrics"] = {key: value for key, value in status["metrics"].items() if key not in private_fields}
        public["tou_plan_readback"] = validate_plan_readback(status.get("tou_plan_readback")) if (
            status.get("model") in ("c1000_gen2", "c2000_gen2") and status.get("protocol") == "native_mqtt") else None
        if public.get("error"):
            error_class = str(public["error"]).split(":", 1)[0]
            public["error"] = error_class if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*Error", error_class) else "ConnectionError"
        controls = [command for command in commands(status["name"])
                    if command in principal.commands(status["name"])] if allow_control and commands else []
        return {**public, "timezone_name": getattr(config, "timezone_name", None),
                "ups_state": tracker.describe(status), "control_enabled": bool(controls), "controls": controls}

    def snapshots(request) -> list[dict]:
        return [status_with_controls(status, request.state.principal) for status in service.snapshots()
                if readable(request, status["name"])]

    @app.api_route("/health", methods=["GET", "HEAD"])
    async def health(request: Request):
        devices = snapshots(request)
        ok = any(device["available"] for device in devices)
        return JSONResponse({"ok": ok, "devices": len(devices), "available": sum(d["available"] for d in devices)},
                            status_code=200 if ok else 503)

    @app.api_route("/devices", methods=["GET", "HEAD"])
    async def all_devices(request: Request):
        return {"devices": snapshots(request)}

    @app.api_route("/diagnostics", methods=["GET", "HEAD"])
    async def diagnostics(request: Request):
        return gateway_diagnostics([status for status in service.snapshots() if readable(request, status["name"])])

    @app.api_route("/setup-check", methods=["GET", "HEAD"])
    async def setup_check(request: Request):
        if not set(service.devices) <= request.state.principal.read:
            return JSONResponse({"error": "InsufficientScope"}, status_code=403)
        check = getattr(service, "check_setup", None)
        if check is None:
            return JSONResponse({"error": "SetupCheckUnavailable"}, status_code=404)
        try:
            return JSONResponse(await check())
        except Exception:
            return JSONResponse({"error": "SetupCheckFailed"}, status_code=503)

    @app.api_route("/devices/{name}", methods=["GET", "HEAD"])
    async def one_device(name: str, request: Request):
        if not readable(request, name):
            return PlainTextResponse("Unknown device", status_code=404)
        return status_with_controls(service.snapshot(name), request.state.principal)

    @app.api_route("/devices/{name}/settings-export", methods=["GET", "HEAD"])
    async def settings_export(name: str, request: Request):
        if not readable(request, name):
            return PlainTextResponse("Unknown device", status_code=404)
        return export_settings(service.snapshot(name))

    @app.post("/devices/{name}/settings-compare")
    async def settings_compare(name: str, request: Request):
        if not readable(request, name):
            return JSONResponse({"error": "UnknownDevice"}, status_code=404)
        try:
            raw = bytearray()
            async for chunk in request.stream():
                if len(raw) + len(chunk) > 32768:
                    return JSONResponse({"error": "ComparisonTooLarge"}, status_code=413)
                raw.extend(chunk)
            body = json.loads(raw, object_pairs_hook=unique_object)
            if type(body) is not dict or set(body) != {"baseline"}:
                raise ValueError
            return compare_settings(body["baseline"], export_settings(service.snapshot(name)))
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return JSONResponse({"error": "InvalidSettingsComparison", "commands_sent": 0}, status_code=400)

    @app.api_route("/devices/{name}/activity", methods=["GET", "HEAD"])
    async def activity(name: str, request: Request):
        if not readable(request, name):
            return JSONResponse({"error": "UnknownDevice"}, status_code=404)
        if activity_failed:
            return JSONResponse({"error": "ActivityUnavailable"}, status_code=503)
        try:
            params = request.query_params
            if set(params) - {"limit", "after"} or len(params.multi_items()) != len(params):
                raise ValueError
            args = {key: int(value) for key, value in params.items()}
            if activity_file is None:
                return activity_store.query({name}, **args)
            return await asyncio.to_thread(activity_store.query, {name}, **args)
        except (ValueError, OverflowError):
            return JSONResponse({"error": "InvalidActivityQuery"}, status_code=400)
        except (OSError, sqlite3.Error):
            return JSONResponse({"error": "ActivityUnavailable"}, status_code=503)

    @app.api_route("/history", methods=["GET", "HEAD"])
    async def history_info(request: Request):
        if history_file is None:
            return {"enabled": False, "estimated": True, "read_only": True}
        if history_store is None or history_failed:
            return JSONResponse({"error": "HistoryUnavailable"}, status_code=503)
        if not set(service.devices) <= request.state.principal.read:
            return {"enabled": True, "estimated": True, "read_only": True, "scope_limited": True}
        try:
            return {"enabled": True, "read_only": True, **await asyncio.to_thread(history_store.stats)}
        except (OSError, ValueError, sqlite3.Error):
            return JSONResponse({"error": "HistoryUnavailable"}, status_code=503)

    @app.api_route("/devices/{name}/history", methods=["GET", "HEAD"])
    async def device_history(name: str, request: Request):
        if not readable(request, name):
            return JSONResponse({"error": "UnknownDevice"}, status_code=404)
        if history_file is None:
            return JSONResponse({"error": "HistoryDisabled"}, status_code=404)
        if history_store is None or history_failed:
            return JSONResponse({"error": "HistoryUnavailable"}, status_code=503)
        try:
            parameters = request.query_params
            if set(parameters) - {"since", "until", "limit"} or len(parameters.multi_items()) != len(parameters):
                raise ValueError
            arguments = {key: float(parameters[key]) for key in ("since", "until") if key in parameters}
            if "limit" in parameters:
                arguments["limit"] = int(parameters["limit"])
            return await asyncio.to_thread(history_store.query, name, **arguments)
        except KeyError:
            return JSONResponse({"error": "HistoryNotRecorded"}, status_code=404)
        except (ValueError, OverflowError):
            return JSONResponse({"error": "InvalidHistoryQuery"}, status_code=400)
        except (OSError, sqlite3.Error):
            return JSONResponse({"error": "HistoryUnavailable"}, status_code=503)

    @app.api_route("/history/summary", methods=["GET", "HEAD"])
    async def history_summary(request: Request):
        if history_file is None:
            return {"schema_version": 1, "enabled": False, "estimated": True, "read_only": True}
        if history_store is None or history_failed:
            return JSONResponse({"error": "HistoryUnavailable"}, status_code=503)
        try:
            summary = await asyncio.to_thread(history_store.summary)
            summary["stations"] = [item for item in summary["stations"] if readable(request, item["name"])]
            return {**summary, "enabled": True, "recording": True, "read_only": True}
        except (OSError, ValueError, sqlite3.Error):
            return JSONResponse({"error": "HistoryUnavailable"}, status_code=503)

    async def evaluate_preview(name: str, request: Request, preview: Callable[[object, object], dict]):
        if not readable(request, name):
            return JSONResponse({"error": "UnknownDevice", "commands_sent": 0}, status_code=404)
        try:
            raw = bytearray()
            async for chunk in request.stream():
                if len(raw) + len(chunk) > MAX_PREVIEW_BYTES:
                    return JSONResponse({"error": "PreviewTooLarge", "commands_sent": 0}, status_code=413)
                raw.extend(chunk)
            def unique_object(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError
                    result[key] = value
                return result
            body = json.loads(raw, object_pairs_hook=unique_object)
            return preview(service.snapshot(name), body)
        except (ChargingPolicyRequestError, ValueError, UnicodeError, RecursionError):
            return JSONResponse({"error": "InvalidChargingPreview", "commands_sent": 0,
                                 "settings_may_have_changed": False}, status_code=400)

    @app.post("/devices/{name}/charging-preview")
    async def charging_preview(name: str, request: Request):
        return await evaluate_preview(name, request, preview_charging_policy)

    @app.post("/devices/{name}/adaptive-preview")
    async def adaptive_preview(name: str, request: Request):
        return await evaluate_preview(name, request, preview_adaptive_policy)

    @app.post("/devices/{name}/commands")
    async def command(name: str, request: Request):
        if not allow_control:
            return JSONResponse({"error": "ControlsDisabled"}, status_code=403)
        if not readable(request, name):
            return PlainTextResponse("Unknown device", status_code=404)
        try:
            raw = bytearray()
            async for chunk in request.stream():
                if len(raw) + len(chunk) > MAX_COMMAND_BYTES:
                    return JSONResponse({"error": "CommandTooLarge", "settings_may_have_changed": False}, status_code=413)
                raw.extend(chunk)
            body = json.loads(raw, object_pairs_hook=unique_object)
            if not isinstance(body, dict) or "command" not in body:
                raise ValueError("Invalid command body")
            action = body.pop("command")
            validate_command(action, body)
        except (ValueError, UnicodeError, RecursionError):
            return JSONResponse({"error": "InvalidCommand", "settings_may_have_changed": False}, status_code=400)
        if action not in request.state.principal.commands(name):
            return JSONResponse({"error": "InsufficientScope", "settings_may_have_changed": False}, status_code=403)
        if action not in service.supported_commands(name):
            return JSONResponse({"error": "UnsupportedCommand", "settings_may_have_changed": False}, status_code=403)
        if activity_failed:
            return JSONResponse({"error": "ActivityUnavailable", "settings_may_have_changed": False}, status_code=503)
        command_id = uuid.uuid4().hex
        before = export_settings(service.snapshot(name))
        if await append_activity({"name": name, "timestamp": time.time(), "kind": "command_started",
                "details": {"command_id": command_id, "command": action, "parameters": body}}) is None:
            return JSONResponse({"error": "ActivityUnavailable", "settings_may_have_changed": False}, status_code=503)

        async def finish(outcome, status, error=None):
            details = {"command_id": command_id, "command": action, "outcome": outcome,
                "physical_behavior_verified": False, "readback": command_readback(action, body, status),
                "comparison": compare_settings(before, export_settings(status))}
            if error is not None:
                details["error"] = error
            return await append_activity({"name": name, "timestamp": time.time(),
                "kind": "command_finished", "details": details}) is not None
        try:
            result = await service.command(name, action, **body)
            recorded = await finish("completed", result)
            return JSONResponse({**status_with_controls(result, request.state.principal),
                "command_result": {"command_id": command_id, "audit_recorded": recorded,
                                   "physical_behavior_verified": False, "readback": command_readback(action, body, result)}})
        except asyncio.CancelledError:
            await finish("outcome_unknown", service.snapshot(name), "CancelledError")
            raise
        except PowerFlowTimeout as error:
            failed_status = error.snapshot if error.snapshot.get("name") == name else service.snapshot(name)
            recorded = await finish("outcome_unknown", failed_status, "PowerFlowTimeout")
            return JSONResponse({"error": "PowerFlowTimeout", "settings_may_have_changed": True,
                                 "audit_recorded": recorded,
                                 "device": status_with_controls(failed_status, request.state.principal)}, status_code=504)
        except (ValueError, PermissionError, ConnectionError, TimeoutError, RuntimeError) as error:
            code = 400 if isinstance(error, ValueError) else 403 if isinstance(error, PermissionError) else 504 if isinstance(error, TimeoutError) else 409
            recorded = await finish("rejected" if isinstance(error, PermissionError) else "outcome_unknown",
                service.snapshot(name), type(error).__name__)
            return JSONResponse({"error": type(error).__name__, "settings_may_have_changed": not isinstance(error, PermissionError),
                                 "audit_recorded": recorded,
                                 "device": status_with_controls(service.snapshot(name), request.state.principal)}, status_code=code)

    @app.get("/events")
    async def events(request: Request):
        async def stream():
            queue = service.subscribe()
            try:
                initial = json.dumps({"devices": snapshots(request)}, separators=(",", ":"))
                yield f"event: snapshot\ndata: {initial}\n\n"
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=20)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    if not readable(request, event["name"]):
                        continue
                    payload = json.dumps(status_with_controls(event, request.state.principal), separators=(",", ":"))
                    yield f"event: update\ndata: {payload}\n\n"
            finally:
                service.unsubscribe(queue)
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.api_route("/metrics", methods=["GET", "HEAD"])
    async def metrics(request: Request):
        lines = []
        for status in snapshots(request):
            name = status["name"].replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            label = f'{{device="{name}"}}'
            lines.append(f"solix_gen2_available{label} {int(status['available'])}")
            if status["last_seen_timestamp"] is not None:
                lines.append(f"solix_gen2_last_seen_timestamp_seconds{label} {status['last_seen_timestamp']}")
            for key, value in status["metrics"].items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    metric = re.sub(r"[^a-zA-Z0-9_]", "_", key)
                    lines.append(f"solix_gen2_{metric}{label} {value}")
        return PlainTextResponse("\n".join(lines) + "\n")

    if web_ui:
        # Exact built-file allowlist: no directory serving or arbitrary disk reads.
        async def dashboard(request: Request):
            path = assets.get(request.url.path)
            if path is None:
                return PlainTextResponse("Not found", status_code=404)
            return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0], headers=WEB_HEADERS)
        app.add_api_route("/", dashboard, methods=["GET", "HEAD"])
        app.add_api_route("/assets/{asset_path:path}", dashboard, methods=["GET", "HEAD"])
    return app


def run_server(service: MonitorService, host: str = "127.0.0.1", port: int = 8765, *, allow_control: bool = False,
               web_ui: bool = False, history_file: Path | None = None,
               history_retention_days: int = 7, permissions_file: Path | None = None,
               activity_file: Path | None = None, activity_retention_days: int = 7) -> None:
    permissions = AccessPolicy.load(permissions_file, service.devices) if permissions_file is not None else None
    token = None if permissions is not None else os.environ.get("SOLIX_HTTP_TOKEN")
    uvicorn.run(create_app(service, token, allow_control=allow_control, web_ui=web_ui,
                          history_file=history_file, history_retention_days=history_retention_days,
                          permissions=permissions, activity_file=activity_file,
                          activity_retention_days=activity_retention_days), host=host, port=port)
