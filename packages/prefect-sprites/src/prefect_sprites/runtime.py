"""Run one execution attempt and retain its outcome across service restarts."""

import fcntl
import json
import os
import signal
import subprocess
import time
from pathlib import Path


class _TerminationRequested(Exception):
    pass


def lease(method: str) -> None:
    argv = [
        "curl",
        "--fail",
        "--silent",
        "--show-error",
        "--unix-socket",
        "/.sprite/api.sock",
        "-X",
        method,
        "http://sprite/v1/tasks/prefect",
    ]
    if method == "PUT":
        argv += ["-H", "Content-Type: application/json", "-d", '{"expire":"5m"}']
    subprocess.run(argv, check=method == "PUT", stdout=subprocess.DEVNULL, timeout=15)


def stop_execution_group(path: str | None) -> None:
    if path is None:
        return
    group = Path(path)
    if not group.exists():
        return  # A Sprite reboot removes the old cgroup and its processes.
    (group / "cgroup.kill").write_text("1")
    deadline = time.monotonic() + 10
    while "populated 1" in (group / "cgroup.events").read_text():
        if time.monotonic() >= deadline:
            raise RuntimeError("Execution cgroup still contains live processes")
        time.sleep(0.05)


def write_state(directory: Path, state: dict) -> None:
    if state.get("phase") == "exited":
        state = dict(state)
        log = directory / "output.log"
        if log.exists():
            with log.open("rb") as file:
                file.seek(max(0, log.stat().st_size - 65536))
                state["log_tail"] = file.read().decode(errors="replace")
        timings = directory / "bootstrap-timings.json"
        if timings.exists():
            state["bootstrap_seconds"] = json.loads(timings.read_text())
    temporary = directory / "state.tmp"
    with temporary.open("w") as file:
        json.dump(state, file)
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(directory / "state.json")


def execute(directory: Path) -> None:
    os.umask(0o077)
    with (directory / "execution.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        state_path = directory / "state.json"
        if state_path.exists():
            previous = json.loads(state_path.read_text())
            if previous["phase"] == "running":
                stop_execution_group(previous.get("cgroup"))
                # A restarted service cannot reconstruct a killed agent's memory.
                # Leave retry decisions to Prefect instead of replaying user code.
                write_state(
                    directory,
                    {
                        **previous,
                        "phase": "exited",
                        "exit_code": 255,
                        "reason": "runtime restarted",
                        "finished_at": time.time(),
                    },
                )
            (directory / "config.json").unlink(missing_ok=True)
            return
        config = json.loads((directory / "config.json").read_text())
        group = Path(config["cgroup"]) if config.get("cgroup") else None
        if group:
            group.mkdir(exist_ok=True)
            if not (group / "cgroup.kill").exists():
                raise RuntimeError("Execution requires cgroup v2 kill support")
        state = {
            "phase": "running",
            "started_at": time.time(),
            "flow_run_id": config["flow_run_id"],
            "cgroup": str(group) if group else None,
        }
        write_state(directory, state)
        environment = {
            key: os.environ[key] for key in ("PATH", "HOME", "LANG") if key in os.environ
        }
        environment.update(config["env"])
        if config.get("runtime_bin"):
            environment["PATH"] = config["runtime_bin"] + ":" + environment.get("PATH", "")
        code, reason = 255, "launch failed"
        process = None

        def terminate(signum, frame):
            if process is not None:
                raise _TerminationRequested

        signal.signal(signal.SIGTERM, terminate)

        def enter_group():
            # The standalone supervisor is single-threaded. Move the child
            # before exec so every descendant inherits this cgroup.
            (group / "cgroup.procs").write_text(str(os.getpid()))

        try:
            # This lease expires even if the runtime is killed before its finally.
            lease("PUT")
            with (directory / "output.log").open("wb") as output:
                process = subprocess.Popen(
                    config["argv"],
                    env=environment,
                    cwd=config["cwd"],
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    # Keep the execution lock held by the flow engine if this
                    # supervisor dies. A replacement supervisor must not mark
                    # the attempt exited while that engine is still alive.
                    pass_fds=() if group else (lock.fileno(),),
                    preexec_fn=enter_group if group else None,
                )
                deadline = time.monotonic() + config["timeout_seconds"]
                try:
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(
                                config["argv"], config["timeout_seconds"]
                            )
                        try:
                            code = process.wait(timeout=min(60, remaining))
                            break
                        except subprocess.TimeoutExpired:
                            if time.monotonic() >= deadline:
                                raise
                            lease("PUT")
                    reason = "process exited"
                except (subprocess.TimeoutExpired, _TerminationRequested) as exc:
                    signal.signal(signal.SIGTERM, signal.SIG_IGN)
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                    if isinstance(exc, _TerminationRequested):
                        code, reason = 143, "execution cancelled"
                    else:
                        code, reason = 124, "execution timeout"
        finally:
            # A detached grandchild may outlive the engine. Never publish an
            # exited outcome before all processes in the execution are stopped.
            stop_execution_group(str(group) if group else None)
            write_state(
                directory,
                {
                    **state,
                    "phase": "exited",
                    "exit_code": code,
                    "reason": reason,
                    "finished_at": time.time(),
                },
            )
            (directory / "config.json").unlink(missing_ok=True)
            lease("DELETE")


if __name__ == "__main__":
    execute(Path(__file__).parent)
