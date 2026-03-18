"""simple diagnostic flow — runs every 5 minutes, prints system info."""

import datetime
import os
import platform

from prefect import flow


@flow(log_prints=True)
def diagnostics():
    now = datetime.datetime.now(datetime.timezone.utc)
    print(f"time:     {now.isoformat()}")
    print(f"hostname: {platform.node()}")
    print(f"python:   {platform.python_version()}")
    print(f"platform: {platform.platform()}")
    print(f"pid:      {os.getpid()}")
    print(f"cwd:      {os.getcwd()}")


if __name__ == "__main__":
    flow.from_source(
        source="https://tangled.sh/zzstoatzz.io/my-prefect-server.git",
        entrypoint="flows/diagnostics.py:diagnostics",
    ).deploy(
        name="diagnostics",
        work_pool_name="kubernetes-pool",
        cron="*/5 * * * *",
    )
