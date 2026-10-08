"""Discrete C1000 settings confirmed through the gateway."""

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .api import (DEVICE_TIMEOUT_OPTIONS, DISPLAY_BRIGHTNESS_OPTIONS, DISPLAY_TIMEOUT_OPTIONS, LIGHT_MODE_OPTIONS,
                  binary_state, device_timeout_options, discharge_floor_options, display_brightness_options,
                  display_timeout_options, light_mode_options, native_gen2, temperature_unit_supported,
                  clock_brightness_supported)
from .coordinator import SolixConfigEntry
from .entity import SolixEntity

PARALLEL_UPDATES = 0
SETTINGS = {
    "temperature_unit_fahrenheit": "set-temperature-unit",
    "min_charge_percentage": "set-discharge-floor",
    "device_timeout_minutes": "set-device-timeout",
    "display_brightness": "set-display-brightness",
    "display_timeout_seconds": "set-display-timeout",
    "light_mode": "set-light",
    "clock_screen_first_brightness_flag_raw": "set-clock-brightness",
    "clock_screen_second_brightness_flag_raw": "set-clock-brightness",
}
CLOCK_BRIGHTNESS_WINDOWS = {"clock_screen_first_brightness_flag_raw": 1,
                            "clock_screen_second_brightness_flag_raw": 2}
DISPLAY_OPTIONS = {"display_brightness": (DISPLAY_BRIGHTNESS_OPTIONS, display_brightness_options, "level"),
                   "display_timeout_seconds": (DISPLAY_TIMEOUT_OPTIONS, display_timeout_options, "seconds"),
                   "light_mode": (LIGHT_MODE_OPTIONS, light_mode_options, "mode")}
DESCRIPTIONS = (
    SelectEntityDescription(key="temperature_unit_fahrenheit", translation_key="temperature_unit",
                            entity_category=EntityCategory.CONFIG),
    SelectEntityDescription(key="min_charge_percentage", translation_key="discharge_floor",
                            entity_category=EntityCategory.CONFIG),
    SelectEntityDescription(key="device_timeout_minutes", translation_key="device_timeout",
                            entity_category=EntityCategory.CONFIG),
    SelectEntityDescription(key="display_brightness", translation_key="display_brightness",
                            entity_category=EntityCategory.CONFIG),
    SelectEntityDescription(key="display_timeout_seconds", translation_key="display_timeout",
                            entity_category=EntityCategory.CONFIG),
    SelectEntityDescription(key="light_mode", translation_key="light_mode",
                            entity_category=EntityCategory.CONFIG),
    SelectEntityDescription(key="clock_screen_first_brightness_flag_raw", translation_key="clock_first_brightness",
                            entity_category=EntityCategory.CONFIG, entity_registry_enabled_default=False),
    SelectEntityDescription(key="clock_screen_second_brightness_flag_raw", translation_key="clock_second_brightness",
                            entity_category=EntityCategory.CONFIG, entity_registry_enabled_default=False),
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
            for description in DESCRIPTIONS:
                key = description.key
                if key == "min_charge_percentage" and (snapshot.get("model") != "c1000_gen2" or not native_gen2(snapshot)):
                    continue
                if (name, key) in added or SETTINGS[key] not in snapshot.get("controls", []):
                    continue
                if key in CLOCK_BRIGHTNESS_WINDOWS:
                    reported = clock_brightness_supported(snapshot, CLOCK_BRIGHTNESS_WINDOWS[key])
                elif key in DISPLAY_OPTIONS:
                    reported = bool(DISPLAY_OPTIONS[key][1](snapshot))
                elif key == "device_timeout_minutes":
                    reported = bool(device_timeout_options(snapshot))
                else:
                    reported = (temperature_unit_supported(snapshot)
                                if key == "temperature_unit_fahrenheit" else bool(discharge_floor_options(snapshot)))
                if reported:
                    added.add((name, key))
                    entities.append(SolixSelect(coordinator, name, description))
        if entities:
            async_add_entities(entities)

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))


class SolixSelect(SolixEntity, SelectEntity):
    def __init__(self, coordinator, name, description: SelectEntityDescription) -> None:
        super().__init__(coordinator, name, description.key)
        self.entity_description = description
        self.command = SETTINGS[description.key]
        if description.key == "display_timeout_seconds" and self.snapshot.get("model") == "c2000_gen2":
            self._attr_extra_state_attributes = {
                "validated_transport": "BLE 30/60-second write and readback",
                "native_transport_validation": "Synthetic preservation tests; hardware round trip pending",
            }
        if description.key in CLOCK_BRIGHTNESS_WINDOWS:
            self._attr_extra_state_attributes = {
                "setting_scope": "Changes an inactive clock window's stored brightness flag; does not enable clock.",
                "enable_requirement": "Requires Standard mode, no active tariff, clock off, idle transfer and inactive output countdowns.",
            }
        elif description.key == "device_timeout_minutes":
            self._attr_extra_state_attributes = {
                "timeout_behavior": "Never disables this timeout; other sleep behavior may still interrupt remote access.",
                "finite_timeout_behavior": "The station may turn off when idle, interrupting remote access.",
            }

    @property
    def options(self) -> list[str]:
        if self.entity_description.key in CLOCK_BRIGHTNESS_WINDOWS:
            window = CLOCK_BRIGHTNESS_WINDOWS[self.entity_description.key]
            return ["normal", "high"] if clock_brightness_supported(self.snapshot, window) else []
        if self.entity_description.key in DISPLAY_OPTIONS:
            return DISPLAY_OPTIONS[self.entity_description.key][1](self.snapshot)
        if self.entity_description.key == "device_timeout_minutes":
            return device_timeout_options(self.snapshot)
        if self.entity_description.key == "temperature_unit_fahrenheit":
            return ["celsius", "fahrenheit"] if temperature_unit_supported(self.snapshot) else []
        return discharge_floor_options(self.snapshot)

    @property
    def current_option(self) -> str | None:
        key = self.entity_description.key
        value = self.snapshot.get("metrics", {}).get(key)
        if key in CLOCK_BRIGHTNESS_WINDOWS:
            return ("high" if value == 1 else "normal") if self.options else None
        if key in DISPLAY_OPTIONS:
            options = DISPLAY_OPTIONS[key][0]
            return next((option for option in self.options if options[option] == value), None)
        if key == "device_timeout_minutes":
            return next((option for option in self.options if DEVICE_TIMEOUT_OPTIONS[option] == value), None)
        if key == "temperature_unit_fahrenheit":
            if not temperature_unit_supported(self.snapshot):
                return None
            state = binary_state(value)
            return None if state is None else "fahrenheit" if state else "celsius"
        option = f"{value}%" if type(value) is int else None
        return option if option in self.options else None

    @property
    def available(self) -> bool:
        key = self.entity_description.key
        if key in CLOCK_BRIGHTNESS_WINDOWS:
            supported = clock_brightness_supported(self.snapshot, CLOCK_BRIGHTNESS_WINDOWS[key])
        elif key in DISPLAY_OPTIONS:
            supported = bool(DISPLAY_OPTIONS[key][1](self.snapshot))
        elif key == "device_timeout_minutes":
            supported = bool(device_timeout_options(self.snapshot))
        elif key == "temperature_unit_fahrenheit":
            supported = temperature_unit_supported(self.snapshot)
        else:
            supported = self.snapshot.get("model") == "c1000_gen2" and native_gen2(self.snapshot)
        return supported and self.control_available(self.command) and self.current_option is not None

    async def async_select_option(self, option: str) -> None:
        if not isinstance(option, str) or option not in self.options:
            raise HomeAssistantError("Choose one of the currently available setting options")
        if not self.available:
            raise HomeAssistantError("Fresh connected telemetry and an enabled gateway control are required")
        if self.entity_description.key in CLOCK_BRIGHTNESS_WINDOWS:
            payload = {"command": self.command, "window": CLOCK_BRIGHTNESS_WINDOWS[self.entity_description.key],
                       "high": option == "high"}
        elif self.entity_description.key in DISPLAY_OPTIONS:
            choices, _, field = DISPLAY_OPTIONS[self.entity_description.key]
            payload = {"command": self.command, field: choices[option]}
        elif self.entity_description.key == "device_timeout_minutes":
            payload = {"command": self.command, "minutes": DEVICE_TIMEOUT_OPTIONS[option]}
        elif self.entity_description.key == "temperature_unit_fahrenheit":
            payload = {"command": self.command, "fahrenheit": option == "fahrenheit"}
        else:
            payload = {"command": self.command, "lower": int(option.removesuffix("%"))}
        await self.coordinator.async_command(self.station_name, payload)
