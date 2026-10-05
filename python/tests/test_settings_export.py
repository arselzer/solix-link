"""Preference export is cached, sanitized, partial and never a restore route."""

import asyncio
import json

from http_helpers import api_client
from solix_link.cli import main
from solix_link.server import create_app
from solix_link.settings_export import export_settings
from test_plan_readback import plan
from test_server_readonly_features import ReadOnlyGateway


def snapshot():
    return dict(name="PRIVATE", owner_id="PRIVATE", model="c1000_gen2", protocol="native_mqtt",
        connected=True, available=True, last_seen_timestamp=1000, tou_plan_readback=plan(),
        metrics=dict(ac_charging_power_limit_w=300, max_charge_percentage=95, min_charge_percentage=1,
            backup_reserve_percentage=20, device_timeout_minutes=0, ac_fast_charge_enabled=0,
            port_memory_enabled=1, serial_number="PRIVATE", ac_output_enabled=1, battery_percentage=90))


def test_export_omits_identity_runtime_and_invalid_settings_and_keeps_limits_explicit():
    value = snapshot()
    result = export_settings(value, now=1001)
    assert "PRIVATE" not in json.dumps(result)
    assert result["snapshot_fresh"] and result["tou_plan_fresh"]
    assert not result["complete"] and not result["restore_supported"]
    assert not result["field_freshness_verified"]
    assert "ac_output_enabled" not in result["settings"]
    assert result["settings"]["device_timeout_minutes"] == 0
    value["metrics"].update(ac_charging_power_limit_w=True, max_charge_percentage=85.5)
    value["tou_plan_readback"]["periods"][0]["end_hour"] = 24
    assert result["tou_plan_readback"]["periods"][0]["end_hour"] == 6
    result = export_settings(value, now=1031)
    assert not result["snapshot_fresh"] and not result["tou_plan_fresh"]
    assert "ac_charging_power_limit_w" in result["missing_or_invalid_fields"]


def test_unknown_or_original_model_cannot_inherit_gen2_plan():
    assert export_settings({"model": []})["model"] is None
    original = export_settings({**snapshot(), "model": "c1000"}, now=1001)
    assert original["tou_plan_readback"] is None
    assert "max_charge_percentage" not in original["settings"]


def test_only_bounded_firmware_versions_are_exported():
    value = snapshot()
    for version in ("1.7.1", "1.1.4.9", "2.1.6.4"):
        value["metrics"]["software_version"] = version
        assert export_settings(value)["firmware_version"] == version
    for version in ("PRIVATE", "1.1.1/token", "1"*1000, 1, []):
        value["metrics"]["software_version"] = version
        assert export_settings(value)["firmware_version"] is None


def test_original_light_mode_four_does_not_expand_c300_values():
    value = {**snapshot(), "model": "c1000", "metrics": {"light_mode": 4}}
    assert export_settings(value)["settings"]["light_mode"] == 4
    assert "light_mode" not in export_settings({**value, "model": "c300"})["settings"]


def test_http_export_auth_unknown_device_and_no_commands():
    gateway = ReadOnlyGateway()
    gateway.current = snapshot()
    gateway.current["name"] = "ups"
    async def run():
        async with api_client(create_app(gateway, token="token")) as client:
            path = "/devices/ups/settings-export"
            assert (await client.get(path)).status_code == 401
            headers = {"Authorization": "Bearer token"}
            response = await client.get(path, headers=headers)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            assert "PRIVATE" not in response.text
            assert response.json()["complete"] is False
            assert (await client.head(path, headers=headers)).status_code == 200
            assert (await client.get("/devices/missing/settings-export", headers=headers)).status_code == 404
            assert (await client.post(path, headers=headers, json={})).status_code == 405
            assert (await client.get("/devices/ups", headers=headers)).json()["tou_plan_readback"] == plan()
        assert gateway.calls == []
    asyncio.run(run())


def test_offline_cli_never_loads_config_or_connects(tmp_path, capsys, monkeypatch):
    from solix_link import cli
    def forbidden(*args, **kwargs):
        raise AssertionError("No config or device access")
    monkeypatch.setattr(cli, "load_config", forbidden)
    monkeypatch.setattr(cli, "SolixMonitor", forbidden)
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(snapshot()))
    assert main(["settings-export", "--snapshot-file", str(path)]) == 0
    value = capsys.readouterr().out
    assert "PRIVATE" not in value and json.loads(value)["restore_supported"] is False
