import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from prefect.logging import disable_run_logger

from flows.mcp_atlas import crawl


@pytest.fixture
def endpoints():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/failed/"):
                self.send_response(503)
                self.end_headers()
                return
            did = "did:plc:first" if self.path.startswith("/first/") else "did:plc:second"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"repos": [{"did": did}]}).encode())

        def do_POST(self):
            requests.append(
                (
                    self.headers.get("Authorization"),
                    json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                )
            )
            self.send_response(503 if self.path == "/failed" else 200)
            self.end_headers()

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_relay_union_and_partial_failure(endpoints, monkeypatch):
    base, _ = endpoints
    monkeypatch.setattr(crawl, "RELAYS", [base + "/first", base + "/second"])
    with disable_run_logger():
        assert crawl.enumerate_dids.fn() == ["did:plc:first", "did:plc:second"]
        monkeypatch.setattr(crawl, "RELAYS", [base + "/first", base + "/failed"])
        assert crawl.enumerate_dids.fn() == ["did:plc:first"]


def test_total_relay_failure_is_not_an_empty_success(endpoints, monkeypatch):
    base, _ = endpoints
    monkeypatch.setattr(crawl, "RELAYS", [base + "/failed"])
    with disable_run_logger(), pytest.raises(RuntimeError, match="every relay failed"):
        crawl.enumerate_dids.fn()


def test_publication_auth_body_and_failure(endpoints, monkeypatch):
    base, requests = endpoints
    monkeypatch.setenv("MCP_ATLAS_INGEST_TOKEN", "synthetic-test-token")
    monkeypatch.setattr(crawl, "ATLAS_ENDPOINT", base + "/atlas")
    atlas = {"generatedAt": "2026-10-26T00:00:00Z", "servers": []}
    crawl.publish_atlas.fn(atlas)
    assert requests == [("Bearer synthetic-test-token", atlas)]
    monkeypatch.setattr(crawl, "ATLAS_ENDPOINT", base + "/failed")
    with pytest.raises(httpx.HTTPStatusError):
        crawl.publish_atlas.fn(atlas)
