"""Mains presence and output state from fresh gateway telemetry."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.core import callback
from homeassistant.const import EntityCategory

from .api import binary_state
from .coordinator import SolixConfigEntry
from .entity import SolixEntity

PARALLEL_UPDATES = 0
DESCRIPTIONS = (
    BinarySensorEntityDescription(key="ac_input_connected", translation_key="mains_present",
                                  device_class=BinarySensorDeviceClass.POWER),
    BinarySensorEntityDescription(key="ac_output_enabled", translation_key="ac_output_enabled",
                                  device_class=BinarySensorDeviceClass.POWER),
    BinarySensorEntityDescription(key="ac_fast_charge_enabled", translation_key="ac_fast_charge_enabled",
                                  entity_category=EntityCategory.DIAGNOSTIC,
                                  entity_registry_enabled_default=True),
    BinarySensorEntityDescription(key="dc_input_active", translation_key="dc_input_active",
                                  entity_category=EntityCategory.DIAGNOSTIC,
                                  entity_registry_enabled_default=False),
    BinarySensorEntityDescription(key="pv_weak_light_locked", translation_key="pv_weak_light_locked",
                                  entity_category=EntityCategory.DIAGNOSTIC,
                                  entity_registry_enabled_default=False),
    BinarySensorEntityDescription(key="disaster_preparation_active", translation_key="disaster_preparation_active",
                                  entity_category=EntityCategory.DIAGNOSTIC,
                                  entity_registry_enabled_default=True),
    *(BinarySensorEntityDescription(key=key, translation_key=key, entity_category=EntityCategory.DIAGNOSTIC,
                                   entity_registry_enabled_default=False)
      for key in ("ac_power_saving_mode_enabled", "dc_power_saving_mode_enabled")),
)


async def async_setup_entry(hass, entry: SolixConfigEntry, async_add_entities) -> None:
    coordinator = entry.runtime_data
    added = set()

    @callback
    def discover() -> None:
        entities = []
        for name, snapshot in (coordinator.data or {}).items():
            for description in DESCRIPTIONS:
                key = description.key
                if key in ("pv_weak_light_locked", "disaster_preparation_active") and snapshot.get("model") != "c1000_gen2":
                    continue
                if key == "ac_fast_charge_enabled" and (snapshot.get("model") != "c2000_gen2"
                                                         or snapshot.get("protocol") != "native_mqtt"):
                    continue
                # Original C1000 already has supported switches for these.
                if key in ("ac_power_saving_mode_enabled", "dc_power_saving_mode_enabled") and snapshot.get("model") not in ("c1000_gen2", "c2000_gen2"):
                    continue
                if key in snapshot["metrics"] and (name, key) not in added:
                    added.add((name, key))
                    entities.append(SolixBinarySensor(coordinator, name, description))
        if entities:
            async_add_entities(entities)

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))


class SolixBinarySensor(SolixEntity, BinarySensorEntity):
    def __init__(self, coordinator, name, description: BinarySensorEntityDescription) -> None:
        super().__init__(coordinator, name, description.key)
        self.entity_description = description
        if description.key == "pv_weak_light_locked":
            self._attr_extra_state_attributes = {"provenance": "C1000 Gen 2 firmware-derived flag; physical PV behavior untested."}
        if description.key == "disaster_preparation_active":
            self._attr_extra_state_attributes = {"provenance": "C1000 Gen 2 D9 firmware-derived active flag; not a complete disaster-plan export."}

    @property
    def is_on(self) -> bool | None:
        return binary_state(self.snapshot.get("metrics", {}).get(self.entity_description.key))

    @property
    def available(self) -> bool:
        if self.entity_description.key in ("pv_weak_light_locked", "disaster_preparation_active") and self.snapshot.get("model") != "c1000_gen2":
            return False
        if self.entity_description.key == "ac_fast_charge_enabled" and (self.snapshot.get("model") != "c2000_gen2"
                                                                       or self.snapshot.get("protocol") != "native_mqtt"):
            return False
        return super().available and self.is_on is not None
