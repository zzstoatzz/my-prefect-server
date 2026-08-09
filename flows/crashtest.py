"""Deliberately crash, to prove the pull-step compounding fix.

A hard exit denies the runner a clean terminal state, so it tries to load the
flow again to execute on_crashed hooks — and loading re-runs the deployment's
pull steps. That second pass is where `set_working_directory` used to resolve
"my-prefect-server" against the cwd the first pass had already moved to,
cloning one level deeper every time. Delete this flow once the fix is proven.
"""

import os

from prefect import flow


@flow(name="crashtest", log_prints=True)
def crashtest() -> None:
    print(f"cwd: {os.getcwd()}")
    os._exit(1)
