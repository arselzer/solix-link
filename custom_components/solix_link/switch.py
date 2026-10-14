"""Capability-gated C1000 configuration switches confirmed by the gateway."""

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .api import binary_state, boolean_setting_supported, fast_charge_enable_allowed, native_gen2
from .coordinator import SolixConfigEntry
from .entity import SolixEntity

PARALLEL_UPDATES = 0
SETTINGS = {"ac_off_grid_alert_enabled": "set-off-grid-alert", "ac_fast_charge_enabled": "set-fast-charge",
            "ac_power_saving_mode_enabled": "set-ac-power-saving", "dc_power_saving_mode_enabled": "set-dc-power-saving",
            "port_memory_enabled": "set-port-memory"}
DESCRIPTIONS = (
    SwitchEntityDescription(key="ac_off_grid_alert_enabled", translation_key="off_grid_alert", entity_category=EntityCategory.CONFIG),
    SwitchEntityDescription(key="ac_fast_charge_enabled", translation_key="fast_charge", entity_category=EntityCategory.CONFIG),
    SwitchEntityDescription(key="ac_power_saving_mode_enabled", translation_key="ac_power_saving", entity_category=EntityCategory.CONFIG),
    SwitchEntityDescription(key="dc_power_saving_mode_enabled", translation_key="dc_power_saving", entity_category=EntityCategory.CONFIG),
    SwitchEntityDescription(key="port_memory_enabled", translation_key="port_memory", entity_category=EntityCategory.CONFIG),
)


async def async_setup_entry(hass, entry: SolixConfigEntry, async_add_entities) -> None:
    coordinator = entry.runtime_data
    added = set()

    @callback
    def discover() -> None:
        entities = []
        if not coordinator.client.token:
            return
        for name, snapshot in (coordinator.data or {}).items():
            power = snapshot.get("ac_power_control") or {}
            if (snapshot.get("model") == "c2000_gen2" and snapshot.get("protocol") == "native_mqtt"
                    and power.get("supported") is True and (name, "ac_output_enabled") not in added):
                added.add((name, "ac_output_enabled"))
                entities.append(SolixACOutputSwitch(coordinator, name))
            for description in DESCRIPTIONS:
                key = description.key
                if (name, key) not in added and boolean_setting_supported(snapshot, SETTINGS[key]):
                    added.add((name, key))
                    entities.append(SolixSettingSwitch(coordinator, name, description))
        if entities:
            async_add_entities(entities)

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))


class SolixSettingSwitch(SolixEntity, SwitchEntity):
    def __init__(self, coordinator, name, description: SwitchEntityDescription) -> None:
        super().__init__(coordinator, name, description.key)
        self.entity_description = description
        self.command = SETTINGS[description.key]
        if self.command in ("set-ac-power-saving", "set-dc-power-saving"):
            self._attr_extra_state_attributes = {"output_behavior": "Power saving may automatically turn the output off at low load."}
            if self.command == "set-dc-power-saving" and self.snapshot.get("protocol") in ("prime", "native_mqtt"):
                self._attr_extra_state_attributes.update(
                    enable_requirement="Requires fresh DC output OFF.",
                    inactivity_behavior="Smart may inherit an inactivity counter; enabling does not guarantee a new grace period.")
                if self.snapshot.get("model") == "c1000_gen2":
                    self._attr_extra_state_attributes["enable_requirement"] = "Requires main 1.1.4.9, fresh DC output OFF and inactive AC/DC countdowns."
            elif self.command == "set-ac-power-saving" and self.snapshot.get("protocol") in ("prime", "native_mqtt"):
                self._attr_extra_state_attributes.update(
                    enable_requirement=("Requires fresh AC output OFF and inactive AC/DC countdowns on C1000 Gen 2 1.1.4.9; original C1000 requires an inactive AC countdown."),
                    inactivity_behavior="Smart may inherit an inactivity counter; enabling does not guarantee a new grace period.")
        elif self.command == "set-port-memory":
            self._attr_extra_state_attributes = {"recovery_behavior": "Off clears output-recovery bookkeeping; turning On does not restore that transient state."}
        elif self.command == "set-fast-charge" and self.snapshot.get("model") == "c1000_gen2":
            self._attr_extra_state_attributes = {"enable_requirement": "C1000 Gen 2 requires Standard mode with no active tariff; native MQTT also requires connected mains."}
        elif self.command == "set-fast-charge" and self.snapshot.get("model") == "c1000":
            self._attr_extra_state_attributes = {
                "supply_requirement": "Use an adequate AC supply.",
                "persistence_behavior": "The flag may clear when AC input is removed; charging speed and reboot persistence are unverified."}

    @property
    def is_on(self) -> bool | None:
        return binary_state(self.snapshot.get("metrics", {}).get(self.entity_description.key))

    @property
    def available(self) -> bool:
        if self.command == "set-fast-charge" and native_gen2(self.snapshot):
            if binary_state(self.snapshot.get("metrics", {}).get("ac_input_connected")) is not True:
                return False
        return boolean_setting_supported(self.snapshot, self.command) and self.control_available(self.command)

    async def _set_enabled(self, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise HomeAssistantError("Use an explicit enabled or disabled setting")
        if not self.available:
            raise HomeAssistantError("Fresh connected telemetry and an enabled gateway control are required")
        if self.command == "set-fast-charge" and enabled and not fast_charge_enable_allowed(self.snapshot):
            raise HomeAssistantError("Fast charge enable requires Standard mode with no active tariff")
        await self.coordinator.async_command(self.station_name, {"command": self.command, "enabled": enabled})

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_enabled(False)


class SolixACOutputSwitch(SolixEntity, SwitchEntity):
    """Owner opt-in plus disabled entity default; dashboards add confirmation."""

    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "ac_output"
    _attr_extra_state_attributes = {
        "warning": "Turning OFF interrupts every AC socket and every attached AC load.",
        "confirmation": "Dashboard confirmation is card-specific; use the supplied guarded card.",
    }

    def __init__(self, coordinator, name):
        super().__init__(coordinator, name, "ac_output_enabled")

    @property
    def is_on(self) -> bool | None:
        return binary_state(self.snapshot.get("metrics", {}).get("ac_output_enabled"))

    @property
    def available(self) -> bool:
        power = self.snapshot.get("ac_power_control") or {}
        return (power.get("enabled") is True and power.get("storage_valid") is True
            and power.get("restore_pending") is not True
            and self.snapshot.get("metrics", {}).get("software_version") == "2.1.6.4"
            and self.is_on is not None and self.control_available("set-ac-output"))

    async def _set(self, enabled: bool):
        if not self.available:
            raise HomeAssistantError("Enable Allow AC output changes and require fresh qualified telemetry")
        await self.coordinator.async_command(self.station_name, {"command": "set-ac-output", "enabled": enabled,
            "acknowledge_power_interruption": not enabled})

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)
