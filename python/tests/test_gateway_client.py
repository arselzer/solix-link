from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from threading import Thread
from urllib.error import HTTPError

import pytest

from solix_link.gateway_client import GatewayClient, GatewayReadError, MAX_RESPONSE_BYTES


def device(name="Office · C1000 Gen 2"):
    return {"name": name, "model": "c1000_gen2", "protocol": "native_mqtt", "connected": True,
            "available": True, "last_seen_timestamp": 1000, "error": None,
            "controls": ["set-ac-output", "set-charge-power"], "metrics": {"battery_percentage": 50}}


def saved_history(name="Office · C1000 Gen 2"):
    totals = {"ac_input_energy_kwh_estimate": 0.1, "ac_output_energy_kwh_estimate": 0.05,
              "ac_input_coverage_seconds": 10, "ac_output_coverage_seconds": 10, "gap_count": 1}
    return {"name": name, "model": "c1000_gen2", "protocol": "native_mqtt", "estimated": True,
            "collection_start": 900, "totals": totals, "lifetime_totals": totals.copy(),
            "points": [{"timestamp": 1000, "battery_percentage": 50, "ac_input_power_w": 300,
                        "ac_output_power_w": 100, "gap": True, "max_source_interval_seconds": None}],
            "window": {"since": 900, "until": 1000}, "truncated": False,
            "limits": {"retention_days": 7, "max_gap_seconds": 15, "max_power_w": 10000,
                       "max_samples": 500000, "max_points": 2000, "max_query_seconds": 31 * 86400}}


class Response(io.BytesIO):
    pass


class Opener:
    def __init__(self, body):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        return Response(self.body)


def test_history_get_is_bounded_quoted_and_drops_unknown_private_fields(tmp_path):
    token = tmp_path / "token"
    token.write_text("SYNTHETIC-BEARER\n")
    token.chmod(0o600)
    body = saved_history()
    body.update(client_id="PRIVATE-PAIRING-ID", raw_tlvs="PRIVATE-CAPTURE")
    body["points"][0]["serial_number"] = "PRIVATE-SERIAL"
    opener = Opener(body)
    client = GatewayClient("http://127.0.0.1:8765", token, opener=opener)
    result = client.history(body["name"], since=900, until=1000, limit=200)
    request, timeout = opener.calls[0]
    assert request.get_method() == "GET" and request.data is None
    assert "/Office%20%C2%B7%20C1000%20Gen%202/history?" in request.full_url
    assert request.get_header("Authorization") == "Bearer SYNTHETIC-BEARER"
    assert timeout == 5 and "PRIVATE" not in repr(result)
    assert result["limits"]["max_gap_seconds"] == 15


def test_device_snapshot_disables_all_advertised_control_routes():
    opener = Opener(device())
    client = GatewayClient("https://gateway.invalid/solix", opener=opener)
    result = client.snapshot(device()["name"])
    assert result["control_enabled"] is False and "controls" not in result
    assert opener.calls[0][0].full_url.startswith("https://gateway.invalid/solix/devices/")
    with pytest.raises(GatewayReadError):
        client.snapshot("Office")  # Exact name, not a prefix or alias.


@pytest.mark.parametrize("url", ["file:///tmp/db", "http://user:secret@localhost", "http://localhost?q=secret",
                                  "http://localhost/#fragment", "http://localhost/../api", "http://localhost:0",
                                  "http://localhost\n", "", None, 1])
def test_invalid_urls_fail_without_requests_or_echoing_input(url):
    opener = Opener({})
    with pytest.raises(GatewayReadError) as error:
        GatewayClient(url, opener=opener)
    assert "secret" not in str(error.value) and opener.calls == []


@pytest.mark.parametrize("kind", ["public", "symlink", "hardlink", "empty", "multiline", "oversize", "fifo"])
def test_token_requires_private_bounded_regular_file(tmp_path, kind):
    path = tmp_path / "token"
    if kind == "fifo":
        os.mkfifo(path, 0o600)
    else:
        path.write_text("SYNTHETIC-TOKEN")
        path.chmod(0o600)
        if kind == "public":
            path.chmod(0o644)
        elif kind == "symlink":
            link = tmp_path / "link"
            link.symlink_to(path)
            path = link
        elif kind == "hardlink":
            os.link(path, tmp_path / "other")
        elif kind == "empty":
            path.write_text("")
        elif kind == "multiline":
            path.write_text("SYNTHETIC\nTOKEN")
        elif kind == "oversize":
            path.write_text("X" * 4097)
    with pytest.raises(GatewayReadError) as error:
        GatewayClient("http://localhost", path)
    assert "SYNTHETIC" not in str(error.value)


@pytest.mark.parametrize("payload", [b'{"devices": [], "devices": []}', b'{"x": NaN}',
                                      b'{"x": 1e10000}', b'[]', b'{', b'X' * (MAX_RESPONSE_BYTES + 1)])
def test_response_json_duplicates_nonfinite_and_size_are_rejected(payload):
    client = GatewayClient("http://localhost", opener=Opener(payload))
    with pytest.raises(GatewayReadError):
        client.devices()


@pytest.mark.parametrize("change", [{"model": []}, {"protocol": {}}, {"connected": 1},
                                   {"last_seen_timestamp": float("nan")}, {"name": "\x00"}, {"metrics": []}])
def test_invalid_station_snapshots_produce_fixed_errors(change):
    body = device()
    body.update(change)
    client = GatewayClient("http://localhost", opener=Opener(body))
    with pytest.raises(GatewayReadError):
        client.snapshot(device()["name"])


@pytest.mark.parametrize("bounds", [{"since": float("nan")}, {"until": float("inf")},
                                    {"since": True}, {"since": -1}, {"since": 1000, "until": 999},
                                    {"since": 0, "until": 32 * 86400}, {"limit": 2001}, {"limit": True}])
def test_invalid_history_requests_never_send_http(bounds):
    opener = Opener(saved_history())
    client = GatewayClient("http://localhost", opener=opener)
    with pytest.raises(GatewayReadError):
        client.history(device()["name"], **bounds)
    assert opener.calls == []


@pytest.mark.parametrize("field,value", [("battery_percentage", 101), ("ac_input_power_w", -1),
                                        ("ac_output_power_w", True), ("timestamp", "1000"),
                                        ("gap", 1), ("max_source_interval_seconds", 16)])
def test_invalid_history_values_do_not_reach_charts(field, value):
    body = saved_history()
    body["points"][0][field] = value
    with pytest.raises(GatewayReadError):
        GatewayClient("http://localhost", opener=Opener(body)).history(body["name"])


@pytest.mark.parametrize("status", [301, 302, 307, 308, 401, 500])
def test_http_errors_never_echo_response_body_or_credentials(status):
    class Failing:
        def open(self, request, timeout):
            raise HTTPError(request.full_url, status, "PRIVATE-ERROR", {}, io.BytesIO(b"PRIVATE-CREDENTIAL"))
    with pytest.raises(GatewayReadError) as error:
        GatewayClient("http://localhost", opener=Failing()).devices()
    assert "PRIVATE" not in str(error.value) and "localhost" not in str(error.value)


def test_real_loopback_get_and_same_host_redirect_refusal(monkeypatch):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.command))
            if self.path.startswith("/devices/"):
                self.send_response(302)
                self.send_header("Location", "/redirect-target")
                self.end_headers()
            else:
                content = json.dumps({"devices": [device()]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    try:
        client = GatewayClient(f"http://127.0.0.1:{server.server_port}")
        assert client.devices()[0]["name"] == device()["name"]
        with pytest.raises(GatewayReadError, match="redirects"):
            client.snapshot(device()["name"])
        assert len(requests) == 2 and all(method == "GET" for _, method in requests)
        assert all(path != "/redirect-target" for path, _ in requests)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
