import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID

import dagster as dg


def submit_and_wait(run_id: str, timeout: float = 1680):
    run_id = str(UUID(run_id))
    home = Path(os.environ["DAGSTER_HOME"])
    with dg.DagsterInstance.get() as instance:
        with (home / "submit.lock").open("a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if instance.get_run_by_id(run_id) is None:
                subprocess.run(
                    [
                        str(Path(sys.executable).with_name("dagster")),
                        "job",
                        "launch",
                        "-w",
                        "deploy/dagster/workspace.yaml",
                        "-j",
                        "hub_transform",
                        "--run-id",
                        run_id,
                        "--tags",
                        json.dumps({"prefect/flow_run_id": run_id}),
                    ],
                    check=True,
                    timeout=120,
                )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run = instance.get_run_by_id(run_id)
            if run is None:
                raise RuntimeError(f"Dagster lost submitted run {run_id}")
            if run.is_finished:
                if run.status != dg.DagsterRunStatus.SUCCESS:
                    raise RuntimeError(f"Dagster run {run_id}: {run.status.value}")
                print(f"Dagster run {run_id} succeeded", flush=True)
                return
            time.sleep(5)
        raise TimeoutError(f"Dagster run {run_id} has not finished; inspect it before retrying")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    args = parser.parse_args()
    submit_and_wait(args.run_id)


if __name__ == "__main__":
    main()
