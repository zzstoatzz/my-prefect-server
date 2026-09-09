"""Explicit Linux-only process probe; run as root on the disposable Sprite."""

import json
import os
import runpy
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def main():
    runtime_path = Path(sys.argv[1])
    runtime = runpy.run_path(str(runtime_path))
    # Exercise process containment independently of service/lease transport.
    runtime["subprocess"].run = lambda *args, **kwargs: None
    group = Path(f"/sys/fs/cgroup/prefect-probe-{os.getpid()}")
    try:
        with tempfile.TemporaryDirectory(prefix="prefect-process-probe-") as temporary:
            root = Path(temporary)
            for mode in ("detached-child", "supervisor-death"):
                directory = root / mode
                directory.mkdir()
                child_code = (
                    "import os, time; from pathlib import Path; "
                    "Path('ready').write_text(str(os.getpid())); time.sleep(60)"
                )
                code = (
                    "import subprocess, sys, time; from pathlib import Path; "
                    f"subprocess.Popen([sys.executable, '-c', {child_code!r}], "
                    "start_new_session=True); "
                    "\nwhile not Path('ready').exists(): time.sleep(0.01)\n"
                )
                if mode == "supervisor-death":
                    code += "time.sleep(60)\n"
                config = {
                    "flow_run_id": mode,
                    "argv": [sys.executable, "-c", code],
                    "env": {},
                    "cwd": str(directory),
                    "timeout_seconds": 5,
                    "cgroup": str(group),
                }
                (directory / "config.json").write_text(json.dumps(config))
                if mode == "detached-child":
                    runtime["execute"](directory)
                else:
                    launcher = (
                        "import runpy; from pathlib import Path; "
                        f"r=runpy.run_path({str(runtime_path)!r}); "
                        "r['subprocess'].run=lambda *a, **k: None; "
                        f"r['execute'](Path({str(directory)!r}))"
                    )
                    supervisor = subprocess.Popen([sys.executable, "-c", launcher])
                    try:
                        deadline = time.monotonic() + 10
                        while not (directory / "ready").exists():
                            assert supervisor.poll() is None
                            assert time.monotonic() < deadline
                            time.sleep(0.01)
                        supervisor.kill()
                        supervisor.wait(timeout=5)
                        runtime["execute"](directory)
                    finally:
                        if supervisor.poll() is None:
                            supervisor.kill()
                            supervisor.wait(timeout=5)
                state = json.loads((directory / "state.json").read_text())
                assert state["phase"] == "exited", state
                assert "populated 0" in (group / "cgroup.events").read_text()
                assert not (directory / "config.json").exists()
                if mode == "supervisor-death":
                    assert state["reason"] == "runtime restarted", state
                print(f"{mode}: exited; cgroup empty; saved environment removed", flush=True)
    finally:
        runtime["stop_execution_group"](str(group))
        if group.exists():
            group.rmdir()


if __name__ == "__main__":
    main()
