"""FastAPI telemetry gateway with optional Vue UI and allowlisted controls."""

from __future__ import annotations

import asyncio
import hmac
import json
import mimetypes
import os
import re
import sqlite3
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
               history_retention_days: int = 7) -> FastAPI:
    """Create the gateway; its lifespan owns the single monitoring service."""
    if allow_control and not token:
        raise ValueError("HTTP controls require SOLIX_HTTP_TOKEN or an explicit Bearer token")
    web_root = Path(__file__).with_name("web")
    assets = {"/" + path.relative_to(web_root).as_posix(): path
              for path in (web_root / "assets").rglob("*") if path.is_file()} if web_ui else {}
    if web_ui:
        if not (web_root / "index.html").is_file():
            raise RuntimeError("Dashboard assets missing; run npm run build:dashboard")
        assets["/"] = web_root / "index.html"

    history_store = None
    history_failed = False

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
        nonlocal history_store, history_failed
        recorder = None
        await service.start()
        try:
            if history_file is not None:
                from .history import HistoryStore
                history_failed = False
                history_store = await asyncio.to_thread(
                    HistoryStore, history_file, retention_days=history_retention_days)
                recorder = asyncio.create_task(record_history())
            yield
        finally:
            if recorder is not None:
                recorder.cancel()
                await asyncio.gather(recorder, return_exceptions=True)
            try:
                if history_store is not None:
                    await asyncio.to_thread(history_store.close)
                    history_store = None
            finally:
                await service.stop()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def authorize(request: Request, call_next):
        # Only the bundled shell is public; it contains no station data or token.
        public_asset = request.method in ("GET", "HEAD") and request.url.path in assets
        if not public_asset and token and not hmac.compare_digest(
                request.headers.get("Authorization", "").encode(), f"Bearer {token}".encode()):
            return JSONResponse({"error": "Unauthorized"}, status_code=401,
                                headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    def status_with_controls(status: dict) -> dict:
        commands = getattr(service, "supported_commands", None)
        config = service.devices.get(status["name"])
        private_fields = {"address", "serial_number", "account_id", "owner_id", "owner_user_id", "client_id", "raw_tlvs"}
        public = {key: value for key, value in status.items() if key not in private_fields}
        public["metrics"] = {key: value for key, value in status["metrics"].items() if key not in private_fields}
        if public.get("error"):
            error_class = str(public["error"]).split(":", 1)[0]
            public["error"] = error_class if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*Error", error_class) else "ConnectionError"
        return {**public, "timezone_name": getattr(config, "timezone_name", None),
                "controls": commands(status["name"]) if allow_control and commands else []}

    def snapshots() -> list[dict]:
        return [status_with_controls(status) for status in service.snapshots()]

    @app.api_route("/health", methods=["GET", "HEAD"])
    async def health():
        devices = service.snapshots()
        ok = any(device["available"] for device in devices)
        return JSONResponse({"ok": ok, "devices": len(devices), "available": sum(d["available"] for d in devices)},
                            status_code=200 if ok else 503)

    @app.api_route("/devices", methods=["GET", "HEAD"])
    async def all_devices():
        return {"devices": snapshots()}

    @app.api_route("/diagnostics", methods=["GET", "HEAD"])
    async def diagnostics():
        return gateway_diagnostics(service.snapshots())

    @app.api_route("/setup-check", methods=["GET", "HEAD"])
    async def setup_check():
        check = getattr(service, "check_setup", None)
        if check is None:
            return JSONResponse({"error": "SetupCheckUnavailable"}, status_code=404)
        try:
            return JSONResponse(await check())
        except Exception:
            return JSONResponse({"error": "SetupCheckFailed"}, status_code=503)

    @app.api_route("/devices/{name}", methods=["GET", "HEAD"])
    async def one_device(name: str):
        if name not in service.devices:
            return PlainTextResponse("Unknown device", status_code=404)
        return status_with_controls(service.snapshot(name))

    @app.api_route("/history", methods=["GET", "HEAD"])
    async def history_info():
        if history_file is None:
            return {"enabled": False, "estimated": True, "read_only": True}
        if history_store is None or history_failed:
            return JSONResponse({"error": "HistoryUnavailable"}, status_code=503)
        try:
            return {"enabled": True, "read_only": True, **await asyncio.to_thread(history_store.stats)}
        except (OSError, ValueError, sqlite3.Error):
            return JSONResponse({"error": "HistoryUnavailable"}, status_code=503)

    @app.api_route("/devices/{name}/history", methods=["GET", "HEAD"])
    async def device_history(name: str, request: Request):
        if name not in service.devices:
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
    async def history_summary():
        if history_file is None:
            return {"schema_version": 1, "enabled": False, "estimated": True, "read_only": True}
        if history_store is None or history_failed:
            return JSONResponse({"error": "HistoryUnavailable"}, status_code=503)
        try:
            summary = await asyncio.to_thread(history_store.summary)
            summary["stations"] = [item for item in summary["stations"] if item["name"] in service.devices]
            return {**summary, "enabled": True, "recording": True, "read_only": True}
        except (OSError, ValueError, sqlite3.Error):
            return JSONResponse({"error": "HistoryUnavailable"}, status_code=503)

    async def evaluate_preview(name: str, request: Request, preview: Callable[[object, object], dict]):
        if name not in service.devices:
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
        if name not in service.devices:
            return PlainTextResponse("Unknown device", status_code=404)
        try:
            raw = bytearray()
            async for chunk in request.stream():
                if len(raw) + len(chunk) > MAX_COMMAND_BYTES:
                    return JSONResponse({"error": "CommandTooLarge", "settings_may_have_changed": False}, status_code=413)
                raw.extend(chunk)
            body = json.loads(raw)
            if not isinstance(body, dict) or "command" not in body:
                raise ValueError("Invalid command body")
            action = body.pop("command")
            validate_command(action, body)
        except (ValueError, UnicodeError):
            return JSONResponse({"error": "InvalidCommand", "settings_may_have_changed": False}, status_code=400)
        if action not in service.supported_commands(name):
            return JSONResponse({"error": "UnsupportedCommand", "settings_may_have_changed": False}, status_code=403)
        try:
            result = await service.command(name, action, **body)
            return JSONResponse(status_with_controls(result))
        except PowerFlowTimeout as error:
            failed_status = error.snapshot if error.snapshot.get("name") == name else service.snapshot(name)
            return JSONResponse({"error": "PowerFlowTimeout", "settings_may_have_changed": True,
                                 "device": status_with_controls(failed_status)}, status_code=504)
        except (ValueError, PermissionError, ConnectionError, TimeoutError, RuntimeError) as error:
            code = 400 if isinstance(error, ValueError) else 403 if isinstance(error, PermissionError) else 504 if isinstance(error, TimeoutError) else 409
            return JSONResponse({"error": type(error).__name__, "settings_may_have_changed": not isinstance(error, PermissionError),
                                 "device": status_with_controls(service.snapshot(name))}, status_code=code)

    @app.get("/events")
    async def events():
        async def stream():
            queue = service.subscribe()
            try:
                initial = json.dumps({"devices": snapshots()}, separators=(",", ":"))
                yield f"event: snapshot\ndata: {initial}\n\n"
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=20)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    payload = json.dumps(status_with_controls(event), separators=(",", ":"))
                    yield f"event: update\ndata: {payload}\n\n"
            finally:
                service.unsubscribe(queue)
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.api_route("/metrics", methods=["GET", "HEAD"])
    async def metrics():
        lines = []
        for status in snapshots():
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
               history_retention_days: int = 7) -> None:
    uvicorn.run(create_app(service, os.environ.get("SOLIX_HTTP_TOKEN"), allow_control=allow_control, web_ui=web_ui,
                          history_file=history_file, history_retention_days=history_retention_days), host=host, port=port)
