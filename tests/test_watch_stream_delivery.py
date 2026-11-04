import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from prefect.blocks.system import Secret
from prefect.testing.utilities import prefect_test_harness
from prefect.variables import Variable

from flows import watch_stream as watcher
from tests.test_watch_stream import PROFILE, sample, verdict


async def test_real_http_delivery_retry_and_persisted_episode(monkeypatch):
    delivered = []
    seq = 1000000

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            now = time.time()
            if self.path == "/metrics":
                body = "\n".join(
                    [
                        f"stream_upstream_seq {seq}",
                        "process_start_time_seconds 1",
                        "jetstream_livestream_reconnects_total 0",
                        f"jetstream_livestream_last_seen_upstream_event_timestamp_seconds {now}",
                    ]
                )
            else:
                body = json.dumps(PROFILE if self.path.endswith("rate-profile") else verdict(now))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body.encode())

        def do_POST(self):
            assert self.headers["authorization"] == "Bearer local-test-token"
            delivered.append(json.loads(self.rfile.read(int(self.headers["content-length"]))))
            self.send_response(503 if len(delivered) == 1 else 200)
            self.end_headers()
            self.wfile.write(b"{}")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    for name in ("STREAM", "RELAY", "PHI_BASE"):
        monkeypatch.setattr(watcher, name, base)
    try:
        with prefect_test_harness():
            await Secret(value="local-test-token").asave("phi-control-token", overwrite=True)
            now = time.time()
            await Variable.aset(
                watcher.STATE,
                {
                    "sample": sample(now - 300, seq - 892 * 300),
                    "high_since": now - 600,
                },
                overwrite=True,
            )
            report = await watcher.watch_stream()
            assert report["status"] == "delivered"
            assert len(delivered) == 2
            assert delivered[0] == delivered[1]
            state = await Variable.aget(watcher.STATE)
            assert state["active"] == delivered[0]["event_id"]
            assert "pending" not in state
            await watcher.watch_stream()
            assert len(delivered) == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
