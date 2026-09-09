"""Verify the real inference bridge from inside the real Linux agent boundary."""

import json
import os
import runpy
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main():
    sandbox = runpy.run_path(sys.argv[1])
    bridge = runpy.run_path(sys.argv[2])

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            assert self.headers["Authorization"] == "fake-trusted-identity"
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert body["model"] == "openai/gpt-5.6-luna"
            self.send_response(200)
            self.send_header(
                "Content-Type", "text/event-stream" if body.get("stream") else "text/plain"
            )
            self.end_headers()
            if body.get("stream"):
                for delta, finish in [
                    ({"role": "assistant", "content": "pi-bridge-ok"}, None),
                    ({}, "stop"),
                ]:
                    chunk = {
                        "id": "probe",
                        "object": "chat.completion.chunk",
                        "created": 0,
                        "model": body["model"],
                        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                    }
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
            else:
                self.wfile.write(b"namespace-bridge-ok")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="phi-bridge-probe-") as temporary:
            root = Path(temporary)
            paths = {name: root / name for name in ("workspace", "home", "tools")}
            for path in paths.values():
                path.mkdir()
                os.chown(path, 2000, 2000)
            endpoint = root / "inference.sock"
            with bridge["inference_bridge"](
                endpoint,
                upstream=f"http://127.0.0.1:{server.server_port}",
                authorization="fake-trusted-identity",
            ):
                result = subprocess.run(
                    sandbox["sandbox_command"](
                        **paths,
                        inference_socket=endpoint,
                        command=[
                            "/usr/bin/curl",
                            "--fail",
                            "--silent",
                            "--show-error",
                            "--unix-socket",
                            "/run/aperture.sock",
                            "-H",
                            "Content-Type: application/json",
                            "-d",
                            json.dumps({"model": "openai/gpt-5.6-luna", "messages": []}),
                            "http://bridge/v1/chat/completions",
                        ],
                    ),
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                assert result.stdout == "namespace-bridge-ok", result.stdout
                print("isolated namespace -> mounted inference socket -> trusted upstream: passed")
                config = paths["home"] / ".pi/agent"
                config.mkdir(parents=True)
                (config / "models.json").write_text(json.dumps(sandbox["aperture_models"]()))
                for path in paths["home"].rglob("*"):
                    os.chown(path, 2000, 2000)
                paths["tools"] = Path("/opt/phi-agent")
                result = subprocess.run(
                    sandbox["sandbox_command"](
                        **paths,
                        inference_socket=endpoint,
                        command=[
                            "/usr/bin/python3",
                            "/opt/phi-agent/pi_relay.py",
                            "node",
                            sandbox["PI_ENTRYPOINT"],
                            "--print",
                            "--no-session",
                            "--no-tools",
                            "--provider",
                            "aperture",
                            "--model",
                            "openai/gpt-5.6-luna",
                            "Reply briefly.",
                        ],
                    ),
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                assert "pi-bridge-ok" in result.stdout, result.stdout
                print(
                    "Pi -> namespace loopback relay -> inference socket -> fake SSE upstream: passed"
                )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
