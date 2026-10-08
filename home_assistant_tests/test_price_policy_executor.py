"""Persistent HA ownership and failure tests; fake client/store, no network."""

import asyncio
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from test_recovery_contract import Entry, integration
from test_price_policy import CONFIG, NOW, owned, station


class Store:
    def __init__(self, data=None, fail_save=0):
        self.data, self.fail_save, self.saves = data, fail_save, []

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, value):
        self.saves.append(deepcopy(value))
        if len(self.saves) == self.fail_save:
            raise OSError("synthetic storage failure")
        self.data = deepcopy(value)


class Client:
    def __init__(self, store, fail=False, ignored=False):
        self.url, self.store, self.fail, self.ignored = "http://synthetic.invalid", store, fail, ignored
        self.snapshot, self.writes = station(), []

    async def async_device(self, name):
        return deepcopy(self.snapshot)

    async def async_command(self, name, payload, *, expected_snapshot=None):
        assert self.store.data["stations"][name]["phase"] == "pending"
        assert expected_snapshot == self.snapshot  # Decision baseline, no silent refresh.
        self.writes.append(payload)
        if self.fail: raise ValueError("synthetic uncertain result")
        decision = "grid" if not payload["enabled"] else "charge" if payload["periods"][0]["tariff"] == "off_peak" else "battery"
        if not self.ignored: self.snapshot = station(decision)
        return deepcopy(self.snapshot)


@pytest.mark.parametrize("problem", ["none", "lost_result", "ignored", "save_before", "save_after", "corrupt_store", "preview"])
def test_latch_is_durable_before_write_and_uncertainty_never_retries(integration, monkeypatch, problem):
    async def run():
        store = Store(data={"bad": "shape"} if problem == "corrupt_store" else None,
            fail_save=1 if problem == "save_before" else 2 if problem == "save_after" else 0)
        client = Client(store, fail=problem == "lost_result", ignored=problem == "ignored")
        c = integration.coordinator.SolixCoordinator(None, Entry("synthetic", client.url), client)
        c._price_store = store
        monkeypatch.setattr(integration.coordinator.time, "time", lambda: NOW)
        async def evaluate(mode="apply"):
            return await c.async_price_policy("synthetic", CONFIG, armed=True, price=.1, price_timestamp=NOW, mode=mode)
        if problem in ("none", "preview"):
            result = await evaluate("preview" if problem == "preview" else "apply")
            assert result["eligible"] and result["would_send"]
            assert len(client.writes) == (0 if problem == "preview" else 1)
            assert result["phase"] == ("unowned" if problem == "preview" else "active")
            if problem == "preview": assert not store.saves
        else:
            with pytest.raises(Exception): await evaluate()
            assert len(client.writes) == (0 if problem in ("save_before", "corrupt_store") else 1)
            if client.writes:
                assert c._price_states["synthetic"]["phase"] == "pending"
                if problem == "save_after":
                    with pytest.raises(Exception): await evaluate()
                    assert not c._price_storage_valid and len(client.writes) == 1
                else:
                    result = await evaluate()
                    assert result["reason"] == "manual_reconciliation_required" and len(client.writes) == 1
                # A new coordinator after restart also refuses to replay.
                fresh = integration.coordinator.SolixCoordinator(None, Entry("synthetic", client.url), client)
                fresh._price_store = store
                result = await fresh.async_price_policy("synthetic", CONFIG, armed=True, price=.1, price_timestamp=NOW, mode="apply")
                assert result["reason"] == "manual_reconciliation_required" and len(client.writes) == 1
    asyncio.run(run())


def test_release_forgets_ownership_only_after_empty_plan_confirmation(integration, monkeypatch):
    async def run():
        store = Store({"stations": {"synthetic": owned()}})
        client = Client(store); client.snapshot = station("charge")
        c = integration.coordinator.SolixCoordinator(None, Entry("synthetic", client.url), client)
        c._price_store = store
        monkeypatch.setattr(integration.coordinator.time, "time", lambda: NOW)
        result = await c.async_price_policy("synthetic", CONFIG, mode="release")
        assert result["phase"] == "unowned" and not store.data["stations"]
        assert c.data["synthetic"]["price_policy"]["phase"] == "unowned"
        assert client.writes == [{"command": "set-tou-plan", "periods": [], "enabled": False}]
    asyncio.run(run())


def test_shared_pure_source_and_disabled_blueprint():
    root = Path(__file__).resolve().parents[1]
    assert (root/"python/solix_link/price_policy.py").read_bytes() == (root/"custom_components/solix_link/price_policy.py").read_bytes()
    class Loader(yaml.SafeLoader): pass
    Loader.add_constructor("!input", lambda _loader, node: node.value)
    data = yaml.load((root/"blueprints/automation/solix_link/price_charging.yaml").read_text(), Loader=Loader)
    assert data["initial_state"] is False and data["mode"] == "single"
    assert data["actions"][0]["action"] == "solix_link.price_policy"
    assert data["actions"][0]["data"]["price_entity"] == "price"


def test_cancelled_ownership_load_cannot_be_skipped(integration, monkeypatch):
    async def run():
        class Interrupted(Store):
            async def async_load(self):
                if not getattr(self, "interrupted", False):
                    self.interrupted = True
                    raise asyncio.CancelledError()
                return await super().async_load()
        pending = {**owned(), "phase": "pending"}
        store = Interrupted({"stations": {"synthetic": pending}})
        client = Client(store); client.snapshot = station("charge")
        c = integration.coordinator.SolixCoordinator(None, Entry("synthetic", client.url), client)
        c._price_store = store
        monkeypatch.setattr(integration.coordinator.time, "time", lambda: NOW)
        with pytest.raises(asyncio.CancelledError):
            await c.async_price_policy("synthetic", CONFIG, armed=True, mode="apply", price=.1, price_timestamp=NOW)
        assert not c._price_loaded
        result = await c.async_price_policy("synthetic", CONFIG, armed=True, mode="apply", price=.1, price_timestamp=NOW)
        assert result["reason"] == "manual_reconciliation_required" and not client.writes
    asyncio.run(run())


def test_cancelled_persistence_blocks_this_coordinator(integration, monkeypatch):
    async def run():
        class Interrupted(Store):
            async def async_save(self, value):
                await super().async_save(value)
                raise asyncio.CancelledError()
        store = Interrupted(); client = Client(store)
        c = integration.coordinator.SolixCoordinator(None, Entry("synthetic", client.url), client)
        c._price_store = store
        monkeypatch.setattr(integration.coordinator.time, "time", lambda: NOW)
        with pytest.raises(asyncio.CancelledError):
            await c.async_price_policy("synthetic", CONFIG, armed=True, mode="apply", price=.1, price_timestamp=NOW)
        assert not client.writes and not c._price_storage_valid
        assert store.data["stations"]["synthetic"]["phase"] == "pending"
    asyncio.run(run())


def test_manual_ha_commands_revoke_policy_ownership_before_write(integration):
    async def run():
        store = Store({"stations": {"synthetic": owned()}})
        client = Client(store)
        async def manual(name, payload):
            assert store.data["stations"][name]["phase"] == "blocked"
            return station()
        client.async_command = manual
        c = integration.coordinator.SolixCoordinator(None, Entry("synthetic", client.url), client)
        c._price_store = store
        await c._async_restore_price()
        await c.async_command("synthetic", {"command": "set-charge-power", "watts": 300})
        assert c._price_states["synthetic"]["phase"] == "blocked"
    asyncio.run(run())
