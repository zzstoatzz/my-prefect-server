"""Linux integration check: transfer loopback data and freeze only a disposable unit."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

spec = importlib.util.spec_from_file_location("watchdog", sys.argv[1])
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(4 * 1024 * 1024))
        self.end_headers()
        try:
            for _ in range(4096):
                self.wfile.write(b"x" * 1024)
                self.wfile.flush()
                time.sleep(0.01)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_):
        pass


unit = f"bandwidth-watchdog-test-{os.getpid()}.service"
server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
config = {
    "alert_bytes_15m": 32768,
    "host_alert_bytes_15m": 10**12,
    "pause_bytes_1h": 65536,
    "monthly_observed_budget_bytes": 10**12,
    "cooldown_seconds": 3600,
    "pause_units": [unit],
}
state = {}
watchdog.advance(state, *watchdog.collect(include_local=True), "test", time.time(), config)
code = (
    f"import urllib.request; urllib.request.urlopen('http://127.0.0.1:{server.server_port}').read()"
)
try:
    subprocess.run(
        ["systemd-run", "--user", f"--unit={unit}", sys.executable, "-c", code], check=True
    )
    time.sleep(2)
    actions = watchdog.advance(
        state, *watchdog.collect(include_local=True), "test", time.time(), config
    )
    assert [a["unit"] for a in actions] == [unit], actions
    watchdog.enforce(state, actions, time.time())
    assert unit in state["paused"]
    event_count = len(state["outbox"])
    watchdog.enforce(state, actions, time.time())
    assert len(state["outbox"]) == event_count
    subprocess.run(["systemctl", "--user", "thaw", unit], check=True)
    watchdog.enforce(state, actions, time.time())
    assert len(state["outbox"]) == event_count + 1
    production = watchdog.collect()[0]
    assert not any(value["group"].endswith("/" + unit) for value in production.values())
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "state.json"
        watchdog.save(path, state)
        reloaded = json.loads(path.read_text())
        assert unit in reloaded["paused"]
    if "--publish" in sys.argv:
        import httpx
        import logfire

        base = os.environ["PREFECT_API_URL"].rstrip("/")
        auth = httpx.BasicAuth(*os.environ["PREFECT_API_AUTH_STRING"].split(":", 1))
        with httpx.Client(auth=auth, timeout=5) as client:
            response = client.get(
                f"{base}/block_types/slug/secret/block_documents/name/logfire-server-write-token",
                params={"include_secrets": "true"},
            )
            response.raise_for_status()
            logfire.configure(
                token=response.json()["data"]["value"],
                service_name="bandwidth-watchdog",
                console=False,
            )
            item = state["outbox"][-1]
            item["payload"]["summary"] = (
                f"TEST PASSED: bandwidth watchdog froze disposable service after {actions[0]['bytes']} loopback bytes at lowered test thresholds. No real overage; no operator action needed. DeepSeek remains paused."
            )
            item["payload"]["test"] = True
            state["outbox"] = [item]
            watchdog.flush(state, client, base)
            print(json.dumps({"published_test_event": item["id"]}))
    print(
        json.dumps(
            {
                "result": "passed",
                "unit": unit,
                "bytes": actions[0]["bytes"],
                "frozen": True,
                "production_excludes_loopback": True,
                "state_persisted": True,
            }
        )
    )
finally:
    subprocess.run(["systemctl", "--user", "stop", unit], check=False)
    server.shutdown()
    server.server_close()
    thread.join()
