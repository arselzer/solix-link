"""HTTP owner permission, exact command shapes and scope isolation via ASGI."""

import asyncio

import pytest

from http_helpers import api_client
from solix_link.access import AccessPolicy, Principal
from solix_link.commands import native_commands_for_model
from solix_link.server import create_app
from test_c2000_power_recovery import make


class PowerGateway:
    def __init__(self, server):
        self.server = server
        self.devices = {server.config.name: server.config}

    async def start(self): pass
    async def stop(self): await self.server.stop()
    def snapshot(self, name): return self.server.snapshot()
    def snapshots(self): return [self.server.snapshot()]
    def supported_commands(self, name):
        status = self.server.ac_output_permission.snapshot()
        return [command for command in native_commands_for_model(self.server.config.model)
            if command not in ("set-ac-output", "cycle-ac-output") or status["enabled"] and status["storage_valid"]]
    async def set_ac_output_permission(self, name, **values):
        return await self.server.configure_ac_output_permission(**values)
    async def command(self, name, command, **values):
        if command == "set-ac-output": return await self.server.set_ac_output_enabled(**values)
        return await self.server.cycle_ac_output(**values)


def test_owner_unlocks_metadata_only_and_scoped_writer_cannot_unlock(tmp_path):
    async def run():
        server, station = make(tmp_path)
        server.ac_output_permission.configure(False, acknowledge_high_impact=False)
        await station.request(server.commands.status())
        gateway = PowerGateway(server)
        policy = AccessPolicy(((b"Bearer owner-synthetic", Principal(frozenset({"test"}),
            (("test", frozenset({"set-ac-output", "cycle-ac-output"})),), frozenset({"test"}))),
            (b"Bearer writer-synthetic", Principal(frozenset({"test"}),
                (("test", frozenset({"set-ac-output", "cycle-ac-output"})),)))))
        owner, writer = [{"Authorization": "Bearer " + role + "-synthetic"} for role in ("owner", "writer")]
        async with api_client(create_app(gateway, permissions=policy, allow_control=True)) as client:
            status = (await client.get("/devices/test", headers=writer)).json()
            assert not status["ac_power_control"]["manageable"] and not status["controls"]
            body = {"enabled": True, "acknowledge_high_impact": True}
            path = "/devices/test/ac-output-permission"
            assert (await client.post(path, json=body, headers=writer)).status_code == 403
            assert (await client.post(path, json={**body, "acknowledge_high_impact": False}, headers=owner)).status_code == 403
            assert (await client.post(path, json={**body, "unexpected": 1}, headers=owner)).status_code == 400
            response = await client.post(path, json=body, headers=owner)
            assert response.status_code == 200 and response.json()["ac_power_control"]["enabled"]
            assert all(command == "0100" for command, _ in station.requests)
            off = {"command": "set-ac-output", "enabled": False, "acknowledge_power_interruption": False}
            assert (await client.post("/devices/test/commands", json=off, headers=writer)).status_code == 403
            off["acknowledge_power_interruption"] = True
            response = await client.post("/devices/test/commands", json=off, headers=writer)
            assert response.status_code == 200 and response.json()["metrics"]["ac_output_enabled"] == 0
            assert response.json()["command_result"]["readback"]["reported_values_match"] is True
            assert not response.json()["command_result"]["physical_behavior_verified"]
            on = {"command": "set-ac-output", "enabled": True, "acknowledge_power_interruption": False}
            assert (await client.post("/devices/test/commands", json=on, headers=writer)).status_code == 200
            assert (await client.post(path, json={"enabled": False, "acknowledge_high_impact": False}, headers=owner)).status_code == 200
            count = len(station.requests)
            assert (await client.post("/devices/test/commands", json=off, headers=writer)).status_code == 403
            assert len(station.requests) == count
            assert sum(command == "0101" for command, _ in station.requests) == 2
            assert server.ac_output_permission.identity not in response.text
    asyncio.run(run())
