"""Hand the completed ingestion batch to Dagster and await its result."""

import os
import subprocess
from pathlib import Path

from prefect import flow
from prefect.runtime import flow_run


@flow(name="transform", log_prints=True, timeout_seconds=1800)
def transform():
    release = Path(os.environ.get("HUB_DAGSTER_ROOT", "/home/stoat/dagster-hub/current"))
    env = {
        **os.environ,
        "PATH": f"{release / '.venv/bin'}:{os.environ.get('PATH', '')}",
        "DAGSTER_HOME": os.environ.get("DAGSTER_HOME", "/home/stoat/.local/share/dagster-hub"),
    }
    subprocess.run(
        [str(release / ".venv/bin/python"), "-m", "hub_dagster.bridge", str(flow_run.id)],
        cwd=release,
        env=env,
        check=True,
        timeout=1740,
    )


if __name__ == "__main__":
    transform()
