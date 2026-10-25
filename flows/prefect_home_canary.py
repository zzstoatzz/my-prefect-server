"""No external side effects: prove the isolated pilot's worker and artifacts."""

import datetime as dt
import socket

from prefect import flow, task
from prefect.artifacts import create_table_artifact


@task
def sample_host() -> dict[str, str]:
    return {"host": socket.gethostname(), "at": dt.datetime.now(dt.UTC).isoformat()}


@flow(name="prefect-home-canary", log_prints=True, timeout_seconds=120)
def prefect_home_canary() -> dict[str, str]:
    sample = sample_host()
    create_table_artifact(key="prefect-home-canary", table=[sample])
    print(f"Pilot canary ran on {sample['host']}")
    return sample
