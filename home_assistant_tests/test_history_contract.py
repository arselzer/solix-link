"""Optional history API, coordinator and entities; synthetic data only."""

import asyncio
from copy import deepcopy
from datetime import datetime, UTC
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import aiohttp
from aiohttp import web
import pytest

from test_freshness_entities import NOW, coordinator, platform, snapshot
from test_recovery_contract import Entry, integration

ROOT = Path(__file__).resolve().parents[1] / "custom_components/solix_link"
SPEC = importlib.util.spec_from_file_location("solix_history_contract", ROOT / "history.py")
history = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(history)


def station(name="station", **changes):
    return {"name": name, "model": "c2000_gen2", "protocol": "native_mqtt",
        "collection_start": NOW-7200, "last_seen_timestamp": NOW-1,
        "gap_open": False, "lifetime_totals": {
            "ac_input_energy_kwh_estimate": 1.25, "ac_output_energy_kwh_estimate": 0.8,
            "ac_input_coverage_seconds": 3600, "ac_output_coverage_seconds": 3000,
            "gap_count": 2}} | changes


def summary(*stations, **changes):
    return {"schema_version": 1, "enabled": True, "estimated": True, "read_only": True,
        "recording": True, "updated_at": NOW,
        "limits": {"max_power_w": 10000, "max_gap_seconds": 15},
        "stations": list(stations) if stations else [station()]} | changes


def attached(**changes):
    data = history.parse_history_summary(summary(station(**changes)), now=NOW)
    return data["stations"]["station"] | {
        "updated_at": data["updated_at"], "max_gap_seconds": data["max_gap_seconds"]}


def test_parser_excludes_private_fields_without_altering_inputs():
    raw = summary(station())
    raw.update(account_id="private", database_path="private", arbitrary="private")
    raw["stations"][0].update(serial_number="private", owner_id="private", pairing_id="private")
    raw["stations"][0]["lifetime_totals"]["password"] = "private"
    before = deepcopy(raw)
    parsed = history.parse_history_summary(raw, now=NOW)
    assert "private" not in json.dumps(parsed)
    assert parsed["stations"]["station"]["lifetime_totals"]["gap_count"] == 2
    assert raw == before


@pytest.mark.parametrize("changes", [
    {"schema_version": True}, {"schema_version": 2}, {"enabled": 1},
    {"estimated": False}, {"read_only": 1}, {"recording": False},
    {"updated_at": True}, {"updated_at": float("nan")}, {"updated_at": NOW-91},
    {"updated_at": NOW+5.001}, {"updated_at": 10**400},
    {"limits": {"max_power_w": 10001, "max_gap_seconds": 15}},
    {"limits": {"max_power_w": 10000, "max_gap_seconds": True}},
    {"stations": {}}, {"stations": [station(str(index)) for index in range(33)]},
])
def test_bad_envelope_fails_with_fixed_error(changes):
    with pytest.raises(history.HistoryValidationError, match="^Invalid gateway history summary$"):
        history.parse_history_summary(summary(**changes), now=NOW)


@pytest.mark.parametrize("key", ["schema_version", "enabled", "estimated", "read_only", "recording", "updated_at", "limits", "stations"])
def test_missing_envelope_fields_fail_closed(key):
    raw = summary()
    del raw[key]
    with pytest.raises(history.HistoryValidationError):
        history.parse_history_summary(raw, now=NOW)


def test_disabled_history_has_no_entities_or_cached_values():
    raw = {"schema_version": 1, "enabled": False, "estimated": True, "read_only": True,
           "stations": [station()], "secret": "private"}
    assert history.parse_history_summary(raw, now=NOW) == {"enabled": False, "stations": {}}


@pytest.mark.parametrize("changes", [
    {"name": "\x00private"}, {"name": " "}, {"name": "x"*65}, {"model": []},
    {"model": "unsupported"}, {"protocol": "cloud"}, {"gap_open": 0},
    {"collection_start": True}, {"collection_start": NOW},
    {"last_seen_timestamp": NOW+6}, {"last_seen_timestamp": float("inf")},
    {"lifetime_totals": {}}, {"lifetime_totals": None},
])
def test_bad_station_does_not_discard_healthy_fleet(changes):
    bad = station(**({"name": "broken"} | changes))
    assert list(history.parse_history_summary(summary(bad, station("healthy")), now=NOW)["stations"]) == ["healthy"]


@pytest.mark.parametrize("value", [True, -1, "0", float("nan"), float("inf"), 10**400, None])
@pytest.mark.parametrize("key", history.TOTAL_KEYS)
def test_totals_reject_nonfinite_negative_bool_or_missing_values(key, value):
    bad = station("bad")
    bad["lifetime_totals"][key] = value
    assert list(history.parse_history_summary(summary(bad, station("good")), now=NOW)["stations"]) == ["good"]


@pytest.mark.parametrize("key,value", [
    ("gap_count", 1.0), ("gap_count", 2**53),
    ("ac_input_coverage_seconds", 7200), ("ac_output_coverage_seconds", 7200),
    ("ac_input_energy_kwh_estimate", 11), ("ac_output_energy_kwh_estimate", 10),
])
def test_total_physical_bounds(key, value):
    bad = station()
    bad["lifetime_totals"][key] = value
    assert history.parse_history_summary(summary(bad), now=NOW)["stations"] == {}


def test_duplicate_names_drop_both_not_healthy_peer():
    assert list(history.parse_history_summary(summary(station(), station(), station("other")), now=NOW)["stations"]) == ["other"]


def test_pending_unknown_epoch_and_zero_coverage_are_not_invented():
    totals = dict.fromkeys(history.TOTAL_KEYS, 0)
    pending = station(collection_start=None, last_seen_timestamp=None, lifetime_totals=totals, gap_open=True)
    assert history.parse_history_summary(summary(pending, updated_at=None), now=NOW)["stations"]["station"]["collection_start"] is None
    assert history.parse_history_summary(summary(station(collection_start=None)), now=NOW)["stations"]["station"]["collection_start"] is None
    pending["lifetime_totals"]["ac_input_energy_kwh_estimate"] = 1e-10
    assert history.parse_history_summary(summary(pending), now=NOW)["stations"] == {}


def test_store_reopen_before_first_sampler_poll_keeps_totals_but_is_unavailable(platform):
    parsed = history.parse_history_summary(summary(updated_at=None), now=NOW)
    data = parsed["stations"]["station"] | {"updated_at": None, "max_gap_seconds": 15}
    entity = sensor(platform, snapshot(history=data))
    assert entity.native_value == 1.25 and not entity.available


@pytest.mark.parametrize("key", history.TOTAL_KEYS)
def test_same_epoch_counter_regression_rejected_and_explicit_epoch_reset_allowed(key):
    previous, current = attached(), attached()
    current["lifetime_totals"][key] -= 0.1
    assert not history.history_nonregressing(previous, current)
    current["collection_start"] += 1
    assert history.history_nonregressing(previous, current)


@pytest.mark.parametrize("changes", [{"updated_at": NOW-1}, {"last_seen_timestamp": None},
    {"last_seen_timestamp": NOW-2}, {"protocol": "prime"}, {"model": "c1000_gen2"}])
def test_same_epoch_order_and_source_changes_fail_closed(changes):
    assert not history.history_nonregressing(attached(), attached() | changes)


def sensor(platform, data, key="history_ac_input_energy_kwh_estimate"):
    description = next(item for item in platform.sensor.HISTORY_DESCRIPTIONS if item.key == key)
    return platform.sensor.SolixHistorySensor(coordinator({"station": data}), "station", description)


def test_history_entities_are_disabled_estimates_without_energy_statistics(platform):
    for description in platform.sensor.HISTORY_DESCRIPTIONS:
        assert description.entity_registry_enabled_default is False
        assert description.state_class is None
        assert description.entity_category == "diagnostic"
    entity = sensor(platform, snapshot(history=attached()))
    assert entity.available and entity.native_value == 1.25
    attrs = entity.extra_state_attributes
    assert attrs["estimated"] is True and attrs["coverage_seconds"] == 3600
    assert attrs["gap_count"] == 2 and attrs["solix_link_role"] == entity.entity_description.key
    assert entity._attr_unique_id.endswith("_history_ac_input_energy_kwh_estimate")
    assert "station" not in entity._attr_unique_id


@pytest.mark.parametrize("gap,expected", [(False, "continuous"), (True, "gap")])
def test_offline_station_retains_totals_and_gap_status(platform, gap, expected):
    data = snapshot(connected=False, available=False, last_seen_timestamp=NOW-1000,
                    history=attached(gap_open=gap))
    assert sensor(platform, data).available
    assert sensor(platform, data, "history_continuity").native_value == expected
    assert sensor(platform, data, "history_continuity").available


@pytest.mark.parametrize("changes", [{"updated_at": NOW-91}, {"updated_at": NOW+6},
    {"model": "c1000"}, {"protocol": "prime"}, {"updated_at": None}])
def test_history_freshness_and_source_match_are_independent_gates(platform, changes):
    assert not sensor(platform, snapshot(history=attached() | changes)).available


def test_history_timestamp_pending_and_poll_failures(platform):
    entity = sensor(platform, snapshot(history=attached()), "history_collection_start")
    assert entity.native_value == datetime.fromtimestamp(NOW-7200, UTC)
    entity.coordinator.last_update_success = False
    assert not entity.available
    entity.coordinator.last_update_success = True
    entity.coordinator.data["station"].pop("history")
    assert entity.native_value is None and not entity.available
    data = snapshot(history=attached(collection_start=None, last_seen_timestamp=None,
        lifetime_totals=dict.fromkeys(history.TOTAL_KEYS, 0), gap_open=True))
    assert sensor(platform, data, "history_continuity").native_value == "pending"
    assert not sensor(platform, data, "history_collection_start").available


def test_dynamic_history_discovery_adds_once_and_has_no_requests(platform):
    source = coordinator({"station": snapshot()})
    entities, unload = [], []
    entry = SimpleNamespace(runtime_data=source, async_on_unload=unload.append)
    asyncio.run(platform.sensor.async_setup_entry(None, entry, entities.extend))
    assert not any(isinstance(item, platform.sensor.SolixHistorySensor) for item in entities)
    source.data["station"]["history"] = attached()
    source.listeners[0]()
    source.listeners[0]()
    derived = [item for item in entities if isinstance(item, platform.sensor.SolixHistorySensor)]
    assert len(derived) == len(platform.sensor.HISTORY_DESCRIPTIONS)
    source.data["station"].pop("history")
    assert all(not item.available for item in derived)


def make_coordinator(integration, monkeypatch, state):
    calls = []
    monkeypatch.setattr(integration.coordinator, "time", SimpleNamespace(
        monotonic=lambda: state.get("clock", 0)))
    parser = integration.coordinator.parse_history_summary
    monkeypatch.setattr(integration.coordinator, "parse_history_summary", lambda raw: parser(raw, now=NOW))

    async def devices():
        calls.append("devices")
        return deepcopy(state.get("devices", {"station": snapshot()}))

    async def history_summary():
        calls.append("history")
        if isinstance(state["history"], Exception):
            raise state["history"]
        return deepcopy(state["history"])

    async def forbidden_command(*args):
        raise AssertionError("History must never send a command")

    client = SimpleNamespace(url="http://gateway.test", token="secret", async_devices=devices,
        async_history_summary=history_summary, async_command=forbidden_command)
    return integration.coordinator.SolixCoordinator(None, Entry("entry", client.url), client), calls


def test_coordinator_one_throttled_fleet_summary_and_no_commands(integration, monkeypatch):
    state = {"history": summary(station(), station("peer")), "devices": {
        "station": snapshot(), "peer": snapshot(name="peer")}}
    source, calls = make_coordinator(integration, monkeypatch, state)
    first = asyncio.run(source._async_update_data())
    assert all("history" in item for item in first.values())
    state["clock"] = 29
    assert asyncio.run(source._async_update_data()) == first
    assert calls == ["devices", "history", "devices"]
    state["clock"] = 30
    asyncio.run(source._async_update_data())
    assert calls[-2:] == ["devices", "history"]


@pytest.mark.parametrize("disabled", [None, {"schema_version": 1, "enabled": False, "estimated": True, "read_only": True}])
def test_absent_or_disabled_endpoint_preserves_old_contract_and_reprobes(integration, monkeypatch, disabled):
    state = {"history": disabled}
    source, calls = make_coordinator(integration, monkeypatch, state)
    assert asyncio.run(source._async_update_data()) == {"station": snapshot()}
    state.update(clock=299, history=summary())
    assert "history" not in asyncio.run(source._async_update_data())["station"]
    state["clock"] = 300
    assert "history" in asyncio.run(source._async_update_data())["station"]
    assert calls.count("history") == 2


def test_history_errors_do_not_break_live_and_do_not_retain_old_values(integration, monkeypatch):
    state = {"history": summary()}
    source, calls = make_coordinator(integration, monkeypatch, state)
    good = asyncio.run(source._async_update_data())
    assert "history" in good["station"]
    state.update(clock=30, history=integration.api.GatewayError("private"))
    assert asyncio.run(source._async_update_data()) == {"station": snapshot()}
    assert "history" not in source._attach_history(good)["station"]
    state.update(clock=60, history=summary())
    assert "history" in asyncio.run(source._async_update_data())["station"]
    assert calls.count("history") == 3


def test_optional_history_auth_failure_still_requests_reauthentication(integration, monkeypatch):
    state = {"history": integration.api.GatewayAuthError("private")}
    source, _ = make_coordinator(integration, monkeypatch, state)
    with pytest.raises(Exception, match="^Gateway authentication failed$"):
        asyncio.run(source._async_update_data())


def test_partial_fleet_invalid_station_and_model_mismatch_are_isolated(integration, monkeypatch):
    state = {"history": summary(station(), station("peer", model="c1000")), "devices": {
        "station": snapshot(), "peer": snapshot(name="peer"), "new": snapshot(name="new")}}
    source, _ = make_coordinator(integration, monkeypatch, state)
    data = asyncio.run(source._async_update_data())
    assert "history" in data["station"]
    assert "history" not in data["peer"] and "history" not in data["new"]
    state.update(clock=30, history=summary(station("peer")))
    data = asyncio.run(source._async_update_data())
    assert "history" not in data["station"] and "history" in data["peer"]


def test_restart_gap_counter_regression_and_epoch_reset(integration, monkeypatch):
    state = {"history": summary()}
    source, _ = make_coordinator(integration, monkeypatch, state)
    asyncio.run(source._async_update_data())
    restarted = station(gap_open=True)
    restarted["lifetime_totals"]["gap_count"] = 3
    state.update(clock=30, history=summary(restarted))
    data = asyncio.run(source._async_update_data())
    assert data["station"]["history"]["gap_open"] is True
    assert data["station"]["history"]["lifetime_totals"]["gap_count"] == 3
    state.update(clock=60, history=summary())
    assert "history" not in asyncio.run(source._async_update_data())["station"]
    reset = station(collection_start=NOW-7000)
    state.update(clock=90, history=summary(reset))
    assert "history" in asyncio.run(source._async_update_data())["station"]


async def with_server(api, callback):
    state = {"status": 200, "body": summary(), "reads": []}

    async def read(request):
        state["reads"].append((request.method, request.path, request.headers.get("Authorization")))
        if state.get("chunks"):
            response = web.StreamResponse(status=state["status"], headers={"Content-Type": "application/json"})
            await response.prepare(request)
            for chunk in state["chunks"]:
                await response.write(chunk)
                await asyncio.sleep(0)
            await response.write_eof()
            return response
        return web.json_response(state["body"], status=state["status"])

    app = web.Application()
    app.router.add_get("/history/summary", read)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with aiohttp.ClientSession() as session:
            client = api.GatewayClient(session, f"http://127.0.0.1:{port}", "test-token")
            await callback(client, state)
    finally:
        await runner.cleanup()


def test_history_http_one_authenticated_get_and_chunked_json(integration):
    async def scenario(client, state):
        assert await client.async_history_summary() == state["body"]
        assert state["reads"] == [("GET", "/history/summary", "Bearer test-token")]
        encoded = json.dumps(state["body"]).encode()
        state["chunks"] = [encoded[:15], encoded[15:50], encoded[50:]]
        assert await client.async_history_summary() == state["body"]
    asyncio.run(with_server(integration.api, scenario))


@pytest.mark.parametrize("status,error", [(404, None), (401, "auth"), (403, "auth"), (503, "read"), (302, "read")])
def test_optional_endpoint_statuses_and_no_redirect_or_retry(integration, status, error):
    async def scenario(client, state):
        state["status"] = status
        if error is None:
            assert await client.async_history_summary() is None
        else:
            expected = integration.api.GatewayAuthError if error == "auth" else integration.api.GatewayError
            with pytest.raises(expected):
                await client.async_history_summary()
        assert len(state["reads"]) == 1
    asyncio.run(with_server(integration.api, scenario))


def test_summary_body_is_bounded_and_invalid_json_is_fixed_error(integration):
    async def scenario(client, state):
        state["body"] = {"private": "x"*65537}
        with pytest.raises(integration.api.GatewayError, match="too large"):
            await client.async_history_summary()
        state["chunks"] = [b'{"secret": invalid']
        with pytest.raises(integration.api.GatewayError, match="^Unable to read the gateway$"):
            await client.async_history_summary()
        assert len(state["reads"]) == 2
    asyncio.run(with_server(integration.api, scenario))


@pytest.mark.parametrize("body", [b"null", b"[]", b"true", b"["*3000+b"0"+b"]"*3000])
def test_malformed_json_shape_and_excessive_nesting_do_not_escape_optional_errors(integration, body):
    async def scenario(client, state):
        state["chunks"] = [body]
        with pytest.raises(integration.api.GatewayError, match="^Invalid gateway history summary$"):
            await client.async_history_summary()
    asyncio.run(with_server(integration.api, scenario))


def test_history_failure_and_reauth_do_not_bypass_subsequent_auth_checks(integration, monkeypatch):
    state = {"history": integration.api.GatewayAuthError("private")}
    source, calls = make_coordinator(integration, monkeypatch, state)
    for _ in range(2):
        with pytest.raises(Exception, match="^Gateway authentication failed$"):
            asyncio.run(source._async_update_data())
    assert calls == ["devices", "history", "devices", "history"]


def test_three_station_fleet_is_supported_independently(integration, monkeypatch):
    variants = [("original", "c1000", "prime"), ("gen2", "c1000_gen2", "native_mqtt"),
                ("large", "c2000_gen2", "native_mqtt")]
    stations = [station(name, model=model, protocol=protocol) for name, model, protocol in variants]
    state = {"history": summary(*stations), "devices": {
        name: snapshot(name=name, model=model, protocol=protocol) for name, model, protocol in variants}}
    source, _ = make_coordinator(integration, monkeypatch, state)
    result = asyncio.run(source._async_update_data())
    assert set(result) == {"original", "gen2", "large"}
    assert all(value["history"]["lifetime_totals"]["ac_input_energy_kwh_estimate"] == 1.25
               for value in result.values())


def test_summary_request_deadline_does_not_change_control_deadlines(integration):
    class Session:
        def request(self, *args, **kwargs):
            assert args[0] == "GET" and args[1].endswith("/history/summary")
            assert kwargs["timeout"].total == 3 and kwargs["timeout"].sock_connect == 3
            assert kwargs["allow_redirects"] is False
            raise asyncio.TimeoutError()
    client = integration.api.GatewayClient(Session(), "http://gateway.test")
    with pytest.raises(integration.api.GatewayError, match="^Unable to read the gateway$"):
        asyncio.run(client.async_history_summary())
    assert integration.api.request_deadline("POST", {"command": "return-grid", "timeout": 30}) == 175


def test_history_and_live_poll_share_command_serialization(integration, monkeypatch):
    async def scenario():
        source, calls = make_coordinator(integration, monkeypatch, {"history": summary()})
        entered, release = asyncio.Event(), asyncio.Event()
        original = source.client.async_history_summary

        async def blocked_history():
            entered.set()
            await release.wait()
            return await original()

        async def command(name, payload):
            calls.append("command")
            return snapshot()

        source.client.async_history_summary = blocked_history
        source.client.async_command = command
        polling = asyncio.create_task(source._async_update_data())
        await entered.wait()
        writing = asyncio.create_task(source.async_command("station", {}))
        await asyncio.sleep(0)
        assert calls == ["devices"]
        release.set()
        await asyncio.gather(polling, writing)
        assert calls == ["devices", "history", "command"]
        assert "history" in source.data["station"]
    asyncio.run(scenario())


def test_history_translations_are_complete_and_match():
    strings = json.loads((ROOT / "strings.json").read_text())
    translated = json.loads((ROOT / "translations/en.json").read_text())
    assert strings == translated
    for key in ("history_ac_input_energy_kwh_estimate", "history_ac_output_energy_kwh_estimate",
                "history_ac_input_coverage_seconds", "history_ac_output_coverage_seconds",
                "history_gap_count", "history_collection_start", "history_updated_at", "history_continuity"):
        assert key in strings["entity"]["sensor"]
