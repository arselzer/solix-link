"""Synthetic surplus automation guards and parity with read-only proposals."""

from datetime import timedelta
from pathlib import Path

import pytest
import yaml

import test_charging_blueprint as harness
from solix_link.adaptive_policy import preview_adaptive_policy


@pytest.fixture
def simulation(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "blueprints/automation/solix_link/surplus_charging.yaml"
    blueprint = yaml.load(path.read_text(), Loader=harness.BlueprintLoader)
    monkeypatch.setattr(harness, "BLUEPRINT", blueprint)
    sim = harness.Simulation()
    sim.inputs.update(manual_override="input_select.override", export_sensor="sensor.export",
                      disaster="binary_sensor.disaster")
    sim.set("manual_override", "none")
    sim.set("export_sensor", "800")
    sim.set("disaster", "off")
    sim.members.add(sim.inputs["disaster"])
    sim.states[sim.inputs["disaster"]].attributes.update(solix_link_role="disaster_preparation_active",
                                                       solix_link_protocol="native_mqtt")
    sim.firmware = "1.1.4.9"
    sim.env.globals["device_attr"] = lambda _, key: sim.model if key == "model" else sim.firmware if key == "sw_version" else None
    sim.states[sim.inputs["export_sensor"]].attributes["unit_of_measurement"] = "W"
    return sim


@pytest.mark.parametrize("watts,export,battery,override", [
    (300, 800, 85, "none"), (300, 800, 85, "hold"), (300, 0, 85, "charge"),
    (500, 200, 85, "none"), (500, 250, 85, "none"), (500, -400, 85, "none"),
    (300, 0, 10, "none"), (1000, 1800, 85, "none"), (100, 500, 85, "none"),
])
@pytest.mark.parametrize("model", ["c1000_gen2", "c2000_gen2"])
def test_blueprint_matches_adaptive_preview_steps(simulation, watts, export, battery, override, model):
    sim = simulation
    sim.model = model
    sim.set("power", watts)
    sim.set("battery", battery)
    sim.set("manual_override", override)
    sim.set("export_sensor", export)
    now = sim.clock.timestamp()
    document = {"config": dict(kind="surplus", armed=True, command_latch=False,
        cooldown=sim.inputs["cooldown"], minimum_reserve=20, maximum_watts=sim.inputs["charging_watts"],
        idle_watts=sim.inputs["idle_watts"], target_export_w=sim.inputs["target_export_w"],
        deadband_w=sim.inputs["deadband_w"], maximum_step_w=sim.inputs["maximum_step_w"],
        export_start=sim.inputs["export_start"], export_stop=sim.inputs["export_stop"],
        export_max_age=sim.inputs["export_max_age"], manual_override=override),
        "state": dict(previous_decision="grid", last_changed_at=now-3600),
        "signals": {"export": dict(value=export, timestamp=now, unit="W", positive_means="export")}}
    status = dict(model=sim.model, protocol="native_mqtt", connected=True, available=True,
        last_seen_timestamp=now-5, metrics=dict(ac_input_connected=1, ac_output_enabled=1,
        ac_fast_charge_enabled=0, usage_mode="standard", active_tariff="none",
        ac_charging_power_limit_w=watts, battery_percentage=battery, min_charge_percentage=1,
        backup_reserve_percentage=10, max_charge_percentage=95))
    result = preview_adaptive_policy(status, document, now=now)
    sim.run()
    expected = [("set-backup-reserve" if item["setting"] == "backup_reserve_percentage" else "set-charge-power",
                 item["value"]) for item in result["proposed_settings"]]
    assert sim.writes == (expected if model == "c1000_gen2" else [])
    assert len([write for write in sim.writes if write[0] == "set-charge-power"]) <= 1


@pytest.mark.parametrize("key,value", [
    ("armed", "off"), ("command_latch", "on"), ("manual_override", "hold"),
    ("manual_override", "unknown"), ("mains", "off"), ("ac_enabled", "off"),
    ("fast", "on"), ("usage_mode", "time_of_use"), ("tariff", "peak"),
    ("disaster", "on"), ("disaster", "unknown"),
    ("power", "unavailable"), ("battery", "nan"), ("export_sensor", "nan"),
])
def test_guards_block_writes(simulation, key, value):
    simulation.set(key, value)
    simulation.run()
    assert not simulation.writes


@pytest.mark.parametrize("kind", ["telemetry_stale", "signal_stale", "wrong_units", "wrong_device",
                                 "wrong_role", "cooldown", "out_of_range", "original", "firmware"])
def test_freshness_ownership_and_model_guards(simulation, kind):
    sim = simulation
    if kind == "telemetry_stale":
        sim.set("telemetry", (sim.clock - timedelta(seconds=31)).isoformat())
    elif kind == "signal_stale":
        sim.states[sim.inputs["export_sensor"]].last_reported -= timedelta(seconds=31)
    elif kind == "wrong_units":
        sim.states[sim.inputs["export_sensor"]].attributes["unit_of_measurement"] = "kW"
    elif kind == "wrong_device":
        sim.members.remove(sim.inputs["power"])
    elif kind == "wrong_role":
        sim.states[sim.inputs["power"]].attributes["solix_link_role"] = "battery_percentage"
    elif kind == "cooldown":
        sim.states[sim.inputs["command_latch"]].last_changed = sim.clock
    elif kind == "out_of_range":
        sim.set("power", "1200")
    elif kind == "firmware":
        sim.firmware = "1.1.5.0"
    else:
        sim.model = "c1000"
    sim.run()
    assert not sim.writes


def test_failure_or_manual_intervention_keeps_latch_and_stops_followup(simulation):
    sim = simulation
    sim.fail_command = "set-backup-reserve"
    with pytest.raises(RuntimeError, match="unconfirmed"):
        sim.run()
    assert sim.writes == [("set-backup-reserve", 20)]
    assert sim.states(sim.inputs["command_latch"]) == "on"


@pytest.mark.parametrize("change", ["power", "manual_override", "signal", "station", "disaster"])
def test_rechecks_after_reserve_and_rejects_other_controller(simulation, change):
    sim = simulation
    def intervene():
        if change == "power":
            sim.set("power", 500)
        elif change == "manual_override":
            sim.set("manual_override", "hold")
        elif change == "signal":
            sim.states[sim.inputs["export_sensor"]].last_reported -= timedelta(seconds=31)
        elif change == "disaster":
            sim.set("disaster", "on")
        else:
            sim.set("usage_mode", "time_of_use")
    sim.after_reserve = intervene
    sim.run()
    assert sim.writes == [("set-backup-reserve", 20)]
    assert sim.states(sim.inputs["command_latch"]) == "on"


def test_blueprint_starts_disabled_and_has_no_output_or_tou_service(simulation):
    assert harness.BLUEPRINT["initial_state"] is False
    assert harness.BLUEPRINT["mode"] == "single"
    serialized = repr(harness.BLUEPRINT["actions"])
    assert "switch.turn" not in serialized and "set-tou" not in serialized and "return-grid" not in serialized
