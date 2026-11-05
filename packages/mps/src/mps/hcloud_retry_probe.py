"""Manual cloud qualification: a flow retry must retain its original process."""

import json
import os
import socket
import tempfile
from pathlib import Path

from prefect import flow
from prefect.context import get_run_context


@flow(
    name="cloud-builder-retry-probe",
    retries=1,
    retry_delay_seconds=90,
    timeout_seconds=240,
    persist_result=False,
    log_prints=True,
)
def cloud_builder_retry_probe() -> dict:
    run = get_run_context().flow_run
    receipt = Path(tempfile.gettempdir()) / f"cloud-retry-{run.id}.json"
    identity = {"pid": os.getpid(), "host": socket.gethostname()}
    if run.run_count == 1:
        with receipt.open("x") as file:
            json.dump(identity, file)
        print({"attempt": 1, **identity})
        raise RuntimeError("Intentional failure to exercise an in-process retry")
    if run.run_count != 2 or json.loads(receipt.read_text()) != identity:
        raise RuntimeError("Retry did not retain its original process")
    receipt.unlink()
    result = {"attempts": run.run_count, "same_process": True, **identity}
    print(result)
    return result
