"""Exercise the supervisor with real child processes and a stubbed Sprite lease."""

import json
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress

import pytest
from prefect_sprites import runtime


@pytest.fixture
def configure(tmp_path, monkeypatch):
    lease_calls = []

    def lease(argv, **kwargs):
        assert argv[0] == "curl"
        lease_calls.append(argv)

    monkeypatch.setattr(runtime.subprocess, "run", lease)

    def prepare(code, timeout=5):
        (tmp_path / "config.json").write_text(
            json.dumps(
                {
                    "flow_run_id": "test-flow-run",
                    "env": {"FLOW_SETTING": "explicit"},
                    "argv": [sys.executable, "-c", code],
                    "cwd": str(tmp_path),
                    "timeout_seconds": timeout,
                }
            )
        )
        return tmp_path

    return prepare, lease_calls


def test_outcome_survives_restart_without_replaying_code(configure):
    prepare, lease_calls = configure
    directory = prepare("from pathlib import Path; Path('count').write_text('once')")
    runtime.execute(directory)
    state = json.loads((directory / "state.json").read_text())
    assert state["exit_code"] == 0
    assert state["finished_at"] >= state["started_at"]
    assert not (directory / "config.json").exists()
    runtime.execute(directory)
    assert (directory / "count").read_text() == "once"
    assert len(lease_calls) == 2


def test_timeout_terminates_child_and_records_outcome(configure):
    prepare, lease_calls = configure
    directory = prepare("import time; time.sleep(10)", timeout=0.05)
    runtime.execute(directory)
    state = json.loads((directory / "state.json").read_text())
    assert state["exit_code"] == 124
    assert state["reason"] == "execution timeout"
    assert not (directory / "config.json").exists()
    assert "DELETE" in lease_calls[-1]


def test_unfinished_attempt_is_not_replayed(configure):
    prepare, lease_calls = configure
    directory = prepare("raise AssertionError('must not replay')")
    runtime.write_state(directory, {"phase": "running", "started_at": 1})
    runtime.execute(directory)
    state = json.loads((directory / "state.json").read_text())
    assert state["phase"] == "exited"
    assert state["reason"] == "runtime restarted"
    assert state["exit_code"] == 255
    assert not (directory / "config.json").exists()
    assert lease_calls == []


def test_child_failure_retains_diagnostics_and_removes_environment(configure):
    prepare, _lease_calls = configure
    directory = prepare("import sys; print('diagnostic'); sys.exit(17)")
    runtime.execute(directory)
    assert json.loads((directory / "state.json").read_text())["exit_code"] == 17
    assert (directory / "output.log").read_text().strip() == "diagnostic"
    assert not (directory / "config.json").exists()


def test_supervisor_death_does_not_report_live_engine_as_exited(configure):
    prepare, _lease_calls = configure
    directory = prepare(
        "import os, time; from pathlib import Path; "
        "Path('child-pid').write_text(str(os.getpid())); time.sleep(30)"
    )
    launcher = (
        "from pathlib import Path; from prefect_sprites import runtime; "
        "runtime.subprocess.run = lambda *a, **k: None; "
        f"runtime.execute(Path({str(directory)!r}))"
    )
    supervisor = subprocess.Popen([sys.executable, "-c", launcher])
    child_pid = None
    try:
        deadline = time.monotonic() + 5
        while not (directory / "child-pid").exists():
            assert supervisor.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.01)
        child_pid = int((directory / "child-pid").read_text())
        supervisor.kill()
        supervisor.wait(timeout=5)
        runtime.execute(directory)
        state = json.loads((directory / "state.json").read_text())
        assert state["phase"] == "running"
        assert (directory / "config.json").exists()
        os.kill(child_pid, 0)
        os.killpg(child_pid, signal.SIGKILL)
        deadline = time.monotonic() + 5
        while state["phase"] == "running":
            assert time.monotonic() < deadline
            time.sleep(0.01)
            runtime.execute(directory)
            state = json.loads((directory / "state.json").read_text())
        assert state["reason"] == "runtime restarted"
        assert not (directory / "config.json").exists()
    finally:
        if supervisor.poll() is None:
            supervisor.kill()
            supervisor.wait(timeout=5)
        if child_pid is not None:
            with suppress(ProcessLookupError):
                os.killpg(child_pid, signal.SIGKILL)
