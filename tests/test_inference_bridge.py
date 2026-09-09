import json
import socket
import tempfile
import threading
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from mps.inference_bridge import inference_bridge
from mps.inference_grants import InferenceGrants


@pytest.fixture
def bridge_dir():
    with tempfile.TemporaryDirectory(prefix="phi-bridge-", dir="/tmp") as directory:
        yield Path(directory)


@pytest.fixture
def upstream():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((dict(self.headers), body))
            if self.path == "/redirect":
                self.send_response(307)
                self.send_header("Location", "/must-not-follow")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b'data: {"ok":true}\n\ndata: [DONE]\n\n')

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def request(path, route="/v1/chat/completions", model="openai/gpt-5.6-luna"):
    connection = HTTPConnection("localhost")
    connection.sock = socket.socket(socket.AF_UNIX)
    connection.sock.connect(str(path))
    try:
        connection.request(
            "POST",
            route,
            json.dumps({"model": model, "messages": [], "max_tokens": 99999}),
            {"Authorization": "fake-agent-auth", "X-Agent-Header": "must-not-forward"},
        )
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_streams_response_but_owns_authentication_and_limits(bridge_dir, upstream):
    url, calls = upstream
    path = bridge_dir / "bridge.sock"
    with inference_bridge(
        path, upstream=url, authorization="fake-trusted-auth", agent_uid=None
    ) as counts:
        status, body = request(path)
        assert status == 200
        assert body.endswith(b"data: [DONE]\n\n")
        headers, payload = calls[0]
        assert headers["Authorization"] == "fake-trusted-auth"
        assert "X-Agent-Header" not in headers
        assert payload["max_tokens"] == 8192
        assert counts == {"requests": 1, "succeeded": 1}
    assert not path.exists()


@pytest.mark.parametrize(
    "route,model,expected",
    [
        ("/admin", "openai/gpt-5.6-luna", 404),
        ("/v1/chat/completions?url=elsewhere", "openai/gpt-5.6-luna", 404),
        ("/v1/chat/completions", "another-model", 400),
    ],
)
def test_rejects_requests_outside_inference_contract(bridge_dir, upstream, route, model, expected):
    url, calls = upstream
    path = bridge_dir / "bridge.sock"
    with inference_bridge(path, upstream=url, agent_uid=None):
        assert request(path, route, model)[0] == expected
    assert not calls


def test_attempt_limit_is_enforced(bridge_dir, upstream):
    url, calls = upstream
    path = bridge_dir / "bridge.sock"
    with inference_bridge(path, upstream=url, max_requests=1, agent_uid=None):
        assert request(path)[0] == 200
        assert request(path)[0] == 429
    assert len(calls) == 1


def test_redirect_is_not_followed(bridge_dir, upstream):
    url, calls = upstream
    path = bridge_dir / "bridge.sock"
    with inference_bridge(path, upstream=url + "/redirect", agent_uid=None):
        assert request(path)[0] == 502
    assert len(calls) == 1


def test_tcp_endpoint_authenticates_each_request(bridge_dir, upstream):
    url, calls = upstream
    grants = InferenceGrants(bridge_dir / "grants.sqlite")
    token = grants.issue("flow-run:attempt-0", model="openai/gpt-5.6-luna", request_limit=1)
    with inference_bridge(None, upstream=url, listen=("127.0.0.1", 0), grants=grants) as status:
        for credential, expected in [(None, 401), ("invalid", 403), (token, 200), (token, 403)]:
            connection = HTTPConnection("127.0.0.1", status["port"])
            headers = {"Authorization": f"Bearer {credential}"} if credential else {}
            connection.request(
                "POST",
                "/v1/chat/completions",
                json.dumps({"model": "openai/gpt-5.6-luna", "messages": []}),
                headers,
            )
            response = connection.getresponse()
            assert response.status == expected
            response.read()
            connection.close()
    assert len(calls) == 1


def test_tcp_listener_cannot_be_unauthenticated():
    with (
        pytest.raises(ValueError, match="durable run grants"),
        inference_bridge(None, upstream="http://localhost", listen=("127.0.0.1", 0)),
    ):
        raise AssertionError("Unauthenticated listener started")
