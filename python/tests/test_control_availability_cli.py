"""GET-only command explanations through a synthetic opener and CLI."""

import json

import pytest

from solix_link import cli
from solix_link.control_availability import control_availability
from solix_link.gateway_client import GatewayClient, GatewayReadError
from test_command_coordination import snapshot
from test_gateway_client import Opener


def report():
    return control_availability(snapshot(), ["set-charge-power"], ["set-charge-power"],
                                gateway_enabled=True, now=1001)


def test_read_only_client_quotes_name_copies_known_fields_and_hides_private_data():
    raw = report()
    raw.update(owner_id="PRIVATE", firmware_version="PRIVATE")
    raw["commands"][0]["password"] = "PRIVATE"
    opener = Opener(raw)
    result = GatewayClient("http://synthetic.test", opener=opener).control_availability("UPS / office")
    request, _ = opener.calls[0]
    assert request.get_method() == "GET" and request.data is None
    assert request.full_url == "http://synthetic.test/devices/UPS%20%2F%20office/control-availability"
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("mutate", [lambda raw: raw.update(model=[]), lambda raw: raw.update(commands={}),
    lambda raw: raw["commands"].append(raw["commands"][0]),
    lambda raw: raw["commands"][0].update(reasons=["PRIVATE"]),
    lambda raw: raw["commands"][0].update(missing_metrics=["owner_id"]),
    lambda raw: raw["commands"][0].update(ready=1)])
def test_bad_report_fails_with_fixed_message(mutate):
    raw = report()
    mutate(raw)
    with pytest.raises(GatewayReadError, match="^Gateway returned invalid cached control explanations$"):
        GatewayClient("http://synthetic.test", opener=Opener(raw)).control_availability("office")


def test_cli_does_not_load_pairing_profiles_or_connect_to_station(monkeypatch, capsys):
    opener = Opener(report())
    client = GatewayClient("http://synthetic.test", opener=opener)
    monkeypatch.setattr("solix_link.gateway_client.GatewayClient", lambda *args: client)
    def forbidden(*args, **kwargs):
        raise AssertionError("No configuration or device access")
    monkeypatch.setattr(cli, "load_config", forbidden)
    monkeypatch.setattr(cli, "SolixMonitor", forbidden)
    assert cli.main(["control-availability", "--gateway-url", "http://synthetic.test", "--name", "office"]) == 0
    assert json.loads(capsys.readouterr().out)["preflight_only"]
    assert len(opener.calls) == 1 and opener.calls[0][0].get_method() == "GET"
