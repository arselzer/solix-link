"""Pure adaptive proposals against synthetic snapshots; no device calls."""
from copy import deepcopy
import json

import pytest

from solix_link.adaptive_policy import preview_adaptive_policy
from solix_link.charging_policy import ChargingPolicyRequestError
from test_charging_policy import NOW, station


def request(kind="surplus"):
    config = {"kind": kind, "armed": True, "command_latch": False, "cooldown": 180,
              "manual_override": "none", "minimum_reserve": 20,
              "idle_watts": 300, "maximum_watts": 1000}
    if kind == "surplus":
        config.update(export_start=600, export_stop=300, export_max_age=30,
                      target_export_w=100, deadband_w=50, maximum_step_w=200)
        signals = {"export": {"value": 900, "timestamp": NOW, "unit": "W", "positive_means": "export"}}
    else:
        config.update(charge_start=0.05, charge_stop=0.10, discharge_start=0.30,
                      discharge_stop=0.20, price_max_age=300, soc_resume_margin=5)
        signals = {"price": {"value": 0.40, "timestamp": NOW}}
    return {"config": config, "signals": signals,
            "state": {"previous_decision": "grid", "last_changed_at": NOW-300}}


def snapshot(model="c1000_gen2"):
    s = station(model)
    s["metrics"].update(backup_reserve_percentage=20, tou_schedule_slot_count=0)
    return s


@pytest.mark.parametrize("model", ["c1000_gen2", "c2000_gen2"])
def test_surplus_is_a_bounded_saved_limit_step_without_input_mutation(model):
    s, r = snapshot(model), request()
    original = deepcopy((s, r))
    result = preview_adaptive_policy(s, r, now=NOW)
    assert result["proposed_settings"] == [{"setting": "ac_charging_power_limit_w", "value": 500}]
    assert result["commands_sent"] == 0 and result["dry_run"]
    assert not result["executor_available"] and not result["electrical_behavior_verified"]
    assert result["proposed_plan"] is None
    assert (s, r) == original
    assert "PRIVATE" not in json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("export,wanted", [(300, 700), (299, 300), (-1000, 300), (900, 700)])
def test_surplus_threshold_quantization_and_downward_step(export, wanted):
    s, r = snapshot(), request()
    s["metrics"]["ac_charging_power_limit_w"] = 500
    r["signals"]["export"]["value"] = export
    result = preview_adaptive_policy(s, r, now=NOW)
    assert result["desired_settings"]["ac_charging_power_limit_w"] == wanted


def test_deadband_holds_limit_and_does_not_advance_unchanged_state():
    s, r = snapshot(), request()
    s["metrics"]["ac_charging_power_limit_w"] = 500
    r["config"].update(target_export_w=600, deadband_w=50)
    r["signals"]["export"]["value"] = 600
    r["state"]["previous_decision"] = "charge"
    result = preview_adaptive_policy(s, r, now=NOW)
    assert result["proposed_settings"] == []
    assert result["next_preview_state"] == r["state"]


@pytest.mark.parametrize("price,previous,expected", [
    (0.05, "grid", "charge"), (0.06, "grid", "grid"),
    (0.10, "charge", "charge"), (0.11, "charge", "grid"),
    (0.30, "grid", "battery"), (0.29, "grid", "grid"),
    (0.20, "battery", "battery"), (0.19, "battery", "grid"),
])
def test_price_hysteresis_from_explicit_preview_state(price, previous, expected):
    r = request("price_tou")
    r["signals"]["price"]["value"] = price
    r["state"]["previous_decision"] = previous
    result = preview_adaptive_policy(snapshot(), r, now=NOW)
    assert result["decision"] == expected
    if expected == "grid":
        assert result["proposed_plan"] is None
    else:
        assert result["proposed_plan"] == {"enabled": True, "periods": [
            {"tariff": "peak" if expected == "battery" else "off_peak", "start_hour": 0, "end_hour": 24}]}


@pytest.mark.parametrize("soc,previous,decision", [(19, "grid", "emergency"), (20, "battery", "grid"),
                                                   (21, "battery", "grid"), (25, "grid", "grid"),
                                                   (26, "grid", "battery")])
def test_actual_reserve_and_resume_margin_protect_battery_proposal(soc, previous, decision):
    s, r = snapshot(), request("price_tou")
    s["metrics"]["battery_percentage"] = soc
    r["state"]["previous_decision"] = previous
    assert preview_adaptive_policy(s, r, now=NOW)["decision"] == decision


def test_reserve_is_only_raised_and_caps_preserved():
    s, r = snapshot(), request("price_tou")
    s["metrics"].update(backup_reserve_percentage=30, max_charge_percentage=85)
    result = preview_adaptive_policy(s, r, now=NOW)
    assert result["desired_settings"]["backup_reserve_percentage"] == 30
    assert result["desired_settings"]["max_charge_percentage"] == 85
    r["config"]["minimum_reserve"] = 40
    assert preview_adaptive_policy(s, r, now=NOW)["proposed_settings"] == [
        {"setting": "backup_reserve_percentage", "value": 40}]


@pytest.mark.parametrize("kind", ["surplus", "price_tou"])
@pytest.mark.parametrize("failure", ["stale", "future", "disarmed", "latched", "cooldown", "original", "fast", "disconnected", "hold"])
def test_guards_never_offer_settings_or_plans(kind, failure):
    s, r = snapshot(), request(kind)
    if failure == "stale": s["last_seen_timestamp"] = NOW-31
    elif failure == "future": s["last_seen_timestamp"] = NOW+6
    elif failure == "disarmed": r["config"]["armed"] = False
    elif failure == "latched": r["config"]["command_latch"] = True
    elif failure == "cooldown": r["state"]["last_changed_at"] = NOW-179
    elif failure == "original": s["model"] = "c1000"
    elif failure == "fast": s["metrics"]["ac_fast_charge_enabled"] = 1
    elif failure == "disconnected": s["connected"] = False
    elif failure == "hold": r["config"]["manual_override"] = "hold"
    result = preview_adaptive_policy(s, r, now=NOW)
    assert not result["eligible"] and result["decision"] == "blocked"
    assert result["proposed_settings"] == [] and result["proposed_plan"] is None
    assert result["next_preview_state"] is None


@pytest.mark.parametrize("count", [None, True, 1, 6])
def test_saved_tariff_plan_is_never_overwritten(count):
    s = snapshot()
    s["metrics"]["tou_schedule_slot_count"] = count
    result = preview_adaptive_policy(s, request("price_tou"), now=NOW)
    assert result["reasons"] == ["empty_saved_tariff_plan_required"]
    assert not result["proposed_plan"]


def test_active_tou_is_blocked_instead_of_inventing_plan_ownership():
    s = snapshot()
    s["metrics"].update(usage_mode="time_of_use", active_tariff="peak")
    result = preview_adaptive_policy(s, request("price_tou"), now=NOW)
    assert not result["eligible"] and "standard_mode_required" in result["reasons"]


def test_manual_battery_cannot_override_reserve_or_stale_external_signal():
    s, r = snapshot(), request("price_tou")
    r["config"]["manual_override"] = "battery"
    s["metrics"]["battery_percentage"] = 20
    assert preview_adaptive_policy(s, r, now=NOW)["decision"] == "grid"
    r["signals"]["price"]["timestamp"] = NOW-301
    assert not preview_adaptive_policy(s, r, now=NOW)["eligible"]


@pytest.mark.parametrize("key,value", [("maximum_step_w", 99), ("deadband_w", True), ("target_export_w", -1),
                                       ("manual_override", "PRIVATE"), ("armed", 1)])
def test_invalid_configuration_uses_fixed_error(key, value):
    r = request()
    r["config"][key] = value
    with pytest.raises(ChargingPolicyRequestError, match="^InvalidChargingPreview$"):
        preview_adaptive_policy(snapshot(), r, now=NOW)


def test_outside_policy_range_blocks_instead_of_emitting_outside_maximum():
    s, r = snapshot(), request()
    s["metrics"]["ac_charging_power_limit_w"] = 1200
    assert not preview_adaptive_policy(s, r, now=NOW)["eligible"]


@pytest.mark.parametrize("override,price,expected", [("charge", 1, "charge"), ("grid", 0, "grid"),
                                                    ("battery", 0, "battery")])
def test_manual_price_override_has_no_executor_and_keeps_output_state(override, price, expected):
    s, r = snapshot(), request("price_tou")
    r["config"]["manual_override"] = override
    r["signals"]["price"]["value"] = price
    result = preview_adaptive_policy(s, r, now=NOW)
    assert result["decision"] == expected and not result["executor_available"]
    assert s["metrics"]["ac_output_enabled"] == 1
    assert not any(item["setting"] in ("ac_output_enabled", "dc_output_enabled") for item in result["proposed_settings"])


@pytest.mark.parametrize("value", [False, None, float("nan"), float("inf"), 10**400, "PRIVATE"])
def test_bad_signal_never_becomes_a_power_or_tariff_proposal(value):
    r = request()
    r["signals"]["export"]["value"] = value
    with pytest.raises(ChargingPolicyRequestError, match="^InvalidChargingPreview$"):
        preview_adaptive_policy(snapshot(), r, now=NOW)


def test_offline_cli_uses_new_contract_only_with_explicit_flag(tmp_path, monkeypatch, capsys):
    from solix_link.cli import main
    s, r = snapshot(), request()
    snapshot_file, request_file = tmp_path / "snapshot.json", tmp_path / "policy.json"
    snapshot_file.write_text(json.dumps(s))
    request_file.write_text(json.dumps(r))
    monkeypatch.setattr("solix_link.adaptive_policy.time.time", lambda: NOW)
    monkeypatch.setattr("solix_link.cli.load_config", lambda *_: pytest.fail("Preview read a station profile"))
    monkeypatch.setattr("solix_link.cli.asyncio.run", lambda *_: pytest.fail("Preview started a device coroutine"))
    assert main(["charging-preview", "--adaptive", "--snapshot-file", str(snapshot_file),
                 "--request-file", str(request_file)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["desired_settings"]["ac_charging_power_limit_w"] == 500
    assert result["commands_sent"] == 0
    assert main(["charging-preview", "--snapshot-file", str(snapshot_file),
                 "--request-file", str(request_file)]) != 0
    assert "InvalidChargingPreview" in capsys.readouterr().err
