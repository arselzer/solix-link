"""Real HA freshness/flag entities with scoped platform doubles and synthetic data.

No HTTP requests or live HA/device operations occur. These exercise entity
discovery, value conversion and inherited availability, not HA's state machine.
"""

import asyncio
from datetime import datetime, UTC
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1] / "custom_components/solix_link"
NOW = 1700000000


class Description(SimpleNamespace):
    def __init__(self, **kwargs):
        super().__init__(**({"options": None, "native_unit_of_measurement": None,
            "state_class": None, "device_class": None,
            "entity_registry_enabled_default": True} | kwargs))


class CoordinatorEntity:
    @classmethod
    def __class_getitem__(cls, _item):
        return cls

    def __init__(self, coordinator, *, context):
        self.coordinator = coordinator
        self.context = context

    @property
    def available(self):
        return self.coordinator.last_update_success


@pytest.fixture
def platform(monkeypatch):
    def install(name, **values):
        module = ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
        return module

    install("homeassistant")
    install("homeassistant.components")
    install("homeassistant.components.sensor", SensorEntity=type("SensorEntity", (), {}),
        SensorEntityDescription=Description, SensorStateClass=SimpleNamespace(MEASUREMENT="measurement"),
        SensorDeviceClass=SimpleNamespace(BATTERY="battery", TEMPERATURE="temperature",
            POWER="power", ENUM="enum", TIMESTAMP="timestamp", ENERGY="energy"))
    install("homeassistant.components.binary_sensor", BinarySensorEntity=type("BinarySensorEntity", (), {}),
        BinarySensorEntityDescription=Description, BinarySensorDeviceClass=SimpleNamespace(POWER="power"))
    install("homeassistant.const", PERCENTAGE="%", EntityCategory=SimpleNamespace(DIAGNOSTIC="diagnostic"),
        UnitOfPower=SimpleNamespace(WATT="W"), UnitOfTemperature=SimpleNamespace(CELSIUS="°C"),
        UnitOfEnergy=SimpleNamespace(KILO_WATT_HOUR="kWh"), UnitOfTime=SimpleNamespace(SECONDS="s"))
    install("homeassistant.core", callback=lambda function: function)
    install("homeassistant.helpers")
    install("homeassistant.helpers.device_registry", DeviceInfo=dict)
    install("homeassistant.helpers.update_coordinator", CoordinatorEntity=CoordinatorEntity)
    package_name = "solix_freshness_contract"
    package = install(package_name)
    package.__path__ = [str(ROOT)]
    install(f"{package_name}.coordinator", SolixCoordinator=object, SolixConfigEntry=object)

    def load(name):
        qualified = f"{package_name}.{name}"
        spec = importlib.util.spec_from_file_location(qualified, ROOT / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, qualified, module)
        spec.loader.exec_module(module)
        return module

    api = load("api")
    api.time = SimpleNamespace(time=lambda: NOW)
    load("entity")
    sensor, binary = load("sensor"), load("binary_sensor")
    sensor.time = SimpleNamespace(time=lambda: NOW)
    return SimpleNamespace(api=api, sensor=sensor, binary=binary)


def snapshot(**changes):
    return {"name": "test_station", "model": "c2000_gen2", "protocol": "native_mqtt",
        "connected": True, "available": True, "last_seen_timestamp": NOW-1,
        "metrics": {}, "controls": []} | changes


def coordinator(data):
    listeners = []

    def listen(callback):
        listeners.append(callback)
        return lambda: listeners.remove(callback)

    async def forbidden_request():
        raise AssertionError("An entity must not request device data")

    return SimpleNamespace(data=data, endpoint_id="stable-endpoint", last_update_success=True,
        listeners=listeners, async_add_listener=listen,
        client=SimpleNamespace(token="", async_devices=forbidden_request))


def timestamp_sensor(platform, data):
    description = next(item for item in platform.sensor.DESCRIPTIONS if item.key == "last_seen_timestamp")
    return platform.sensor.SolixSensor(coordinator({"station": data}), "station", description)


def fast_flag(platform, data):
    description = next(item for item in platform.binary.DESCRIPTIONS if item.key == "ac_fast_charge_enabled")
    return platform.binary.SolixBinarySensor(coordinator({"station": data}), "station", description)


@pytest.mark.parametrize("value", [NOW-1, NOW-0.125, NOW+5])
def test_timestamp_uses_exact_top_level_station_time_in_utc(platform, value):
    sensor = timestamp_sensor(platform, snapshot(last_seen_timestamp=value,
        metrics={"last_seen_timestamp": NOW+99999}))
    assert sensor.native_value == datetime.fromtimestamp(value, UTC)
    assert sensor.native_value.tzinfo is UTC and sensor.native_value.timestamp() == value
    assert sensor.available
    assert sensor.entity_description.device_class == "timestamp"
    assert sensor.entity_description.native_unit_of_measurement is None
    assert sensor.entity_description.state_class is None
    assert sensor.entity_description.entity_registry_enabled_default is True


@pytest.mark.parametrize("value", [None, True, False, "1700000000", float("nan"), float("inf"),
    -float("inf"), NOW+5.001, 1e100, -1e100, 10**400])
def test_timestamp_rejects_invalid_and_future_values_without_throwing(platform, value):
    sensor = timestamp_sensor(platform, snapshot(last_seen_timestamp=value))
    assert sensor.native_value is None and sensor.available is False


@pytest.mark.parametrize("changes", [{"last_seen_timestamp": NOW-90.001},
    {"connected": False}, {"available": False}])
def test_timestamp_cached_data_is_unavailable_when_station_is_stale_or_offline(platform, changes):
    sensor = timestamp_sensor(platform, snapshot(**changes))
    assert sensor.native_value is not None
    assert not sensor.available


def test_timestamp_fails_closed_after_poll_failure(platform):
    sensor = timestamp_sensor(platform, snapshot())
    sensor.coordinator.last_update_success = False
    assert not sensor.available


def test_timestamp_discovery_all_models_is_stable_and_reads_no_gateway(platform):
    data = {model: snapshot(model=model) for model in ("c1000", "c1000_gen2", "c2000_gen2", "c300")}
    source = coordinator(data)
    unload = []
    entry = SimpleNamespace(runtime_data=source, async_on_unload=unload.append)
    entities = []
    asyncio.run(platform.sensor.async_setup_entry(None, entry, entities.extend))
    assert len(entities) == 4
    assert {entity.station_name for entity in entities} == set(data)
    assert all(entity.entity_description.key == "last_seen_timestamp" for entity in entities)
    identities = {entity.station_name: entity._attr_unique_id for entity in entities}
    for item in source.data.values():
        item["last_seen_timestamp"] = NOW-0.5
    source.listeners[0]()
    assert len(entities) == 4
    assert identities == {entity.station_name: entity._attr_unique_id for entity in entities}
    assert all(entity.native_value.timestamp() == NOW-0.5 for entity in entities)
    unload[0]()
    assert source.listeners == []


@pytest.mark.parametrize("raw,expected", [(0, False), (1, True), (None, None),
    (True, None), (False, None), (0.0, None), (1.0, None), ("1", None), (2, None), (-1, None)])
def test_fast_flag_reports_only_exact_decoded_integer_state(platform, raw, expected):
    sensor = fast_flag(platform, snapshot(metrics={"ac_fast_charge_enabled": raw}))
    assert sensor.is_on is expected
    assert sensor.available is (expected is not None)
    assert sensor.entity_description.device_class is None
    assert sensor.entity_description.entity_registry_enabled_default is True
    assert not hasattr(sensor, "async_turn_on") and not hasattr(sensor, "async_turn_off")


@pytest.mark.parametrize("model,protocol,expected", [("c2000_gen2", "native_mqtt", 1),
    ("c2000_gen2", "prime", 0), ("c2000_gen2", "legacy", 0),
    ("c1000_gen2", "native_mqtt", 0), ("c1000", "native_mqtt", 0), ("c300", "native_mqtt", 0)])
def test_fast_flag_discovery_is_native_c2000_only(platform, model, protocol, expected):
    source = coordinator({"station": snapshot(model=model, protocol=protocol,
        metrics={"ac_fast_charge_enabled": 0})})
    entry = SimpleNamespace(runtime_data=source, async_on_unload=lambda callback: None)
    entities = []
    asyncio.run(platform.binary.async_setup_entry(None, entry, entities.extend))
    assert len(entities) == expected
    if expected:
        assert entities[0].entity_description.key == "ac_fast_charge_enabled"
        source.listeners[0]()
        assert len(entities) == 1


@pytest.mark.parametrize("changes", [{"last_seen_timestamp": NOW-91}, {"connected": False}, {"available": False}])
def test_fast_flag_retains_stale_value_but_is_unavailable(platform, changes):
    sensor = fast_flag(platform, snapshot(metrics={"ac_fast_charge_enabled": 0}, **changes))
    assert sensor.is_on is False and not sensor.available


@pytest.mark.parametrize("changes", [{"model": "c1000_gen2"}, {"protocol": "prime"}])
def test_existing_fast_flag_becomes_unavailable_after_profile_change(platform, changes):
    sensor = fast_flag(platform, snapshot(metrics={"ac_fast_charge_enabled": 0}))
    assert sensor.available
    sensor.coordinator.data["station"].update(changes)
    assert not sensor.available


def test_new_entity_labels_icons_and_english_translations_match():
    strings = json.loads((ROOT/"strings.json").read_text())
    assert strings == json.loads((ROOT/"translations/en.json").read_text())
    icons = json.loads((ROOT/"icons.json").read_text())
    assert strings["entity"]["sensor"]["last_seen_timestamp"]["name"] == "Last telemetry"
    assert strings["entity"]["binary_sensor"]["ac_fast_charge_enabled"]["name"] == "Fast charging enabled"
    assert "last_seen_timestamp" in icons["entity"]["sensor"]
    assert "ac_fast_charge_enabled" in icons["entity"]["binary_sensor"]


def test_role_and_transport_attributes_preserve_existing_metadata(platform):
    sensor = timestamp_sensor(platform, snapshot())
    sensor._attr_extra_state_attributes = {"output_behavior": "Preserved warning",
        "solix_link_role": "forged", "solix_link_protocol": "forged"}
    attributes = sensor.extra_state_attributes
    assert attributes == {"output_behavior": "Preserved warning", "solix_link_role": "last_seen_timestamp",
                          "solix_link_protocol": "native_mqtt"}
    assert "serial" not in attributes and "account_id" not in attributes and "name" not in attributes
    sensor.coordinator.data["station"]["protocol"] = "prime"
    assert sensor.extra_state_attributes["solix_link_protocol"] == "prime"
    assert sensor.extra_state_attributes["solix_link_role"] == "last_seen_timestamp"


@pytest.mark.parametrize("protocol", [None, "unknown", "private identity", {"secret": "value"}])
def test_transport_attribute_drops_unrecognized_values(platform, protocol):
    sensor = timestamp_sensor(platform, snapshot(protocol=protocol))
    assert sensor.extra_state_attributes["solix_link_protocol"] == "unknown"
