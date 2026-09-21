import os
import subprocess
import sys
import time

from flows.stream_admission import _process_group_exists, _terminate_process_group


def test_terminate_process_group_stops_leader_and_child(tmp_path):
    child_pid_path = tmp_path / "child.pid"
    leader = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import pathlib, subprocess, sys, time; "
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid)); "
            "time.sleep(60)",
        ],
        start_new_session=True,
    )
    try:
        for _ in range(100):
            if child_pid_path.exists():
                break
            time.sleep(0.01)
        child_pid = int(child_pid_path.read_text())

        assert os.getpgid(child_pid) == leader.pid
        assert _process_group_exists(leader.pid)
        assert not _terminate_process_group(leader.pid, timeout_s=2)
        leader.wait(timeout=2)
        assert not _process_group_exists(leader.pid)
    finally:
        if leader.poll() is None:
            leader.kill()
            leader.wait()
