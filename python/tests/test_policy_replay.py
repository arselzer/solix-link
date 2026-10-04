"""Offline replay uses supplied snapshots and previous previews, never writes."""
from copy import deepcopy
import json
from xml.etree import ElementTree

import pytest

from solix_link.policy_replay import PolicyReplayError, adaptive_timeline_svg, replay_adaptive_timeline
from test_adaptive_policy import request, snapshot


def timeline(kind="surplus"):
    body = request(kind)
    frames = []
    for offset in (0, 60, 180, 360):
        station = snapshot()
        station["last_seen_timestamp"] = 1000 + offset
        signals = deepcopy(body["signals"])
        for signal in signals.values():
            signal["timestamp"] = 1000 + offset
        frames.append({"timestamp": 1000 + offset, "snapshot": station, "signals": signals})
    return {"schema_version": 1, "request": body, "frames": frames}


def test_replay_threads_preview_cooldown_without_fabricating_changed_watts_or_soc():
    document = timeline()
    before = deepcopy(document)
    result = replay_adaptive_timeline(document)
    previews = [row["preview"] for row in result["frames"]]
    assert [preview["decision"] for preview in previews] == ["charge", "blocked", "charge", "charge"]
    assert previews[1]["reasons"] == ["cooldown_active"]
    assert [preview["current_settings"]["ac_charging_power_limit_w"] for preview in previews] == [300] * 4
    assert previews[0]["proposed_settings"] == previews[2]["proposed_settings"]
    assert document == before and result["commands_sent"] == 0
    assert result["snapshots_modified"] is False
    assert result["state_basis"] == "previous_preview_only"
    assert "PRIVATE" not in json.dumps(result)


def test_replay_exposes_gaps_manual_hold_and_reserve_protection():
    document = timeline("price_tou")
    document["frames"][1]["manual_override"] = "hold"
    document["frames"][2]["snapshot"]["last_seen_timestamp"] -= 31
    document["frames"][3]["snapshot"]["metrics"]["battery_percentage"] = 20
    previews = [row["preview"] for row in replay_adaptive_timeline(document)["frames"]]
    assert previews[0]["decision"] == "battery"
    assert previews[1]["decision"] == "blocked"
    assert "telemetry_stale" in previews[2]["reasons"]
    assert previews[3]["decision"] == "grid" and previews[3]["proposed_plan"] is None


def test_supplied_active_plan_is_blocked_even_after_previous_candidate():
    document = timeline("price_tou")
    document["frames"][2]["snapshot"]["metrics"].update(usage_mode="time_of_use", active_tariff="peak", tou_schedule_slot_count=1)
    result = replay_adaptive_timeline(document)
    assert result["frames"][2]["preview"]["decision"] == "blocked"
    assert result["frames"][2]["preview"]["proposed_plan"] is None


def test_invalid_signal_object_is_rejected_without_running_copy_hooks():
    class InvalidSignal:
        def __deepcopy__(self, _memo):
            pytest.fail("Invalid signal executed a copy hook")
    document = timeline()
    document["frames"][0]["signals"] = InvalidSignal()
    with pytest.raises(PolicyReplayError, match="^InvalidPolicyReplay$"):
        replay_adaptive_timeline(document)


@pytest.mark.parametrize("mutation", ["duplicate_clock", "reverse_clock", "extra_field", "too_many", "bad_override", "bad_request", "bool_version", "nan"])
def test_malformed_timeline_has_fixed_error(mutation):
    document = timeline()
    if mutation == "duplicate_clock": document["frames"][1]["timestamp"] = 1000
    elif mutation == "reverse_clock": document["frames"][1]["timestamp"] = 999
    elif mutation == "extra_field": document["frames"][0]["execute"] = True
    elif mutation == "too_many": document["frames"] *= 65
    elif mutation == "bad_override": document["frames"][0]["manual_override"] = "PRIVATE"
    elif mutation == "bad_request": document["request"]["config"]["armed"] = 1
    elif mutation == "bool_version": document["schema_version"] = True
    else: document["frames"][0]["timestamp"] = float("nan")
    with pytest.raises(PolicyReplayError, match="^InvalidPolicyReplay$"):
        replay_adaptive_timeline(document)


def test_svg_is_standalone_parseable_and_contains_only_proposal_metadata():
    rendered = adaptive_timeline_svg(timeline("price_tou"))
    root = ElementTree.fromstring(rendered)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert "commands sent: 0" in rendered and "TOU peak" in rendered
    assert "PRIVATE" not in rendered and "<script" not in rendered
    assert "cooldown_active" in rendered


@pytest.mark.parametrize("format", ["json", "svg"])
def test_replay_cli_reads_only_saved_timeline(tmp_path, monkeypatch, capsys, format):
    from solix_link.cli import main
    path = tmp_path / "timeline.json"
    path.write_text(json.dumps(timeline()))
    monkeypatch.setattr("solix_link.cli.load_config", lambda *_: pytest.fail("Read station profile"))
    monkeypatch.setattr("solix_link.cli.asyncio.run", lambda *_: pytest.fail("Started device coroutine"))
    assert main(["policy-replay", "--timeline-file", str(path), "--format", format]) == 0
    output = capsys.readouterr().out
    if format == "json":
        assert json.loads(output)["commands_sent"] == 0
    else:
        assert ElementTree.fromstring(output).tag.endswith("svg")
