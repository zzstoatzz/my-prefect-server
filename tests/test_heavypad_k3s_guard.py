import os
import subprocess
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parent.parent / "deploy/heavypad-prefect/k3s-guard.sh"

FAKE_DOCKER = """#!/bin/sh
restarts=$(cat "$STATE/restarts")
case "$1" in
  exec) [ "$restarts" -ge "$READY_AFTER" ] ;;
  restart) echo $((restarts + 1)) >"$STATE/restarts"; echo "$2" ;;
  *) exit 2 ;;
esac
"""


def run_guard(tmp_path, ready_after):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    (tmp_path / "restarts").write_text("0\n")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "STATE": str(tmp_path),
        "READY_AFTER": str(ready_after),
        "PREFECT_HOME_K3S_WAIT": "0",
        "PREFECT_HOME_K3S_POLL": "0",
    }
    result = subprocess.run(
        ["bash", str(GUARD)], env=env, capture_output=True, text=True, check=False
    )
    return result, int((tmp_path / "restarts").read_text())


def test_healthy_k3s_is_left_alone(tmp_path):
    result, restarts = run_guard(tmp_path, ready_after=0)
    assert result.returncode == 0
    assert restarts == 0
    assert "ready after 0 restart(s)" in result.stdout


@pytest.mark.parametrize("needed", [1, 2])
def test_boot_race_recovers_with_bounded_restarts(tmp_path, needed):
    result, restarts = run_guard(tmp_path, ready_after=needed)
    assert result.returncode == 0
    assert restarts == needed


def test_persistent_failure_stops_after_two_restarts(tmp_path):
    result, restarts = run_guard(tmp_path, ready_after=99)
    assert result.returncode == 1
    assert restarts == 2
    assert "still not ready" in result.stderr
