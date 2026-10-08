"""Cached preconditions and bounded request history; no station access."""

from copy import deepcopy
import json

import pytest

from solix_link.command_coordination import (CommandCoordinator, CoordinationError,
    check_preconditions, make_preconditions, validate_preconditions)
from solix_link.commands import commands_for_transport
from solix_link.control_availability import control_availability
from solix_link.protocol import Model


def snapshot(**changes):
    return {"model": "c2000_gen2", "protocol": "native_mqtt", "connected": True,
        "available": True, "last_seen_timestamp": 1000, "control_enabled": True,
        "metrics": {"ac_charging_power_limit_w": 300, "max_charge_percentage": 100,
            "min_charge_percentage": 1, "backup_reserve_percentage": 10,
            "software_version": "2.1.6.4", "ac_input_connected": 1}} | changes


def ticket(coordinator, request_id="synthetic-request-01"):
    return {"request_id": request_id, "gateway_instance": coordinator.instance, "issued_at": coordinator.clock()}


def row(report, command):
    return next(item for item in report["commands"] if item["command"] == command)


def test_allowlists_and_model_transport_explanations():
    counts = {Model.C300: (3, 0, 0), Model.C1000: (8, 9, 9),
              Model.C1000_GEN2: (0, 4, 16), Model.C2000_GEN2: (0, 3, 6)}
    for model, expected in counts.items():
        assert tuple(len(commands_for_transport(model, transport))
                     for transport in ("legacy", "prime", "native_mqtt")) == expected
    supported = commands_for_transport(Model.C2000_GEN2, "native_mqtt")
    report = control_availability(snapshot(), supported, supported, gateway_enabled=True, now=1001)
    assert row(report, "set-charge-power")["ready"]
    assert row(report, "set-backup-reserve")["ready"]
    assert "missing_or_invalid_metrics" in row(report, "set-display-timeout")["reasons"]
    assert "transport_unsupported" not in row(report, "set-display-timeout")["reasons"]
    assert "model_unsupported" in row(report, "set-clock-brightness")["reasons"]
    assert report["preflight_only"] and report["backend_validation_required"]


@pytest.mark.parametrize("changes,advertised,permitted,enabled,busy,reason", [
    ({}, ["set-charge-power"], [], True, False, "token_scope_denied"),
    ({}, ["set-charge-power"], ["set-charge-power"], False, False, "gateway_controls_disabled"),
    ({"control_enabled": False}, [], ["set-charge-power"], True, False, "worker_controls_disabled"),
    ({}, [], ["set-charge-power"], True, False, "not_advertised"),
    ({"available": False}, ["set-charge-power"], ["set-charge-power"], True, False, "telemetry_unavailable"),
    ({"last_seen_timestamp": 970}, ["set-charge-power"], ["set-charge-power"], True, False, "telemetry_unavailable"),
    ({"metrics": {"ac_charging_power_limit_w": True}}, ["set-charge-power"], ["set-charge-power"], True, False, "missing_or_invalid_metrics"),
    ({}, ["set-charge-power"], ["set-charge-power"], True, True, "command_in_progress"),
])
def test_readiness_reasons(changes, advertised, permitted, enabled, busy, reason):
    item = row(control_availability(snapshot(**changes), advertised, permitted,
        gateway_enabled=enabled, busy=busy, now=1001), "set-charge-power")
    assert not item["ready"] and reason in item["reasons"]


def test_guarded_c1000_preferences_and_safe_report():
    metrics = {"software_version": "1.1.4.9", "ac_power_saving_mode_enabled": 0,
        "ac_output_enabled": 0, "ac_output_timeout_seconds": 0, "dc_output_timeout_seconds": 0,
        "clock_screen_enabled": 0, "clock_screen_transfer_status_raw": 0,
        "clock_screen_first_brightness_flag_raw": 0, "clock_screen_second_brightness_flag_raw": 1,
        "display_brightness": 2, "usage_mode": "standard", "active_tariff": "none"}
    station = snapshot(model="c1000_gen2", metrics=metrics)
    commands = commands_for_transport(Model.C1000_GEN2, "native_mqtt")
    def report():
        return control_availability(station, commands, commands, gateway_enabled=True, now=1001)
    assert row(report(), "set-ac-power-saving")["ready"]
    assert row(report(), "set-clock-brightness")["ready"]
    metrics.update(software_version="1.1.4.3", ac_output_enabled=1, ac_output_timeout_seconds=10,
                   usage_mode="time_of_use", clock_screen_enabled=1, owner_id="PRIVATE")
    result = report()
    assert "PRIVATE" not in json.dumps(result)
    assert {"firmware_unqualified", "output_must_be_off", "countdown_must_be_inactive"} <= set(row(result, "set-ac-power-saving")["reasons"])
    assert {"standard_mode_required", "clock_must_be_inactive"} <= set(row(result, "set-display-brightness")["reasons"])


def test_expected_settings_are_sanitized_detached_and_compare_fresh_values(monkeypatch):
    monkeypatch.setattr("solix_link.settings_export.time.time", lambda: 1001)
    current = snapshot()
    current["metrics"].update(owner_id="PRIVATE", raw_tlvs="PRIVATE", battery_percentage=50,
                              ac_output_timer_remaining_seconds=12)
    expected = make_preconditions(current)
    assert "PRIVATE" not in json.dumps(expected) and "battery_percentage" not in expected["metrics"]
    assert "ac_output_timer_remaining_seconds" not in expected["metrics"]
    validate_preconditions(expected)
    assert check_preconditions(expected, current)
    current["metrics"]["ac_charging_power_limit_w"] = 400
    assert expected["metrics"]["ac_charging_power_limit_w"] == 300
    assert not check_preconditions(expected, current)
    assert not check_preconditions(make_preconditions(current), current | {"available": False})
    assert check_preconditions(None, {})


@pytest.mark.parametrize("value", [None, [], {}, {"model": "c2000_gen2", "protocol": "native_mqtt", "metrics": {"owner_id": "PRIVATE"}},
    {"model": "c2000_gen2", "protocol": "native_mqtt", "metrics": {"ac_input_connected": True}},
    {"model": "c2000_gen2", "protocol": "native_mqtt", "metrics": {"ac_charging_power_limit_w": 0}},
    {"model": "c2000_gen2", "protocol": "native_mqtt", "metrics": {}, "extra": "PRIVATE"}])
def test_invalid_preconditions(value):
    with pytest.raises(ValueError, match="^Invalid command preconditions$"):
        validate_preconditions(value)


def test_plan_guard_requires_independent_fresh_plan_and_ignores_report_time(monkeypatch):
    monkeypatch.setattr("solix_link.settings_export.time.time", lambda: 1001)
    current = snapshot(tou_plan_readback={"schema_version": 1, "source": "status_d9",
        "enabled": False, "periods": [{"tariff": "peak", "start_hour": 0, "end_hour": 24}], "reported_at": 1000})
    expected = make_preconditions(current)
    assert "tou_plan_readback" in expected
    current["tou_plan_readback"]["reported_at"] = 1001
    assert check_preconditions(expected, current)
    current["tou_plan_readback"]["reported_at"] = 900
    assert not check_preconditions(expected, current)
    current["tou_plan_readback"]["reported_at"] = 1001
    current["tou_plan_readback"]["periods"][0]["tariff"] = "off_peak"
    assert not check_preconditions(expected, current)


def test_replay_does_not_reacquire_station_or_mutate_record():
    principal = object()
    coordinator = CommandCoordinator(clock=lambda: 1000)
    envelope = ticket(coordinator)
    payload = {"command": "set-charge-power", "watts": 300}
    key, replay = coordinator.begin(principal, "one", payload, envelope)
    assert replay is None and coordinator.context("one")["busy"]
    assert coordinator.result(principal, "one", envelope["request_id"])["state"] == "in_progress"
    with pytest.raises(CoordinationError, match="CommandInProgress") as error:
        coordinator.begin(principal, "one", payload, envelope)
    assert error.value.changed
    coordinator.finish("one", key, 504, {"error": "TimeoutError", "nested": {"unknown": True}})
    assert not coordinator.context("one")["busy"]
    _, replay = coordinator.begin(principal, "one", payload, envelope)
    assert replay[0] == 504
    replay[1]["nested"]["unknown"] = False
    assert coordinator.result(principal, "one", envelope["request_id"])["response"]["nested"]["unknown"]
    assert coordinator.result(object(), "one", envelope["request_id"]) is None
    with pytest.raises(CoordinationError, match="RequestIdConflict"):
        coordinator.begin(principal, "one", payload | {"watts": 400}, envelope)


def test_cache_full_busy_expiry_and_restart_fail_closed():
    clock = [1000]
    coordinator = CommandCoordinator(clock=lambda: clock[0], max_requests=1)
    principal = object()
    payload = {"command": "return-grid"}
    first = ticket(coordinator)
    key, _ = coordinator.begin(principal, "one", payload, first)
    with pytest.raises(CoordinationError, match="DeviceBusy"):
        coordinator.begin(principal, "one", payload, None)
    # A legacy request for a different station can proceed without a cache entry.
    other, _ = coordinator.begin(principal, "two", payload, None)
    coordinator.finish("two", other, 200, {})
    coordinator.finish("one", key, 200, {})
    with pytest.raises(CoordinationError, match="RequestCacheFull"):
        coordinator.begin(principal, "two", payload, ticket(coordinator, "synthetic-request-02"))
    assert coordinator.begin(principal, "one", payload, first)[1] == (200, {})
    with pytest.raises(CoordinationError, match="GatewayInstanceChanged"):
        CommandCoordinator(clock=lambda: clock[0]).begin(principal, "one", payload, first)
    clock[0] += 3601
    with pytest.raises(CoordinationError, match="RequestTicketExpired"):
        coordinator.begin(principal, "one", payload, first)
    assert coordinator.result(principal, "one", first["request_id"]) is None
    assert coordinator.begin(principal, "two", payload, ticket(coordinator, "synthetic-request-02"))[1] is None


@pytest.mark.parametrize("changes", [{"request_id": "short"}, {"issued_at": True}, {"issued_at": float("nan")},
    {"issued_at": 1006}, {"gateway_instance": None}, {"extra": "PRIVATE"}])
def test_invalid_ticket_never_acquires_slot(changes):
    coordinator = CommandCoordinator(clock=lambda: 1000)
    with pytest.raises(CoordinationError):
        coordinator.begin(object(), "one", {}, ticket(coordinator) | changes)
    assert not coordinator.busy and not coordinator.requests
