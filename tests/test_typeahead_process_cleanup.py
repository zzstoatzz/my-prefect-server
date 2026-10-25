import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from flows.typeahead_index import ProcessExecutionError, _stream


def alive(pid):
    result = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
    )
    return result.returncode == 0 and not result.stdout.strip().startswith("Z")


def test_silent_command_timeout_stops_its_process(tmp_path):
    marker = tmp_path / "child"
    command = [
        sys.executable,
        "-c",
        f"import os,time,pathlib,json;pathlib.Path({str(marker)!r}).write_text(json.dumps([os.getpid(),os.environ['TMPDIR']]));time.sleep(60)",
    ]
    with pytest.raises(RuntimeError, match="exceeded"):
        _stream(command, tmp_path, dict(os.environ), timeout=1)
    pid, scratch = json.loads(marker.read_text())
    assert not alive(pid)
    assert not Path(scratch).exists()


def test_guard_preserves_child_failure_output(tmp_path):
    with pytest.raises(ProcessExecutionError) as error:
        _stream(
            [sys.executable, "-c", "print('real child failure',flush=True);raise SystemExit(7)"],
            tmp_path,
            dict(os.environ),
            timeout=10,
        )
    assert error.value.returncode == 7
    assert "real child failure" in error.value.output


def test_parent_sigkill_stops_child_and_grandchild(tmp_path):
    marker = tmp_path / "processes.json"
    grandchild = "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)"
    command = (
        'import os,pathlib,subprocess,sys,time,signal,json;signal.signal(signal.SIGTERM,signal.SIG_IGN);child=subprocess.Popen([sys.executable,"-c",'
        + repr(grandchild)
        + "]);pathlib.Path("
        + repr(str(marker))
        + ").write_text(json.dumps([os.getpid(),child.pid]));time.sleep(60)"
    )
    parent_code = (
        'import os,subprocess,sys,time;subprocess.Popen([sys.executable,"-m","mps.process_guard","--parent",str(os.getpid()),"--",sys.executable,"-c",'
        + repr(command)
        + "],start_new_session=True);time.sleep(60)"
    )
    parent = subprocess.Popen([sys.executable, "-c", parent_code])
    pids = []
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists()
        pids = json.loads(marker.read_text())
        os.kill(parent.pid, signal.SIGKILL)
        parent.wait(timeout=5)
        deadline = time.monotonic() + 12
        while any(alive(pid) for pid in pids) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not any(alive(pid) for pid in pids)
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait()
        for pid in pids:
            if alive(pid) and os.getpgid(pid) == pids[0]:
                os.kill(pid, signal.SIGKILL)
