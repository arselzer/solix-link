"""One coordinated local HTTP poll for all station entities."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import GatewayAuthError, GatewayClient, GatewayError, gateway_id
from .const import DOMAIN, POLL_SECONDS
from .charging_controller import confirmed_state, controller_decision, owner, validate_store
from .repair_notifications import sync_repairs
from .price_policy import _number
from .history import (HISTORY_DISCOVERY_SECONDS, HISTORY_POLL_SECONDS,
                      HistoryValidationError, history_nonregressing, parse_history_highwater,
                      parse_history_summary, history_storage_evidence)

_LOGGER = logging.getLogger(__name__)


class SolixCoordinator(DataUpdateCoordinator[dict[str, dict]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: GatewayClient) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry,
                         update_interval=timedelta(seconds=POLL_SECONDS), always_update=False)
        self.client = client
        self.endpoint_id = entry.unique_id or gateway_id(client.url)
        self._io_lock = asyncio.Lock()
        self._history_cache: dict | None = None
        self._history_next_poll = 0.0
        self._history_highwater: dict[str, dict] = {}
        self._history_loaded = False
        self._history_dirty = False
        self._history_storage_valid = True
        self.history_accounting_ready = False
        self._history_store = None
        self._price_store = None
        self._price_loaded = False
        self._price_storage_valid = True
        self._price_states: dict[str, dict] = {}
        self._policy_runtime: dict[str, dict] = {}
        if hass is not None:
            from homeassistant.helpers.storage import Store
            self._history_store = Store(hass, 1, f"{DOMAIN}.energy_continuity.{entry.entry_id}")
            self._price_store = Store(hass, 1, f"{DOMAIN}.price_policy.{entry.entry_id}")

    async def _async_restore_price(self) -> None:
        if self._price_loaded:
            return
        if self._price_store is None:
            self._price_loaded = True
            return
        try:
            path = getattr(self._price_store, "path", None)
            evidence = (await self.hass.async_add_executor_job(history_storage_evidence, path)
                        if isinstance(path, str) else False)
            raw = await self._price_store.async_load()
            if raw is None and evidence:
                raise ValueError("Charging ownership storage was lost or corrupt")
            self._price_states = validate_store(raw)
        except (OSError, ValueError, TypeError, HomeAssistantError):
            self._price_storage_valid = False
            _LOGGER.error("Unable to restore charging ownership; automatic charging commands are blocked")
        self._price_loaded = True
        sync_repairs(self, self.data or {})

    async def _async_save_price(self, name: str, state: dict | None) -> None:
        if self._price_store is None or not self._price_storage_valid:
            raise HomeAssistantError("Price policy ownership storage is unavailable")
        states = dict(self._price_states)
        if state is None:
            states.pop(name, None)
        else:
            states[name] = state
        validate_store({"stations": states})
        try:
            await self._price_store.async_save({"stations": states})
        except asyncio.CancelledError:
            # Storage can finish after task cancellation. Do not continue in
            # this coordinator with an ownership state that may now be stale.
            self._price_storage_valid = False
            raise
        except (OSError, ValueError, TypeError, HomeAssistantError) as err:
            self._price_storage_valid = False
            raise HomeAssistantError("Charging ownership could not be persisted; commands are blocked") from err
        self._price_states = states

    def _attach_price(self, data: dict[str, dict]) -> dict[str, dict]:
        result = dict(data)
        for name, snapshot in data.items():
            if (snapshot.get("model") == "c1000_gen2" and snapshot.get("protocol") == "native_mqtt"
                    or name in self._price_states or "price_policy" in snapshot or not self._price_storage_valid):
                state = self._price_states.get(name, {})
                result[name] = {**snapshot, "price_policy": {
                    "phase": "storage_error" if not self._price_storage_valid else state.get("phase", "unowned"),
                    "owner": owner(state) if state else None,
                    "decision": state.get("decision"), "changed_at": state.get("changed_at"),
                    **self._policy_runtime.get(name, {}),
                }}
        sync_repairs(self, result)
        return result

    async def async_price_policy(self, name: str, config: dict, *, price: float | None = None,
                                 price_timestamp: float | None = None, armed: bool = False,
                                 override: str = "none", mode: str = "preview") -> dict:
        return await self.async_charging_policy(name, config, policy="price", price=price,
            price_timestamp=price_timestamp, armed=armed, override=override, mode=mode)

    async def async_charging_policy(self, name: str, config: dict, *, policy: str,
                                    price: float | None = None, price_timestamp: float | None = None,
                                    export: float | None = None, export_timestamp: float | None = None,
                                    armed: bool = False, override: str = "none", mode: str = "preview") -> dict:
        """Persist a latch before one guarded plan change; never retry uncertainty."""
        if mode not in ("preview", "apply", "release", "reset"):
            raise HomeAssistantError("Invalid price policy mode")
        async with self._io_lock:
            data = dict(self.data or {})
            try:
                await self._async_restore_price()
                if not self._price_storage_valid:
                    raise HomeAssistantError("Charging ownership storage is invalid")
                snapshot = await self.client.async_device(name)
                data[name] = snapshot
                result = controller_decision(snapshot, config, self._price_states.get(name), policy=policy,
                    now=time.time(), price=price, price_timestamp=price_timestamp,
                    export=export, export_timestamp=export_timestamp, armed=armed, override=override,
                    action=mode if mode in ("release", "reset") else "evaluate")
                # Preview must not replace the executor's health/arming record.
                if mode != "preview" and result["reason"] != "another_policy_owns_station":
                    signal_timestamp = price_timestamp if policy == "price" else export_timestamp
                    self._policy_runtime[name] = {"policy": policy, "mode": mode,
                        "armed": armed, "override": override, "reason": result["reason"],
                        "checked_at": time.time(),
                        "signal_timestamp": signal_timestamp if _number(signal_timestamp) else None,
                        "signal_max_age": config["price_max_age" if policy == "price" else "export_max_age"]}
                if mode != "preview":
                    if result["next_state"] != self._price_states.get(name):
                        await self._async_save_price(name, result["next_state"])
                    if result["command"] is not None:
                        # The exact decision snapshot supplies the optimistic
                        # gateway preconditions. Do not fetch a new baseline
                        # that silently accepts intervening manual changes.
                        data[name] = await self.client.async_command(name, result["command"], expected_snapshot=snapshot)
                        confirmed = confirmed_state(data[name], result["next_state"], now=time.time())
                        await self._async_save_price(name, None if mode == "release" else confirmed)
            finally:
                self.async_set_updated_data(self._attach_price(self._attach_history(data)))
            return {key: result[key] for key in ("eligible", "reason", "decision")} | {
                "would_send": result["command"] is not None,
                "phase": self._price_states.get(name, {}).get("phase", "unowned"),
                "owner": owner(self._price_states.get(name)),
                "electrical_behavior_verified": False,
            }

    async def _async_restore_history(self) -> None:
        if self._history_loaded:
            return
        self._history_loaded = True
        if self._history_store is None:
            return
        try:
            path = getattr(self._history_store, "path", None)
            evidence = (await self.hass.async_add_executor_job(history_storage_evidence, path)
                        if isinstance(path, str) else False)
            raw = await self._history_store.async_load()
            if raw is None and evidence:
                raise HistoryValidationError()
            self._history_highwater = {} if raw is None else parse_history_highwater(raw)
        except (OSError, ValueError, TypeError, KeyError, NotImplementedError, HomeAssistantError):
            # Preserve corrupt continuity evidence; never overwrite it with a new baseline.
            self._history_storage_valid = False
            _LOGGER.error("Unable to restore energy continuity; energy statistics are unavailable")

    async def _async_save_history(self) -> None:
        self.history_accounting_ready = False
        if self._history_store is None or not self._history_storage_valid:
            return
        if self._history_dirty:
            try:
                await self._history_store.async_save({"stations": list(self._history_highwater.values())})
            except (OSError, ValueError, TypeError, HomeAssistantError):
                _LOGGER.error("Unable to persist energy continuity; energy statistics are unavailable")
                return
            self._history_dirty = False
        self.history_accounting_ready = True

    async def _async_history(self) -> None:
        if time.monotonic() < self._history_next_poll:
            return
        self._history_next_poll = time.monotonic() + HISTORY_POLL_SECONDS
        try:
            raw = await self.client.async_history_summary()
            summary = None if raw is None else parse_history_summary(raw)
        except GatewayAuthError:
            self._history_cache = None
            self._history_next_poll = 0.0
            raise
        except (GatewayError, HistoryValidationError):
            self._history_cache = None
            return
        self._history_cache = summary
        if summary is None or not summary["enabled"]:
            self._history_next_poll = time.monotonic() + HISTORY_DISCOVERY_SECONDS

    def _attach_history(self, data: dict[str, dict]) -> dict[str, dict]:
        # Command results and peers may already carry an earlier summary.
        # Reattach only the current validated cache; never keep failed history.
        data = {name: {key: value for key, value in snapshot.items() if key != "history"}
                for name, snapshot in data.items()}
        summary = self._history_cache
        if summary is None or not summary["enabled"]:
            return data
        result = dict(data)
        for name, snapshot in data.items():
            candidate = summary["stations"].get(name)
            if candidate is None or (candidate["model"], candidate["protocol"]) != (
                    snapshot.get("model"), snapshot.get("protocol")):
                continue
            history = {**candidate, "updated_at": summary["updated_at"],
                       "max_gap_seconds": summary["max_gap_seconds"]}
            if name not in self._history_highwater and len(self._history_highwater) >= 32:
                continue
            if not history_nonregressing(self._history_highwater.get(name), history):
                continue
            if self._history_highwater.get(name) != history:
                self._history_highwater[name] = history
                self._history_dirty = True
            result[name] = {**snapshot, "history": history}
        return result

    async def _async_update_data(self) -> dict[str, dict]:
        try:
            async with self._io_lock:
                await self._async_restore_history()
                await self._async_restore_price()
                data = await self.client.async_devices()
                await self._async_history()
                data = self._attach_history(data)
                await self._async_save_history()
                return self._attach_price(data)
        except GatewayAuthError as err:
            raise ConfigEntryAuthFailed("Gateway authentication failed") from err
        except GatewayError as err:
            raise UpdateFailed(str(err)) from err

    async def async_command(self, name: str, payload: dict) -> None:
        """Serialize commands and polling, then publish confirmed or refreshed data."""
        error = None
        async with self._io_lock:
            data = dict(self.data or {})
            try:
                await self._async_restore_price()
                if not self._price_storage_valid:
                    raise HomeAssistantError("Charging ownership storage is invalid; reconcile before manual controls")
                if name in self._price_states:
                    await self._async_save_price(name, {**self._price_states[name], "phase": "blocked"})
                data[name] = await self.client.async_command(name, payload)
            except (GatewayError, ValueError) as err:
                error = err
                try:
                    data[name] = await self.client.async_device(name)
                except GatewayError:
                    if name in data:
                        data[name] = {**data[name], "connected": False, "available": False}
            self.async_set_updated_data(self._attach_price(self._attach_history(data)))
        if error is not None:
            raise HomeAssistantError(str(error)) from error


SolixConfigEntry = ConfigEntry[SolixCoordinator]
