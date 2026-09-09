"""Long-lived authenticated ingress; only this host needs Aperture access."""

import argparse
import os
import signal
import threading
from pathlib import Path

from mps.inference_bridge import inference_bridge
from mps.inference_grants import InferenceGrants
from mps.workflow_requests import WorkflowRequests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--port", type=int, default=8999)
    parser.add_argument(
        "--workflow-deployment", help="Deployment ID enabled for investigation requests"
    )
    parser.add_argument("--workflow-token-block", default="phi-workflow-request-token")
    args = parser.parse_args()
    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    with inference_bridge(
        None,
        upstream=args.upstream,
        listen=("127.0.0.1", args.port),
        grants=InferenceGrants(args.database),
        workflow_requests=WorkflowRequests(
            token_block=args.workflow_token_block,
            prefect_url=os.environ["PREFECT_API_URL"],
            prefect_auth=os.environ["PREFECT_API_AUTH_STRING"],
            deployments={"investigate": args.workflow_deployment},
        )
        if args.workflow_deployment
        else None,
    ):
        stopped.wait()


if __name__ == "__main__":
    main()
