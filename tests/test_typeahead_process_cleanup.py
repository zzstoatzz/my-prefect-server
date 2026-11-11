import json
import os
import shutil
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
        _stream(command, tmp_path, dict(os.environ), timeout=5)
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


def test_stream_reserve_refuses_to_start_child(tmp_path):
    marker = tmp_path / "started"
    env = dict(
        os.environ,
        INDEX_BUILD_ROOT=str(tmp_path),
        INDEX_MIN_REMAIN_GIB="1000000000",
    )
    with pytest.raises(RuntimeError, match="disk reserve"):
        _stream(
            [sys.executable, "-c", f"open({str(marker)!r},'w').write('started')"],
            tmp_path,
            env,
            timeout=10,
        )
    assert not marker.exists()


def test_stream_stops_real_child_if_watched_disk_disappears(tmp_path):
    root = tmp_path / "watched"
    root.mkdir()
    if shutil.disk_usage(root).free < 2**30:
        pytest.skip("requires one GiB of real free space")
    pid_file = tmp_path / "pid"
    code = (
        "import os,time,pathlib; "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); "
        f"pathlib.Path({str(root)!r}).rmdir(); "
        "print('disk disappeared',flush=True); time.sleep(30)"
    )
    env = dict(os.environ, INDEX_BUILD_ROOT=str(root), INDEX_MIN_REMAIN_GIB="1")
    with pytest.raises(FileNotFoundError):
        _stream([sys.executable, "-c", code], tmp_path, env, timeout=10)
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


def test_guard_stops_child_when_sigterm_arrives_during_launch(monkeypatch):
    from mps import process_guard

    launch = process_guard.subprocess.Popen
    children = []

    def launch_then_signal(*args, **kwargs):
        child = launch(*args, **kwargs)
        children.append(child)
        os.kill(os.getpid(), signal.SIGTERM)
        return child

    monkeypatch.setattr(process_guard.subprocess, "Popen", launch_then_signal)
    previous = signal.getsignal(signal.SIGTERM)
    try:
        with pytest.raises(SystemExit) as stopped:
            process_guard.run([sys.executable, "-c", "import time; time.sleep(60)"], os.getppid())
        assert stopped.value.code == 128 + signal.SIGTERM
        assert children[0].poll() is not None
    finally:
        signal.signal(signal.SIGTERM, previous)
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()
