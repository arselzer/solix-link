"""Sensors backed by the gateway's reported values."""

from datetime import datetime, UTC
import time

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import EntityCategory, PERCENTAGE, UnitOfEnergy, UnitOfPower, UnitOfTemperature, UnitOfTime
from homeassistant.core import callback

from .api import numeric, parse_tou_plan, snapshot_available
from .coordinator import SolixConfigEntry
from .entity import SolixEntity
from .history import history_available
from .native_energy import ENERGY_CHANNELS, GROUP_NAMES, validate_native_energy

PARALLEL_UPDATES = 0
DESCRIPTIONS = (
    SensorEntityDescription(key="control_availability", translation_key="control_availability",
                            entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=True),
    SensorEntityDescription(key="battery_percentage", translation_key="battery", device_class=SensorDeviceClass.BATTERY,
                            native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="temperature_c", translation_key="temperature", device_class=SensorDeviceClass.TEMPERATURE,
                            native_unit_of_measurement=UnitOfTemperature.CELSIUS, state_class=SensorStateClass.MEASUREMENT),
    *(SensorEntityDescription(key=key, translation_key=key, device_class=SensorDeviceClass.POWER,
                             native_unit_of_measurement=UnitOfPower.WATT, state_class=SensorStateClass.MEASUREMENT)
      for key in ("ac_input_power_w", "ac_output_power_w", "dc_output_power_w", "output_power_w")),
    SensorEntityDescription(key="battery_status", translation_key="battery_status", device_class=SensorDeviceClass.ENUM,
                            options=["idle", "charging", "discharging", "unknown"]),
    SensorEntityDescription(key="active_tariff", translation_key="active_tariff", device_class=SensorDeviceClass.ENUM,
                            options=["none", "peak", "mid_peak", "off_peak", "unknown"]),
    SensorEntityDescription(key="usage_mode", translation_key="usage_mode", device_class=SensorDeviceClass.ENUM,
                            options=["standard", "time_of_use", "self_consumption", "custom", "unknown"]),
    SensorEntityDescription(key="power_flow", translation_key="power_flow", device_class=SensorDeviceClass.ENUM,
                            options=["grid", "battery", "transitioning", "unknown"]),
    SensorEntityDescription(key="last_seen_timestamp", translation_key="last_seen_timestamp",
                            device_class=SensorDeviceClass.TIMESTAMP, entity_category=EntityCategory.DIAGNOSTIC,
                            entity_registry_enabled_default=True),
    # Saved configuration, deliberately without measurement state class.
    SensorEntityDescription(key="ac_output_frequency_setting_hz", translation_key="ac_output_frequency_setting_hz",
                            native_unit_of_measurement="Hz", entity_category=EntityCategory.DIAGNOSTIC,
                            entity_registry_enabled_default=False),
    *(SensorEntityDescription(key=key, translation_key=key, entity_category=EntityCategory.DIAGNOSTIC,
                             entity_registry_enabled_default=False)
      for key in ("dc_input_power_raw", "controller_error_code", "battery_health_raw", "ac_frequency_raw")),
)

# Lifetime integrals are explicitly estimates. No statistics state class or
# Energy-dashboard registration is supplied for these incomplete histories.
HISTORY_DESCRIPTIONS = (
    *(SensorEntityDescription(key=f"history_ac_{channel}_energy_kwh_estimate",
                             translation_key=f"history_ac_{channel}_energy_kwh_estimate",
                             device_class=SensorDeviceClass.ENERGY,
                             native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                             entity_category=EntityCategory.DIAGNOSTIC,
                             entity_registry_enabled_default=False)
      for channel in ("input", "output")),
    *(SensorEntityDescription(key=f"history_ac_{channel}_coverage_seconds",
                             translation_key=f"history_ac_{channel}_coverage_seconds",
                             native_unit_of_measurement=UnitOfTime.SECONDS,
                             entity_category=EntityCategory.DIAGNOSTIC,
                             entity_registry_enabled_default=False)
      for channel in ("input", "output")),
    SensorEntityDescription(key="history_gap_count", translation_key="history_gap_count",
                            entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False),
    *(SensorEntityDescription(key=key, translation_key=key, device_class=SensorDeviceClass.TIMESTAMP,
                             entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False)
      for key in ("history_collection_start", "history_updated_at")),
    SensorEntityDescription(key="history_continuity", translation_key="history_continuity",
                            device_class=SensorDeviceClass.ENUM, options=["continuous", "gap", "pending"],
                            entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False),
)

# Reported mode counters are uncalibrated and can decrease. No long-term energy
# statistics are advertised until model-specific scaling and resets are known.
NATIVE_ENERGY_DESCRIPTIONS = tuple(
    SensorEntityDescription(key=f"native_energy_{group}_{channel}", translation_key="native_energy_kwh",
        device_class=SensorDeviceClass.ENERGY, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False)
    for group in GROUP_NAMES for channel in ENERGY_CHANNELS)
NATIVE_ENERGY_COORDINATES = {f"native_energy_{group}_{channel}": (group, channel)
                             for group in GROUP_NAMES for channel in ENERGY_CHANNELS}


async def async_setup_entry(hass, entry: SolixConfigEntry, async_add_entities) -> None:
    coordinator = entry.runtime_data
    added = set()

    @callback
    def discover() -> None:
        entities = []
        for name, snapshot in (coordinator.data or {}).items():
            for description in DESCRIPTIONS:
                key = description.key
                if key == "ac_output_frequency_setting_hz" and snapshot.get("model") != "c1000_gen2":
                    continue
                if key == "ac_frequency_raw" and snapshot.get("model") != "c2000_gen2":
                    continue
                present = key in (snapshot if key in ("power_flow", "last_seen_timestamp", "control_availability") else snapshot["metrics"])
                if present and (name, key) not in added:
                    added.add((name, key))
                    entities.append(SolixSensor(coordinator, name, description))
            if isinstance(snapshot.get("history"), dict):
                for description in HISTORY_DESCRIPTIONS:
                    if (name, description.key) not in added:
                        added.add((name, description.key))
                        entities.append(SolixHistorySensor(coordinator, name, description))
            energy = validate_native_energy(snapshot.get("native_energy"), model=snapshot.get("model")) if snapshot.get("protocol") == "native_mqtt" else None
            if energy is not None:
                for description in NATIVE_ENERGY_DESCRIPTIONS:
                    group, channel = NATIVE_ENERGY_COORDINATES[description.key]
                    if channel in energy["groups"].get(group, {}).get("energy_kwh", {}) and (name, description.key) not in added:
                        added.add((name, description.key))
                        entities.append(SolixNativeEnergySensor(coordinator, name, description, group, channel))
        if entities:
            async_add_entities(entities)

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))


class SolixSensor(SolixEntity, SensorEntity):
    def __init__(self, coordinator, name, description: SensorEntityDescription) -> None:
        super().__init__(coordinator, name, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        key = self.entity_description.key
        if key == "control_availability":
            report = self.snapshot.get(key)
            return sum(not row["ready"] for row in report["commands"]
                       if not {"model_unsupported", "transport_unsupported"}.intersection(row["reasons"])) if report is not None else None
        if key == "last_seen_timestamp":
            try:
                value = numeric(self.snapshot.get(key))
                if value is None or value > time.time() + 5:
                    return None
                return datetime.fromtimestamp(value, UTC)
            except (OverflowError, OSError, ValueError):
                return None
        value = self.snapshot.get("power_flow") if key == "power_flow" else self.snapshot.get("metrics", {}).get(key)
        if self.entity_description.options is not None:
            return value if value in self.entity_description.options else None
        value = numeric(value)
        if key == "battery_percentage" and value is not None and not 0 <= value <= 100:
            return None
        if key.endswith("_power_w") and value is not None and value < 0:
            return None
        if key == "ac_output_frequency_setting_hz" and value not in (50, 60):
            return None
        return value

    @property
    def available(self) -> bool:
        if self.entity_description.key == "control_availability":
            return self.native_value is not None and self.coordinator.last_update_success
        return self.native_value is not None and super().available

    @property
    def extra_state_attributes(self) -> dict:
        attributes = dict(super().extra_state_attributes)
        if self.entity_description.key == "control_availability":
            report = self.snapshot.get("control_availability")
            if report is not None:
                attributes.update(preflight_only=True, backend_validation_required=True,
                    commands=[{**row, "reasons": list(row["reasons"]), "missing_metrics": list(row["missing_metrics"])}
                              for row in report["commands"]])
        if self.entity_description.key == "usage_mode":
            plan = parse_tou_plan(self.snapshot.get("tou_plan_readback"))
            if plan is not None:
                age = time.time() - plan["reported_at"]
                attributes.update(saved_tou_plan=plan, saved_tou_plan_fresh=self.available
                                  and snapshot_available(self.snapshot, 30) and -5 <= age < 30)
        return attributes


class SolixNativeEnergySensor(SolixEntity, SensorEntity):
    """Cached mode-specific counter, independent of the live power stream."""

    def __init__(self, coordinator, name, description, group: str, channel: str) -> None:
        super().__init__(coordinator, name, description.key)
        self.entity_description = description
        self.group, self.channel = group, channel
        self._attr_translation_placeholders = {"group": group.replace("_", " ").title(), "channel": channel.replace("_", " ").upper()}

    @property
    def report(self):
        if self.snapshot.get("protocol") != "native_mqtt":
            return None
        return validate_native_energy(self.snapshot.get("native_energy"), model=self.snapshot.get("model"), now=time.time())

    @property
    def native_value(self):
        report = self.report
        return report["groups"].get(self.group, {}).get("energy_kwh", {}).get(self.channel) if report else None

    @property
    def available(self) -> bool:
        report = self.report
        return bool(self.coordinator.last_update_success and report and report["available"] and self.native_value is not None)

    @property
    def extra_state_attributes(self) -> dict:
        attributes = {**super().extra_state_attributes, "source": "device_energy_report", "units_verified": False,
                      "mode_group": self.group, "channel": self.channel, "includes_bypass": self.channel in ("ac_input", "ac_output")}
        report = self.report
        if report:
            attributes.update({key: report[key] for key in ("reported_at", "counter_epoch", "counter_epoch_started_at",
                "received_reports", "batch_reports", "continuity", "conversion_basis", "firmware_version", "max_report_age_seconds")})
            attributes["raw_counter"] = report["groups"].get(self.group, {}).get("raw", {}).get(f"{self.channel}_energy_raw")
            attributes["raw_group_counters"] = dict(report["groups"].get(self.group, {}).get("raw", {}))
        return attributes


class SolixHistorySensor(SolixEntity, SensorEntity):
    """Read persisted estimates even offline, but require a healthy fresh sampler."""

    def __init__(self, coordinator, name, description: SensorEntityDescription) -> None:
        super().__init__(coordinator, name, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        history = self.snapshot.get("history", {})
        key = self.entity_description.key.removeprefix("history_")
        if key == "continuity":
            if not history:
                return None
            if history.get("last_seen_timestamp") is None:
                return "pending"
            return "gap" if history["gap_open"] else "continuous"
        if key in ("collection_start", "updated_at"):
            value = history.get(key)
            if value is None:
                return None
            try:
                return datetime.fromtimestamp(value, UTC)
            except (TypeError, OverflowError, OSError, ValueError):
                return None
        return history.get("lifetime_totals", {}).get(key)

    @property
    def extra_state_attributes(self) -> dict:
        attributes = {**super().extra_state_attributes, "estimated": True,
                      "source": "gateway_cached_ac_power_integral"}
        history = self.snapshot.get("history")
        if isinstance(history, dict):
            attributes.update({key: history[key] for key in (
                "collection_start", "updated_at", "last_seen_timestamp", "gap_open", "max_gap_seconds"
            ) if key in history})
            totals = history.get("lifetime_totals", {})
            attributes["gap_count"] = totals.get("gap_count")
            key = self.entity_description.key
            for channel in ("input", "output"):
                if key == f"history_ac_{channel}_energy_kwh_estimate":
                    attributes["coverage_seconds"] = totals.get(f"ac_{channel}_coverage_seconds")
        return attributes

    @property
    def available(self) -> bool:
        # Live station staleness is separate: a disconnected station can still
        # have valid persisted history with an open recording gap.
        return (self.coordinator.last_update_success and self.native_value is not None
                and history_available(self.snapshot.get("history"), self.snapshot, now=time.time()))
