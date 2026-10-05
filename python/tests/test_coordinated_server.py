"""HTTP race and retry tests against synthetic station executors only."""

import asyncio
from copy import deepcopy
import json

import pytest

from http_helpers import api_client
from solix_link.server import create_app
from test_access_activity_server import Stations, policy

HEADERS = {"Authorization": "Bearer synthetic-token"}
PATH = "/devices/test/commands"


async def envelope(client, *, request_id="synthetic-request-01", name="test", headers=HEADERS):
    device = (await client.get(f"/devices/{name}", headers=headers)).json()
    context = device["command_context"]
    return {"command": "set-charge-power", "watts": 500, "expected": context["expected"],
        "coordination": {"request_id": request_id, "gateway_instance": context["gateway_instance"],
                         "issued_at": context["issued_at"]}}


class BlockingStations(Stations):
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def command(self, name, command, **values):
        self.entered.set()
        await self.release.wait()
        return await super().command(name, command, **values)


def test_same_station_conflicts_and_exact_success_replay_do_not_execute_twice():
    async def run():
        gateway = BlockingStations()
        async with api_client(create_app(gateway, token="synthetic-token", allow_control=True)) as client:
            body = await envelope(client)
            pending = asyncio.create_task(client.post(PATH, headers=HEADERS, json=body))
            await asyncio.wait_for(gateway.entered.wait(), 2)
            busy = (await client.get("/devices/test", headers=HEADERS)).json()
            assert busy["command_context"]["busy"]
            assert all("command_in_progress" in row["reasons"] for row in busy["control_availability"]["commands"])
            result = (await client.get("/devices/test/command-results/synthetic-request-01", headers=HEADERS)).json()
            assert result["state"] == "in_progress"
            duplicate = await client.post(PATH, headers=HEADERS, json=body)
            assert duplicate.status_code == 409 and duplicate.json()["error"] == "CommandInProgress"
            assert duplicate.json()["settings_may_have_changed"]
            conflict = await client.post(PATH, headers=HEADERS, json=body | {"watts": 600})
            assert conflict.status_code == 409 and conflict.json()["error"] == "RequestIdConflict"
            other = deepcopy(body)
            other["coordination"]["request_id"] = "synthetic-request-02"
            assert (await client.post(PATH, headers=HEADERS, json=other)).json()["error"] == "DeviceBusy"
            # Older callers are also serialized, without claiming deduplication.
            assert (await client.post(PATH, headers=HEADERS, json={"command":"set-charge-power", "watts":600})).json()["error"] == "DeviceBusy"
            gateway.release.set()
            first = await pending
            assert first.status_code == 200 and not first.json()["command_context"]["busy"]
            assert not any("command_in_progress" in row["reasons"] for row in first.json()["control_availability"]["commands"])
            repeated = await client.post(PATH, headers=HEADERS, json=body)
            assert repeated.status_code == 200 and repeated.json()["coordination"]["replayed"]
            assert len(gateway.calls) == 1
            activity = (await client.get("/devices/test/activity", headers=HEADERS)).json()["records"]
            assert [row["kind"] for row in activity if row["kind"].startswith("command_")] == ["command_started", "command_finished"]
            result = (await client.get("/devices/test/command-results/synthetic-request-01", headers=HEADERS)).json()
            assert result["http_status"] == 200 and result["state"] == "finished"
            assert result["response"]["coordination"]["replayed"] is False
    asyncio.run(run())


def test_different_stations_can_execute_independently():
    async def run():
        gateway = BlockingStations()
        second_entered = asyncio.Event()
        original = gateway.command
        async def command(name, action, **values):
            if name == "protected":
                second_entered.set()
            return await original(name, action, **values)
        gateway.command = command
        async with api_client(create_app(gateway, token="synthetic-token", allow_control=True)) as client:
            first_body = await envelope(client)
            second_body = await envelope(client, name="protected")
            first = asyncio.create_task(client.post(PATH, headers=HEADERS, json=first_body))
            await asyncio.wait_for(gateway.entered.wait(), 2)
            second = asyncio.create_task(client.post("/devices/protected/commands", headers=HEADERS, json=second_body))
            await asyncio.wait_for(second_entered.wait(), 2)
            gateway.release.set()
            assert [response.status_code for response in await asyncio.gather(first, second)] == [200, 200]
            assert {call[0] for call in gateway.calls} == {"test", "protected"}
    asyncio.run(run())


@pytest.mark.parametrize("failure,code", [(TimeoutError("PRIVATE"), 504), (OSError("PRIVATE"), 500)])
def test_uncertain_error_is_cached_and_slot_released(failure, code):
    async def run():
        gateway = Stations()
        gateway.failure = failure
        async with api_client(create_app(gateway, token="synthetic-token", allow_control=True)) as client:
            body = await envelope(client)
            first = await client.post(PATH, headers=HEADERS, json=body)
            assert first.status_code == code and first.json()["settings_may_have_changed"]
            repeated = await client.post(PATH, headers=HEADERS, json=body)
            assert repeated.status_code == code and repeated.json()["coordination"]["replayed"]
            assert "PRIVATE" not in repeated.text and len(gateway.calls) == 1
            records = (await client.get("/devices/test/activity", headers=HEADERS)).json()["records"]
            assert records[-1]["kind"] == "command_finished"
            assert records[-1]["details"]["outcome"] == "outcome_unknown"
            assert not (await client.get("/devices/test", headers=HEADERS)).json()["command_context"]["busy"]
            gateway.failure = None
            assert (await client.post(PATH, headers=HEADERS, json=await envelope(client, request_id="synthetic-request-02"))).status_code == 200
    asyncio.run(run())


def test_preconditions_reject_changed_missing_or_stale_cached_settings():
    async def run():
        gateway = Stations()
        async with api_client(create_app(gateway, token="synthetic-token", allow_control=True)) as client:
            original = await envelope(client)
            gateway.watts = 400
            mismatch = await client.post(PATH, headers=HEADERS, json=original)
            assert mismatch.status_code == 409 and mismatch.json()["error"] == "PreconditionFailed"
            assert mismatch.json()["settings_may_have_changed"] is False and not gateway.calls
            assert (await client.post(PATH, headers=HEADERS, json=original)).json()["coordination"]["replayed"]
            fresh = await envelope(client, request_id="synthetic-request-02")
            fresh["expected"]["metrics"]["port_memory_enabled"] = 1
            assert (await client.post(PATH, headers=HEADERS, json=fresh)).json()["error"] == "PreconditionFailed"
            fresh = await envelope(client, request_id="synthetic-request-03")
            old = gateway.snapshot
            gateway.snapshot = lambda name: old(name) | {"available": False}
            assert (await client.post(PATH, headers=HEADERS, json=fresh)).json()["error"] == "PreconditionFailed"
            assert not gateway.calls
    asyncio.run(run())


def test_scoped_result_and_readiness_routes_do_not_leak_other_roles(tmp_path):
    access, _, tokens = policy(tmp_path)
    async def run():
        gateway = Stations()
        monitor, control, partial = [{"Authorization": "Bearer " + token} for token in tokens]
        async with api_client(create_app(gateway, permissions=access, allow_control=True)) as client:
            for path in ("/devices/protected/control-availability", "/devices/protected/command-results/synthetic-request-01"):
                assert (await client.get(path, headers=partial)).status_code == 404
                assert (await client.head(path, headers=partial)).status_code == 404
            report = (await client.get("/devices/test/control-availability", headers=monitor)).json()
            assert all(not row["permitted"] for row in report["commands"])
            body = await envelope(client, headers=control)
            assert (await client.post(PATH, headers=control, json=body)).status_code == 200
            assert (await client.get("/devices/test/command-results/synthetic-request-01", headers=monitor)).status_code == 404
            assert (await client.get("/devices/test/command-results/synthetic-request-01", headers=control)).status_code == 200
            assert (await client.post(PATH, headers=monitor, json=body)).status_code == 403
            assert len(gateway.calls) == 1
    asyncio.run(run())


@pytest.mark.parametrize("metadata", [{"expected": None}, {"coordination": None}, {"coordination": {}},
    {"expected": {"model":"c2000_gen2", "protocol":"native_mqtt", "metrics":{"secret":"PRIVATE"}}}])
def test_malformed_metadata_rejected_without_execution(metadata):
    async def run():
        gateway = Stations()
        async with api_client(create_app(gateway, token="synthetic-token", allow_control=True)) as client:
            response = await client.post(PATH, headers=HEADERS, json={"command":"set-charge-power", "watts":500} | metadata)
            assert response.status_code in (400, 409) and response.json()["settings_may_have_changed"] is False
            assert "PRIVATE" not in response.text and not gateway.calls
    asyncio.run(run())


def test_cancellation_caches_unknown_result_and_does_not_hold_station_busy():
    async def run():
        gateway = BlockingStations()
        async with api_client(create_app(gateway, token="synthetic-token", allow_control=True)) as client:
            body = await envelope(client)
            pending = asyncio.create_task(client.post(PATH, headers=HEADERS, json=body))
            await asyncio.wait_for(gateway.entered.wait(), 2)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            # Middleware cancellation must finish the endpoint's tombstone.
            result = (await client.get("/devices/test/command-results/synthetic-request-01", headers=HEADERS)).json()
            assert result["state"] == "finished" and result["http_status"] == 504
            assert result["response"]["settings_may_have_changed"]
            assert not (await client.get("/devices/test", headers=HEADERS)).json()["command_context"]["busy"]
            gateway.release.set()
            repeated = await client.post(PATH, headers=HEADERS, json=body)
            assert repeated.status_code == 504 and repeated.json()["coordination"]["replayed"]
            assert not gateway.calls
    asyncio.run(run())
