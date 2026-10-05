"""Optional dashboard, Bearer boundaries, bounded bodies and stream cleanup."""
import asyncio
from types import SimpleNamespace

from http_helpers import api_client
from test_gateway import Gateway
from solix_link import server
from solix_link.access import Principal
from starlette.requests import Request


def stream_request(gateway):
    # Direct generator tests supply the request principal normally established
    # by the HTTP authentication middleware (covered separately).
    request = Request({"type": "http", "method": "GET", "path": "/events", "headers": []})
    request.state.principal = Principal(frozenset(gateway.devices), tuple(
        (name, frozenset(gateway.supported_commands(name))) for name in gateway.devices))
    return request


def test_optional_shell_is_public_but_all_station_routes_stay_protected(tmp_path, monkeypatch):
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text('<html><script src="./assets/app.js"></script></html>')
    (web / "assets" / "app.js").write_text('console.log("synthetic dashboard")')
    monkeypatch.setattr(server, "__file__", str(tmp_path / "server.py"))
    gateway = Gateway()
    gateway.devices = {"ups": SimpleNamespace(timezone_name="Europe/Vienna")}
    started = []
    async def start(): started.append("start")
    async def stop(): started.append("stop")
    gateway.start, gateway.stop = start, stop

    async def run():
        async with api_client(server.create_app(gateway, token="SECRET-TOKEN", web_ui=True)) as client:
            for path in ("/", "/assets/app.js"):
                response = await client.get(path)
                assert response.status_code == 200
                assert "SECRET-TOKEN" not in response.text and "battery_percentage" not in response.text
                assert response.headers["cache-control"] == "no-store"
                assert "script-src 'self'" in response.headers["content-security-policy"]
                assert (await client.head(path)).status_code == 200
            for path in ("/health", "/devices", "/devices/ups", "/events", "/metrics", "/assets/missing.js"):
                assert (await client.get(path)).status_code == 401
            assert (await client.post("/", json={})).status_code == 401
            headers = {"Authorization": "Bearer SECRET-TOKEN"}
            value = (await client.get("/devices/ups", headers=headers)).json()
            assert value["timezone_name"] == "Europe/Vienna" and value["controls"] == []
            for path in ("/assets/missing.js", "/assets/%2E%2E/server.py", "/docs", "/openapi.json"):
                assert (await client.get(path, headers=headers)).status_code == 404
        assert started == ["start", "stop"]
        async with api_client(server.create_app(gateway)) as client:
            assert (await client.get("/")).status_code == 404
            assert (await client.get("/assets/app.js")).status_code == 404
    asyncio.run(run())


def test_command_size_limit_covers_chunked_bodies_and_never_calls_device():
    gateway = Gateway()
    async def chunks():
        yield b'{"command":"set-charge-power","watts":300,'
        yield b' ' * server.MAX_COMMAND_BYTES
        yield b'"ignored":0}'
    async def run():
        async with api_client(server.create_app(gateway, token="test", allow_control=True)) as client:
            headers = {"Authorization": "Bearer test", "Content-Type": "application/json"}
            response = await client.post("/devices/ups/commands", content=chunks(), headers=headers)
            assert response.status_code == 413 and not response.json()["settings_may_have_changed"]
            for raw in (b"null", b"[]", b"\xff", b'{"command":null}', b"{not-json}"):
                assert (await client.post("/devices/ups/commands", content=raw, headers=headers)).status_code == 400
        assert not gateway.calls
    asyncio.run(run())


def test_sse_initial_update_and_disconnect_release_subscription():
    gateway = Gateway()
    queue = asyncio.Queue()
    subscriptions = []
    gateway.subscribe = lambda: subscriptions.append(queue) or queue
    gateway.unsubscribe = lambda value: subscriptions.remove(value)
    async def run():
        app = server.create_app(gateway)
        endpoint = next(route.endpoint for route in app.routes if route.path == "/events")
        response = await endpoint(stream_request(gateway))
        stream = response.body_iterator
        assert "event: snapshot" in await anext(stream)
        queue.put_nowait(gateway.snapshot("ups"))
        assert "event: update" in await anext(stream)
        assert subscriptions == [queue]
        await stream.aclose()
        assert not subscriptions
    asyncio.run(run())


def test_private_ble_fields_and_error_details_are_filtered_from_every_public_route():
    gateway = Gateway()
    original_snapshot = gateway.snapshot
    private_fields = ("address", "serial_number", "owner_id", "owner_user_id", "client_id", "account_id", "raw_tlvs")

    def snapshot(name):
        value = original_snapshot(name)
        value.update({field: "PRIVATE-SYNTHETIC" for field in private_fields})
        value["metrics"].update({field: 123456789 for field in private_fields})
        value["error"] = "RuntimeError: PRIVATE-SYNTHETIC"
        return value

    gateway.snapshot = snapshot
    queue = asyncio.Queue()
    gateway.subscribe = lambda: queue
    gateway.unsubscribe = lambda value: None

    def check(value):
        assert all(field not in value and field not in value["metrics"] for field in private_fields)
        assert value["error"] == "RuntimeError"
        assert value["metrics"]["battery_percentage"] == 90

    async def run():
        app = server.create_app(gateway, token="test", allow_control=True)
        async with api_client(app) as client:
            headers = {"Authorization": "Bearer test"}
            check((await client.get("/devices", headers=headers)).json()["devices"][0])
            check((await client.get("/devices/ups", headers=headers)).json())
            check((await client.post("/devices/ups/commands", headers=headers,
                                    json={"command": "set-charge-power", "watts": 300})).json())
            gateway.failure = RuntimeError("PRIVATE-SYNTHETIC")
            check((await client.post("/devices/ups/commands", headers=headers,
                                    json={"command": "set-charge-power", "watts": 300})).json()["device"])
            text = (await client.get("/metrics", headers=headers)).text
            assert "battery_percentage" in text
            assert "123456789" not in text and "PRIVATE-SYNTHETIC" not in text
            assert all(field not in text for field in private_fields)
        endpoint = next(route.endpoint for route in app.routes if route.path == "/events")
        stream = (await endpoint(stream_request(gateway))).body_iterator
        import json
        try:
            initial = await anext(stream)
            check(json.loads(initial.split("data: ", 1)[1])["devices"][0])
            queue.put_nowait(snapshot("ups"))
            check(json.loads((await anext(stream)).split("data: ", 1)[1]))
        finally:
            await stream.aclose()
        # Public filtering must not destroy the private service baseline.
        assert gateway.snapshot("ups")["address"] == "PRIVATE-SYNTHETIC"
        assert gateway.snapshot("ups")["metrics"]["serial_number"] == 123456789

    asyncio.run(run())


def test_real_http_sse_disconnect_and_server_shutdown_stop_monitor():
    import socket
    import httpx
    import uvicorn
    gateway = Gateway()
    queues = []
    stopped = []
    def subscribe():
        queue = asyncio.Queue()
        queues.append(queue)
        return queue
    async def stop(): stopped.append(True)
    gateway.subscribe = subscribe
    gateway.unsubscribe = queues.remove
    gateway.stop = stop

    async def run():
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        app = server.create_app(gateway, token="test")
        runner = uvicorn.Server(uvicorn.Config(app, log_level="critical", lifespan="on"))
        task = asyncio.create_task(runner.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(5):
                while not runner.started:
                    if task.done(): await task
                    await asyncio.sleep(.01)
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=3) as client:
                async with client.stream("GET", "/events", headers={"Authorization": "Bearer test"}) as response:
                    assert response.status_code == 200
                    lines = response.aiter_lines()
                    assert await anext(lines) == "event: snapshot"
                    assert '"name":"ups"' in await anext(lines)
                    assert await anext(lines) == ""
                    queues[0].put_nowait(gateway.snapshot("ups"))
                    assert await anext(lines) == "event: update"
                async with asyncio.timeout(3):
                    while queues: await asyncio.sleep(.01)
        finally:
            runner.should_exit = True
            await asyncio.wait_for(task, timeout=5)
            sock.close()
        assert stopped == [True]
    asyncio.run(run())
