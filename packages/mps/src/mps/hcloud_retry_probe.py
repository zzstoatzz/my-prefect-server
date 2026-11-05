"""Manual cloud qualification: a flow retry must retain its original process."""

import json
import os
import socket
import tempfile
from pathlib import Path

from prefect import flow
from prefect.runtime import flow_run


@flow(
    name="cloud-builder-retry-probe",
    retries=1,
    retry_delay_seconds=90,
    timeout_seconds=240,
    persist_result=False,
    log_prints=True,
)
def cloud_builder_retry_probe() -> dict:
    attempt = flow_run.run_count
    receipt = Path(tempfile.gettempdir()) / f"cloud-retry-{flow_run.id}.json"
    identity = {"pid": os.getpid(), "host": socket.gethostname()}
    if attempt == 1:
        with receipt.open("x") as file:
            json.dump(identity, file)
        print({"attempt": 1, **identity})
        raise RuntimeError("Intentional failure to exercise an in-process retry")
    if attempt != 2 or json.loads(receipt.read_text()) != identity:
        raise RuntimeError("Retry did not retain its original process")
    receipt.unlink()
    result = {"attempts": attempt, "same_process": True, **identity}
    print(result)
    return result
