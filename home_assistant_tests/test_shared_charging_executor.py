"""One coordinator/store/lock for solar and price, with synthetic clients only."""

import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_recovery_contract import Entry, integration
from test_price_policy_executor import Store
from test_charging_controller import NOW, CONFIG, SURPLUS_CONFIG, station


class Client:
    url = "http://synthetic.invalid"

    def __init__(self, store, fail=False, ignored=False):
        self.store, self.fail, self.ignored = store, fail, ignored
        self.snapshot, self.writes = station(), []

    async def async_device(self, name):
        return deepcopy(self.snapshot)

    async def async_command(self, name, command, *, expected_snapshot):
        assert self.store.data["stations"][name]["phase"] == "pending"
        assert expected_snapshot == self.snapshot
        self.writes.append(command)
        if self.fail:
            raise ValueError("Synthetic uncertain result")
        if not self.ignored:
            self.snapshot = station(ac_charging_power_limit_w=command["watts"])
        return deepcopy(self.snapshot)


@pytest.mark.parametrize("problem", ["none","lost_result","ignored","storage_before","storage_after","preview"])
def test_surplus_latch_restore_and_price_exclusion(integration,monkeypatch,problem):
    async def run():
        store = Store(fail_save=1 if problem=="storage_before" else 2 if problem=="storage_after" else 0)
        client = Client(store, fail=problem=="lost_result", ignored=problem=="ignored")
        c = integration.coordinator.SolixCoordinator(None,Entry("test",client.url),client)
        c._price_store = store
        monkeypatch.setattr(integration.coordinator.time,"time",lambda:NOW)
        async def apply(mode="apply"):
            return await c.async_charging_policy("station",SURPLUS_CONFIG,policy="surplus",
                export=800,export_timestamp=NOW,armed=True,mode=mode)
        if problem in ("none","preview"):
            result = await apply("preview" if problem=="preview" else "apply")
            assert result["eligible"]
            if problem=="preview":
                assert not store.saves and not client.writes and not c._policy_runtime
                return
            assert result["owner"]=="surplus" and len(client.writes)==1
            blocked = await c.async_price_policy("station",CONFIG,armed=True,mode="apply",price=.1,price_timestamp=NOW)
            assert blocked["reason"]=="another_policy_owns_station" and len(client.writes)==1
            result = await apply("release")
            assert result["phase"]=="unowned" and client.writes[-1]=={"command":"set-charge-power","watts":300}
        else:
            with pytest.raises(Exception): await apply()
            writes=len(client.writes)
            if c._price_storage_valid:
                assert (await apply())["reason"]=="manual_reconciliation_required"
            else:
                with pytest.raises(Exception): await apply()
            assert len(client.writes)==writes
            # Persisted pending state also blocks a fresh coordinator.
            if writes:
                fresh=integration.coordinator.SolixCoordinator(None,Entry("test",client.url),client)
                fresh._price_store=store
                result=await fresh.async_charging_policy("station",SURPLUS_CONFIG,policy="surplus",
                    export=800,export_timestamp=NOW,armed=True,mode="apply")
                assert result["reason"]=="manual_reconciliation_required" and len(client.writes)==writes
    asyncio.run(run())


def test_canonical_controller_is_identical_in_the_ha_component():
    root=Path(__file__).resolve().parents[1]
    assert (root/"python/solix_link/charging_controller.py").read_bytes()==(root/"custom_components/solix_link/charging_controller.py").read_bytes()


def test_empty_load_with_corruption_evidence_cannot_acquire_a_fresh_owner(integration,monkeypatch,tmp_path):
    async def run():
        store=Store();store.path=str(tmp_path/"ownership")
        (tmp_path/"ownership.corrupt.synthetic").write_text("synthetic corrupt ownership")
        client=Client(store)
        c=integration.coordinator.SolixCoordinator(None,Entry("test",client.url),client)
        async def executor(function,*args):return function(*args)
        c.hass=SimpleNamespace(async_add_executor_job=executor)
        c._price_store=store
        monkeypatch.setattr(integration.coordinator.time,"time",lambda:NOW)
        with pytest.raises(Exception):
            await c.async_charging_policy("station",SURPLUS_CONFIG,policy="surplus",
                export=800,export_timestamp=NOW,armed=True,mode="apply")
        assert not c._price_storage_valid and not client.writes and not store.saves
    asyncio.run(run())
