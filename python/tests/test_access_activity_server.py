"""Scope isolation and command audit through ASGI and a synthetic executor."""

import asyncio
from copy import deepcopy
import json
import time

import pytest

from http_helpers import api_client
from solix_link.access import AccessPolicy
from solix_link.activity import ActivityStore
from solix_link.server import create_app
from solix_link.settings_export import export_settings
from starlette.requests import Request
from test_gateway import Gateway


class Stations(Gateway):
    devices = {"test": object(), "protected": object()}

    def __init__(self):
        super().__init__()
        self.watts = 300

    def snapshot(self, name):
        return {**super().snapshot(name), "metrics": {"battery_percentage": 50,
            "ac_input_connected": 1, "ac_charging_power_limit_w": self.watts}}

    def snapshots(self):
        return [self.snapshot(name) for name in self.devices]

    async def command(self, name, command, **values):
        result = await super().command(name, command, **values)
        if command == "set-charge-power":
            self.watts = values["watts"]
        return self.snapshot(name)

    async def check_setup(self):
        pytest.fail("Partially scoped token must not execute setup check")

    def subscribe(self):
        self.queue = asyncio.Queue()
        return self.queue

    def unsubscribe(self, queue):
        assert queue is self.queue
        self.queue = None


def policy(tmp_path):
    tmp_path.chmod(0o700)
    tokens = ["monitor-synthetic-token", "control-synthetic-token", "partial-synthetic-token"]
    entries = []
    for index, token in enumerate(tokens):
        path = tmp_path / f"token-{index}"
        path.write_text(token)
        path.chmod(0o600)
        entries.append({"token_file": str(path), "read": ["test", "protected"] if index < 2 else ["test"],
                        "write": {"test": ["set-charge-power"]} if index == 1 else {}})
    path = tmp_path / "permissions.json"
    path.write_text(json.dumps({"schema_version": 1, "tokens": entries}))
    path.chmod(0o600)
    return AccessPolicy.load(path, Stations.devices), path, tokens


def test_tokens_do_not_leak_or_allow_invalid_duplicate_expanded_scopes(tmp_path):
    access, path, tokens = policy(tmp_path)
    assert all(token not in repr(access) for token in tokens)
    assert access.authenticate("Bearer " + tokens[0]).commands("protected") == frozenset()
    assert access.authenticate("Bearer wrong") is None
    body = json.loads(path.read_text())
    for mutate in (lambda b: b["tokens"][0]["read"].append("unknown"),
                   lambda b: b["tokens"][0]["read"].append("test"),
                   lambda b: b["tokens"][0]["write"].update(protected=["set-ac-output"]),
                   lambda b: b["tokens"][0].update(extra="PRIVATE"),
                   lambda b: b["tokens"][0].update(token_file=b["tokens"][1]["token_file"])):
        changed = deepcopy(body)
        mutate(changed)
        path.write_text(json.dumps(changed))
        with pytest.raises(ValueError, match="Invalid owner-only") as error:
            AccessPolicy.load(path, Stations.devices)
        assert "PRIVATE" not in str(error.value)
    path.chmod(0o644)
    with pytest.raises(ValueError):
        AccessPolicy.load(path, Stations.devices)


def test_scoped_reads_writes_metadata_and_command_history(tmp_path):
    access, _, tokens = policy(tmp_path)
    gateway = Stations()
    with pytest.raises(ValueError):
        create_app(gateway, token="legacy", permissions=access)
    async def run():
        async with api_client(create_app(gateway, permissions=access, allow_control=True)) as client:
            monitor, control, partial = [{"Authorization": "Bearer " + token} for token in tokens]
            assert (await client.get("/devices")).status_code == 401
            assert all(not item["controls"] for item in (await client.get("/devices", headers=monitor)).json()["devices"])
            result = (await client.get("/devices", headers=control)).json()["devices"]
            assert result[0]["controls"] == ["set-charge-power"] and result[1]["controls"] == []
            for target in ("/devices/protected", "/devices/protected/settings-export", "/devices/protected/activity", "/devices/protected/history"):
                assert (await client.get(target, headers=partial)).status_code == 404
                assert (await client.head(target, headers=partial)).status_code == 404
            for target in ("/devices/protected/charging-preview", "/devices/protected/adaptive-preview", "/devices/protected/settings-compare"):
                assert (await client.post(target, json={}, headers=partial)).status_code == 404
            assert (await client.get("/setup-check", headers=partial)).status_code == 403
            for target in ("/devices", "/diagnostics", "/metrics"):
                response = await client.get(target, headers=partial)
                assert response.status_code == 200 and "protected" not in response.text
            health = (await client.get("/health", headers=partial)).json()
            assert health["devices"] == 1 and health["available"] == 1
            body = {"command": "set-charge-power", "watts": 500}
            for name, headers in (("test", monitor), ("protected", control), ("test", partial)):
                response = await client.post(f"/devices/{name}/commands", json=body, headers=headers)
                assert response.status_code == 403 and not response.json()["settings_may_have_changed"]
            assert gateway.calls == []
            before = export_settings(gateway.snapshot("test"))
            response = await client.post("/devices/test/commands", json=body, headers=control)
            assert response.status_code == 200 and response.json()["command_result"]["audit_recorded"]
            assert not response.json()["command_result"]["physical_behavior_verified"]
            assert response.json()["command_result"]["readback"]["reported_values_match"] is True
            comparison = (await client.post("/devices/test/settings-compare", json={"baseline": before}, headers=monitor)).json()
            assert comparison["changes"] == [{"field":"ac_charging_power_limit_w", "before":300,"after":500}]
            assert len(gateway.calls) == 1
            activity = (await client.get("/devices/test/activity", headers=monitor)).json()
            commands = [row for row in activity["records"] if row["kind"].startswith("command_")]
            assert [row["kind"] for row in commands] == ["command_started", "command_finished"]
            assert commands[1]["details"]["outcome"] == "completed"
            assert not commands[1]["details"]["physical_behavior_verified"]
            assert not commands[1]["details"]["readback"]["independent_confirmation"]
            gateway.failure = TimeoutError("PRIVATE-ERROR-TEXT")
            failure = await client.post("/devices/test/commands", json=body, headers=control)
            assert failure.status_code == 504 and failure.json()["settings_may_have_changed"]
            activity = (await client.get("/devices/test/activity", headers=monitor)).json()
            assert "PRIVATE" not in json.dumps(activity) and not any(token in json.dumps(activity) for token in tokens)
            assert activity["records"][-1]["details"]["outcome"] == "outcome_unknown"
            for query in ("limit=201", "limit=1&limit=2", "after=-1", "private=PRIVATE"):
                assert (await client.get("/devices/test/activity?"+query, headers=monitor)).status_code == 400
            assert (await client.post("/devices/test/commands", headers=control,
                content=b'{"command":"set-charge-power","watts":300,"watts":400}')).status_code == 400
            assert len(gateway.calls) == 2
    asyncio.run(run())


def test_audit_failure_before_write_prevents_command(tmp_path, monkeypatch):
    access, _, tokens = policy(tmp_path)
    gateway = Stations()
    def fail(*args, **kwargs):
        raise OSError("PRIVATE")
    monkeypatch.setattr(ActivityStore, "append", fail)
    async def run():
        async with api_client(create_app(gateway, permissions=access, allow_control=True)) as client:
            response = await client.post("/devices/test/commands", headers={"Authorization": "Bearer "+tokens[1]},
                json={"command":"set-charge-power","watts":500})
            assert response.status_code == 503 and not response.json()["settings_may_have_changed"]
            assert gateway.calls == []
    asyncio.run(run())


def test_post_command_audit_failure_is_not_reported_as_an_unexecuted_command(tmp_path, monkeypatch):
    access, _, tokens = policy(tmp_path)
    gateway = Stations()
    original, count = ActivityStore.append, 0
    def append(self, record):
        nonlocal count
        count += 1
        if count > 1:
            raise OSError("PRIVATE")
        return original(self, record)
    monkeypatch.setattr(ActivityStore, "append", append)
    async def run():
        async with api_client(create_app(gateway, permissions=access, allow_control=True)) as client:
            headers = {"Authorization":"Bearer "+tokens[1]}
            response = await client.post("/devices/test/commands", headers=headers,
                json={"command":"set-charge-power","watts":500})
            assert response.status_code == 200 and not response.json()["command_result"]["audit_recorded"]
            assert len(gateway.calls) == 1
            assert (await client.get("/devices/test/activity", headers=headers)).status_code == 503
            assert (await client.post("/devices/test/commands", headers=headers,
                json={"command":"set-charge-power","watts":600})).status_code == 503
            assert len(gateway.calls) == 1
    asyncio.run(run())


def test_concurrent_principals_and_sse_never_share_device_scope(tmp_path):
    access, _, tokens = policy(tmp_path)
    gateway = Stations()
    app = create_app(gateway, permissions=access, allow_control=True)
    async def run():
        async with api_client(app) as client:
            responses = await asyncio.gather(*[client.get("/devices", headers={"Authorization":"Bearer "+tokens[index%3]})
                                               for index in range(18)])
            for index, response in enumerate(responses):
                data = response.json()["devices"]
                assert len(data) == (1 if index%3 == 2 else 2)
                assert bool(data[0]["controls"]) == (index%3 == 1)
            request = Request({"type":"http", "method":"GET", "path":"/events", "headers":[]})
            request.state.principal = access.authenticate("Bearer "+tokens[2])
            route = next(route for route in app.routes if route.path == "/events")
            response = await route.endpoint(request)
            stream = response.body_iterator
            first = await anext(stream)
            assert "protected" not in first and '"test"' in first
            await gateway.queue.put(gateway.snapshot("protected"))
            await gateway.queue.put(gateway.snapshot("test"))
            second = await anext(stream)
            assert "protected" not in second and '"test"' in second
            await stream.aclose()
            assert gateway.queue is None
    asyncio.run(run())


def test_scoped_history_aggregates_and_summary_hide_other_devices(tmp_path):
    access, _, tokens = policy(tmp_path)
    gateway = Stations()
    async def run():
        async with api_client(create_app(gateway, permissions=access, history_file=tmp_path/"history.sqlite")) as client:
            await asyncio.sleep(.05)
            headers = {"Authorization":"Bearer "+tokens[2]}
            info = (await client.get("/history", headers=headers)).json()
            assert info["scope_limited"] and "samples" not in info and "stations" not in info
            summary = (await client.get("/history/summary", headers=headers)).json()
            assert [item["name"] for item in summary["stations"]] == ["test"]
            assert "protected" not in json.dumps(summary)
            assert not gateway.calls
    asyncio.run(run())


def test_scoped_startup_replaces_environment_token_and_cli_exposes_options(tmp_path, monkeypatch):
    from solix_link import server
    from solix_link.cli import parser
    _, path, tokens = policy(tmp_path)
    gateway = Stations()
    saved = []
    monkeypatch.setenv("SOLIX_HTTP_TOKEN", "synthetic-legacy-bypass-forbidden")
    monkeypatch.setattr(server.uvicorn, "run", lambda app, **kwargs: saved.append(app))
    server.run_server(gateway, permissions_file=path, allow_control=True)
    async def run():
        async with api_client(saved[0]) as client:
            assert (await client.get("/devices", headers={"Authorization":"Bearer synthetic-legacy-bypass-forbidden"})).status_code == 401
            assert (await client.get("/devices", headers={"Authorization":"Bearer "+tokens[0]})).status_code == 200
    asyncio.run(run())
    for command, extra in (("serve", []), ("ap-service-serve", ["--directory", "/synthetic"])):
        args = parser().parse_args([command, *extra, "--permissions-file", str(path), "--activity-file", "/synthetic/activity.sqlite"])
        assert args.permissions_file == path and args.activity_retention_days == 7
