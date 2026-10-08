"""Home Assistant integration for a separately running SOLIX Link gateway."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .api import GatewayClient, GatewayError, device_id
from .const import CONF_TOKEN, CONF_URL, DOMAIN
from .coordinator import SolixConfigEntry, SolixCoordinator
from .price_policy import CONFIG_KEYS

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.NUMBER, Platform.BUTTON,
             Platform.SELECT, Platform.SWITCH]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    def selected(device_id_value: str) -> tuple[SolixCoordinator, str]:
        device = dr.async_get(hass).async_get(device_id_value)
        if device is not None:
            for entry in hass.config_entries.async_entries(DOMAIN):
                coordinator = getattr(entry, "runtime_data", None)
                if (entry.state is not ConfigEntryState.LOADED
                        or entry.entry_id not in device.config_entries
                        or not isinstance(coordinator, SolixCoordinator)):
                    continue
                for name in coordinator.data or {}:
                    if (DOMAIN, device_id(coordinator.endpoint_id, name)) in device.identifiers:
                        return coordinator, name
        raise HomeAssistantError("Select a loaded SOLIX Link device")

    async def set_tou_plan(call: ServiceCall) -> None:
        coordinator, name = selected(call.data["device_id"])
        await coordinator.async_command(name, {
            "command": "set-tou-plan", "periods": call.data["periods"], "enabled": call.data["enabled"],
        })

    async def price_policy(call: ServiceCall) -> dict:
        coordinator, name = selected(call.data["device_id"])
        state = hass.states.get(call.data.get("price_entity", ""))
        price = timestamp = None
        unit = state.attributes.get("unit_of_measurement") if state is not None else None
        if state is not None and isinstance(unit, str) and unit.endswith("/kWh"):
            try:
                price = float(state.state)
                timestamp = state.last_reported.timestamp()
            except (ValueError, TypeError, AttributeError):
                pass
        try:
            return await coordinator.async_price_policy(name, {key: call.data[key] for key in CONFIG_KEYS},
                price=price, price_timestamp=timestamp, armed=call.data["armed"],
                override=call.data["override"], mode=call.data["mode"])
        except (OSError, ValueError, GatewayError) as err:
            raise HomeAssistantError("Price policy could not confirm its plan; inspect ownership before retrying") from err

    hass.services.async_register(DOMAIN, "set_tou_plan", set_tou_plan, schema=vol.Schema({
        vol.Required("device_id"): str,
        vol.Required("periods"): [dict],
        vol.Required("enabled"): bool,
    }))
    hass.services.async_register(DOMAIN, "price_policy", price_policy, supports_response=SupportsResponse.OPTIONAL,
        schema=vol.Schema({
            vol.Required("device_id"): str,
            vol.Optional("price_entity", default=""): str,
            vol.Optional("mode", default="preview"): vol.In(("preview", "apply", "release", "reset")),
            vol.Optional("armed", default=False): bool,
            vol.Optional("override", default="none"): vol.In(("none", "hold", "charge", "grid", "battery")),
            vol.Optional("charge_start", default=0.15): vol.Coerce(float),
            vol.Optional("charge_stop", default=0.20): vol.Coerce(float),
            vol.Optional("discharge_stop", default=0.30): vol.Coerce(float),
            vol.Optional("discharge_start", default=0.35): vol.Coerce(float),
            vol.Optional("cooldown", default=300): int,
            vol.Optional("price_max_age", default=300): int,
            vol.Optional("minimum_reserve", default=20): int,
            vol.Optional("soc_resume_margin", default=5): int,
        }))
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SolixConfigEntry) -> bool:
    client = GatewayClient(async_get_clientsession(hass), entry.data[CONF_URL], entry.data.get(CONF_TOKEN, ""))
    coordinator = SolixCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SolixConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
