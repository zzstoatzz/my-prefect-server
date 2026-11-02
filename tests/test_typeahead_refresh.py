import http.server
import json
import threading

import httpx
import mps.typeahead_refresh as refresh
import pytest


def test_managed_route_rejects_other_apps_and_builds():
    current = {"origin": refresh.origin(refresh.SLOTS[0]), "build_id": "b1792908655-test"}
    assert refresh.route_target({"version": 1, "current": current}) == current
    for origin in [
        "https://example.com",
        current["origin"] + "/",
        "http://typeahead-startup-20261024.fly.dev",
    ]:
        with pytest.raises(ValueError):
            refresh.route_target({"version": 1, "current": {**current, "origin": origin}})
    with pytest.raises(ValueError):
        refresh.origin("typeahead-search-compact")


def test_health_requires_known_fresh_lag_over_real_http():
    target = {"build_id": "b1792908655-test", "source_watermark": 1792908655}
    health = {"ready": True, "sync_lag_s": -1}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = health if self.path == "/health" else {**target, "snapshot_format": "FLATPX05"}
            payload = json.dumps(body).encode()
            self.send_response(200)
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
        finally:
            service.http.close()
            server.shutdown()
            thread.join()
