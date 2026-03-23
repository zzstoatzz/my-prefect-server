"""simple diagnostic flow — prints system info."""

import datetime
import os
import platform

from prefect import flow


@flow(name="diagnostics", log_prints=True)
def diagnostics():
    now = datetime.datetime.now(datetime.timezone.utc)
    print(f"time:     {now.isoformat()}")
    print(f"hostname: {platform.node()}")
    print(f"python:   {platform.python_version()}")
    print(f"platform: {platform.platform()}")
    print(f"pid:      {os.getpid()}")
    print(f"cwd:      {os.getcwd()}")
