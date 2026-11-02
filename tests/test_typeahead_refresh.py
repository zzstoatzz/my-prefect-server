import http.server
import json
import threading

import httpx
import mps.typeahead_refresh as refresh
import pytest

CURRENT = {
    "engine": "tantivy",
    "origin": "https://typeahead-tantivy-a.fly.dev",
    "build_id": "b1792908655-test",
    "format": refresh.TANTIVY_FORMAT,
    "admission_sha256": "a" * 64,
    "binary_sha256": "b" * 64,
    "retrieval_strategy": "global-merge",
}


def test_managed_route_rejects_other_apps_builds_and_native_backends():
    assert refresh.route_target({"version": 1, "current": CURRENT}) == CURRENT
    for origin in [
        "https://example.com",
        CURRENT["origin"] + "/",
        "http://typeahead-tantivy-a.fly.dev",
        "https://typeahead-startup-20261024.fly.dev",
        "https://typeahead-search-refresh.fly.dev",
    ]:
        with pytest.raises(ValueError):
            refresh.route_target({"version": 1, "current": {**CURRENT, "origin": origin}})
    with pytest.raises(ValueError):
        refresh.route_target({"version": 1, "current": {**CURRENT, "build_id": "latest"}})


def test_health_requires_known_fresh_lag_over_real_http():
    target = {**CURRENT, "source_watermark": 1792908655}
    health = {"ready": True, "sync_lag_s": -1, "engine": "tantivy", "build_id": target["build_id"]}
    index = {
        "engine": "tantivy",
        "ready": True,
        "snapshot_watermark": target["source_watermark"],
        **{key: target[key] for key in ("build_id", "format", "binary_sha256")},
        "retrieval_strategy": target["retrieval_strategy"],
    }
    headers = {
        "X-Typeahead-Engine": "tantivy",
        "X-Typeahead-Build": target["build_id"],
        "X-Typeahead-Format": target["format"],
        "X-Typeahead-Binary-Sha256": target["binary_sha256"],
        "X-Typeahead-Retrieval-Strategy": target["retrieval_strategy"],
    }

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            payload = json.dumps(health if self.path == "/health" else index).encode()
            self.send_response(200)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        service = refresh.Refresh.__new__(refresh.Refresh)
        service.http = httpx.Client(timeout=5)
        target["origin"] = f"http://127.0.0.1:{server.server_port}"
        try:
            for lag in [-1, 600]:
                health["sync_lag_s"] = lag
                with pytest.raises(RuntimeError, match="not fresh and ready"):
                    service.healthy(target)
                assert service.healthy(target, require_fresh=False)["health"]["sync_lag_s"] == lag
            for lag in [0, 599]:
                health["sync_lag_s"] = lag
                assert service.healthy(target)["health"]["sync_lag_s"] == lag
            index["snapshot_format"] = "FLATPX05"
            index["engine"] = None
            with pytest.raises(RuntimeError, match="identity mismatch"):
                service.healthy(target)
        finally:
            service.http.close()
            server.shutdown()
            thread.join()
