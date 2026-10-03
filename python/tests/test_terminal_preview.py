from copy import deepcopy
import json

import pytest

from solix_link.charging_policy import ChargingPolicyRequestError
from solix_link.terminal_preview import manual_preview
from test_charging_policy import request, station


def write_request(tmp_path, body=None):
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request() if body is None else body))
    return path


def test_manual_observation_is_explicit_and_does_not_modify_file_or_snapshot(tmp_path):
    path = write_request(tmp_path)
    raw, snapshot = path.read_bytes(), station()
    before = deepcopy(snapshot)
    result = manual_preview(snapshot, path, price_value="0.5", price_age="0", now=1000)
    assert result["decision"] == "idle" and result["commands_sent"] == 0
    assert path.read_bytes() == raw and snapshot == before
    assert manual_preview(snapshot, path, now=1000)["decision"] == "opportunity"


@pytest.mark.parametrize("fields", [
    {"price_value": "1"}, {"price_age": "0"}, {"price_value": "nan", "price_age": "0"},
    {"price_value": "1e20", "price_age": "0"}, {"price_value": "1001", "price_age": "0"},
    {"price_value": "0", "price_age": "-1"}, {"price_value": "0", "price_age": "86401"},
    {"price_value": None}, {"price_value": "1" * 65, "price_age": "0"},
    {"export_value": "1000", "export_age": "0"},
])
def test_invalid_manual_inputs_fail_without_echoing_or_writing(tmp_path, fields):
    path = write_request(tmp_path)
    before = path.read_bytes()
    with pytest.raises(ChargingPolicyRequestError, match="^InvalidChargingPreview$"):
        manual_preview(station(), path, now=1000, **fields)
    assert path.read_bytes() == before


def test_manual_export_units_sign_and_staleness_are_preserved(tmp_path):
    path = write_request(tmp_path, request("export"))
    result = manual_preview(station(), path, export_value="-400", export_age="0", now=1000)
    assert result["decision"] == "idle"
    result = manual_preview(station(), path, export_value="1000", export_age="31", now=1000)
    assert result["decision"] == "blocked" and "export_signal_stale" in result["reasons"]


def test_stale_snapshot_and_local_clock_skew_return_blocked_preview(tmp_path):
    path = write_request(tmp_path)
    result = manual_preview(station(), path, now=1040)
    assert result["decision"] == "blocked" and "telemetry_stale" in result["reasons"]
    result = manual_preview(station(), path, now=990)
    assert result["decision"] == "blocked" and "telemetry_future" in result["reasons"]


def test_invalid_or_oversize_file_cannot_be_fixed_by_manual_override(tmp_path):
    path = write_request(tmp_path)
    path.write_text('{"config": {}, "signals": {}}')
    with pytest.raises(ChargingPolicyRequestError):
        manual_preview(station(), path, price_value="0", price_age="0", now=1000)
    path.write_text(" " * 4097)
    with pytest.raises(ChargingPolicyRequestError):
        manual_preview(station(), path, now=1000)
