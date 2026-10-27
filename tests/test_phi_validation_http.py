import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from flows import phi_trigger


@pytest.fixture
def bot(monkeypatch):
    seen = []
    config = {"healthy": True, "token": "test-token", "slots": ["curation"]}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args):
            pass

        def respond(self, status, body):
            self.send_response(status)
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def do_GET(self):
            assert self.path == "/health"
            self.respond(200 if config["healthy"] else 503, {"status": "healthy"})

        def do_POST(self):
            seen.append(self.path)
            if self.headers.get("authorization") != f"Bearer {config['token']}":
                self.respond(401, {"error": "unauthorized"})
            else:
                self.respond(404, {"error": "unknown slot", "slots": config["slots"]})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(phi_trigger, "PHI_BASE", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(phi_trigger, "configure_logfire", lambda *args: None)

    async def secret(name):
        assert name == "phi-control-token"
        return "test-token"

    monkeypatch.setattr(phi_trigger, "secret", secret)
    yield config, seen
    server.shutdown()
    server.server_close()
    thread.join()


async def test_authenticated_validation_starts_no_pass(bot):
    _, seen = bot
    result = await phi_trigger.phi_trigger.fn("curation", validate_only=True)
    assert result.is_completed()
    assert result.name == "Validated"
    assert seen == ["/api/control/trigger/__prefect_validate__"]


async def test_validation_rejects_wrong_token(bot):
    config, _ = bot
    config["token"] = "different-token"
    with pytest.raises(httpx.HTTPStatusError, match="401"):
        await phi_trigger.phi_trigger.fn("curation", validate_only=True)


async def test_validation_rejects_unknown_slot(bot):
    with pytest.raises(ValueError, match="does not advertise"):
        await phi_trigger.phi_trigger.fn("missing-slot", validate_only=True)


async def test_validation_rejects_unhealthy_bot_before_probe(bot):
    config, seen = bot
    config["healthy"] = False
    with pytest.raises(httpx.HTTPStatusError, match="503"):
        await phi_trigger.phi_trigger.fn("curation", validate_only=True)
    assert seen == []
