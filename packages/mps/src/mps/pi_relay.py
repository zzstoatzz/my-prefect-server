"""Run inside the agent namespace: expose its inference socket on loopback."""

import socket
import subprocess
import sys
import time


def main() -> int:
    with subprocess.Popen(
        [
            "/usr/bin/socat",
            "TCP4-LISTEN:8888,bind=127.0.0.1,reuseaddr,fork",
            "UNIX-CONNECT:/run/aperture.sock",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) as relay:
        try:
            deadline = time.monotonic() + 5
            while True:
                if relay.poll() is not None:
                    raise RuntimeError("Inference relay exited during startup")
                try:
                    with socket.create_connection(("127.0.0.1", 8888), timeout=0.1):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Inference relay did not start") from None
                    time.sleep(0.05)
            return subprocess.call(sys.argv[1:])
        finally:
            relay.terminate()
            try:
                relay.wait(timeout=5)
            except subprocess.TimeoutExpired:
                relay.kill()
                relay.wait()


if __name__ == "__main__":
    raise SystemExit(main())
