"""Disposable Linux proof: isolated Pi -> local filter -> ts-unplug -> Aperture.

Run as root with mps on PYTHONPATH and Prefect installed. Without --live,
exercise the same boundary against a fake upstream. No tailnet credentials
are accepted here: ts-unplug is a separately supervised trusted process.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mps.inference_bridge import inference_bridge
from mps.inference_models import resolve_inference_model
from mps.pi_execution import prepare_pi_runtime, read_pi_events, tree_paths
from mps.pi_sandbox import AGENT_UID, PI_ENTRYPOINT, aperture_models, sandbox_command

MODEL = "openai/gpt-5.6-luna"


class FakeInference(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert self.path == "/v1/chat/completions"
        assert body["model"] == MODEL
        assert not self.headers.get("Authorization")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for delta, finish in [({"content": "tsunplug-proof-ok"}, None), ({}, "stop")]:
            event = {
                "id": "proof",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": MODEL,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


BOUNDARY = r"""
import os, socket
from pathlib import Path
assert os.getuid() == 2000 and os.getgroups() == []
status = Path('/proc/self/status').read_text()
assert 'CapEff:\t0000000000000000' in status
assert 'NoNewPrivs:\t1' in status
for key in ('TS_AUTHKEY', 'PREFECT_API_AUTH_STRING', 'PHI_INFERENCE_TOKEN'):
    assert key not in os.environ, key
for path in ('/root', '/var/lib/tsunplug-proof', '/.sprite/api.sock',
             '/var/lib/prefect-sprites', '/workspace/escape'):
    assert not Path(path).exists(), path
assert not Path('/proc/net/route').read_text().splitlines()[1:]
with socket.socket() as client:
    client.settimeout(1)
    assert client.connect_ex(('127.0.0.1', 18080)) != 0
try:
    Path('/workspace/.git/probe').write_text('bad')
except OSError:
    pass
else:
    raise AssertionError('git metadata writable')
Path('/workspace/probe').write_text('ok')
"""


def probe(upstream: str, live: bool, model: str = MODEL, credential: str | None = None) -> dict:
    selected = resolve_inference_model(model)
    tools = prepare_pi_runtime()
    with tempfile.TemporaryDirectory(prefix="tsunplug-proof-") as directory:
        root = Path(directory)
        workspace, home = root / "workspace", root / "home"
        (workspace / ".git").mkdir(parents=True)
        config = home / ".pi/agent"
        config.mkdir(parents=True)
        (config / "models.json").write_text(json.dumps(aperture_models(model=model)))
        canary = root / "supervisor-secret"
        canary.write_text("fake-canary")
        (workspace / "escape").symlink_to(canary)
        for path in [*tree_paths(workspace), *tree_paths(home)]:
            os.chown(path, AGENT_UID, AGENT_UID, follow_symlinks=False)
        with inference_bridge(
            root / "inference.sock",
            upstream=upstream,
            model=model,
            anthropic_api_key=credential,
            max_requests=3,
            max_output_tokens=64,
        ):

            def run(argv):
                result = subprocess.run(
                    sandbox_command(
                        workspace=workspace,
                        home=home,
                        tools=tools,
                        inference_socket=root / "inference.sock",
                        command=argv,
                    ),
                    env={"PATH": "/usr/bin:/bin", "TS_AUTHKEY": "fake-supervisor-canary"},
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
                if result.returncode:
                    raise RuntimeError(f"Sandbox command failed with exit {result.returncode}")
                return result.stdout

            run(["/usr/bin/python3", "-c", BOUNDARY])
            for route, rejected_model, expected in [
                ("/workflows/request", model, "404"),
                (selected.path, "openai/not-authorized", "400"),
            ]:
                status = run(
                    [
                        "curl",
                        "-s",
                        "-o",
                        "/dev/null",
                        "-w",
                        "%{http_code}",
                        "--unix-socket",
                        "/run/aperture.sock",
                        "-H",
                        "Content-Type: application/json",
                        "-d",
                        json.dumps({"model": rejected_model, "messages": []}),
                        "http://bridge" + route,
                    ]
                )
                assert status == expected, (route, status)
            output = run(
                [
                    "/usr/bin/python3",
                    "/opt/phi-agent/pi_relay.py",
                    "node",
                    PI_ENTRYPOINT,
                    "--print",
                    "--mode",
                    "json",
                    "--no-session",
                    "--no-tools",
                    "--provider",
                    "aperture",
                    "--model",
                    selected.name,
                    "Reply with exactly: tsunplug-proof-ok",
                ]
            )
            answer = read_pi_events(output).strip()
            assert answer == "tsunplug-proof-ok", "Unexpected inference response"
        assert canary.read_text() == "fake-canary"
        return {
            "live_aperture": live,
            "model": selected.name,
            "pi_response": answer,
            "boundary_passed": True,
            "forbidden_routes_rejected": True,
            "wrong_model_rejected": True,
            "remote_grant_service_used": False,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--credential-stdin", action="store_true")
    parser.add_argument("--upstream", default="http://127.0.0.1:18080/v1/chat/completions")
    args = parser.parse_args()
    credential = json.load(sys.stdin)["anthropic_api_key"] if args.credential_stdin else None
    server = None
    try:
        upstream = args.upstream
        if not args.live:
            server = ThreadingHTTPServer(("127.0.0.1", 0), FakeInference)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            upstream = f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
        print(json.dumps(probe(upstream, args.live, args.model, credential)))
    finally:
        if server:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
