"""UI configuration and local gateway reauthentication."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import selector

from .api import GatewayAuthError, GatewayClient, GatewayError, gateway_id, normalize_url
from .const import CONF_NATIVE_ENERGY_ENABLED, CONF_TOKEN, CONF_URL, DOMAIN


def schema(defaults: dict, *, token_only: bool = False) -> vol.Schema:
    fields = {}
    if not token_only:
        fields[vol.Required(CONF_URL, default=defaults.get(CONF_URL, "http://"))] = str
    fields[vol.Optional(CONF_TOKEN)] = selector.TextSelector(
        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
    )
    return vol.Schema(fields)


class SolixConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SolixOptionsFlow()

    async def _validate(self, data: dict) -> dict:
        normalized = {CONF_URL: normalize_url(data[CONF_URL]), CONF_TOKEN: data.get(CONF_TOKEN, "").strip()}
        client = GatewayClient(async_get_clientsession(self.hass), normalized[CONF_URL], normalized[CONF_TOKEN])
        await client.async_devices()
        return normalized

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            try:
                data = await self._validate(user_input)
            except ValueError:
                errors["base"] = "invalid_url"
            except GatewayAuthError:
                errors["base"] = "invalid_auth"
            except GatewayError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(gateway_id(data[CONF_URL]))
                self._abort_if_unique_id_configured()
                self._async_abort_entries_match({CONF_URL: data[CONF_URL]})
                return self.async_create_entry(title="SOLIX Link", data=data)
        return self.async_show_form(step_id="user", data_schema=schema(user_input or {}), errors=errors)

    async def async_step_reauth(self, entry_data: dict) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors = {}
        if user_input is not None:
            try:
                data = await self._validate({**entry.data, CONF_TOKEN: user_input.get(CONF_TOKEN, "")})
            except GatewayAuthError:
                errors["base"] = "invalid_auth"
            except (GatewayError, ValueError):
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(entry, data_updates=data)
        return self.async_show_form(step_id="reauth_confirm", data_schema=schema({}, token_only=True), errors=errors)

    async def async_step_reconfigure(self, user_input: dict | None = None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            try:
                data = await self._validate({**user_input, CONF_TOKEN: user_input.get(CONF_TOKEN, entry.data.get(CONF_TOKEN, ""))})
            except ValueError:
                errors["base"] = "invalid_url"
            except GatewayAuthError:
                errors["base"] = "invalid_auth"
            except GatewayError:
                errors["base"] = "cannot_connect"
            else:
                if any(other.entry_id != entry.entry_id and other.data.get(CONF_URL) == data[CONF_URL]
                       for other in self._async_current_entries()):
                    return self.async_abort(reason="already_configured")
                return self.async_update_reload_and_abort(entry, data_updates=data, reason="reconfigure_successful")
        return self.async_show_form(step_id="reconfigure", data_schema=schema(dict(entry.data)), errors=errors)


class SolixOptionsFlow(OptionsFlowWithReload):
    """Choose defaults for future diagnostics without changing station settings."""

    async def async_step_init(self, user_input: dict | None = None) -> ConfigFlowResult:
        options = self.config_entry.options
        if user_input is not None:
            return self.async_create_entry(title="", data={**options,
                CONF_NATIVE_ENERGY_ENABLED: user_input[CONF_NATIVE_ENERGY_ENABLED]})
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Required(CONF_NATIVE_ENERGY_ENABLED,
                default=options.get(CONF_NATIVE_ENERGY_ENABLED, False) is True): bool,
        }))
