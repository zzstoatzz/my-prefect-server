"""Long-lived authenticated ingress; only this host needs Aperture access."""

import argparse
import base64
import json
import logging
import os
import signal
import threading
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from mps.inference_bridge import NoRedirect, inference_bridge
from mps.inference_grants import InferenceGrants
from mps.workflow_requests import WorkflowRequests


def load_inference_key(block: str) -> str:
    """Read an existing Prefect Secret on the trusted host, never in a Sprite."""
    base = os.environ["PREFECT_API_URL"].rstrip("/")
    if urlsplit(base).scheme != "https":
        raise ValueError("Inference credentials require HTTPS")
    auth = base64.b64encode(os.environ["PREFECT_API_AUTH_STRING"].encode()).decode()
    request = Request(
        base
        + "/block_types/slug/secret/block_documents/name/"
        + quote(block, safe="")
        + "?include_secrets=true",
        headers={"Authorization": "Basic " + auth},
    )
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=20) as response:
            value = json.load(response)["data"]["value"]
        if not isinstance(value, str) or not value:
            raise ValueError("Missing key")
        return value
    except Exception:
        raise RuntimeError("Unable to load inference credential") from None


def main():
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--port", type=int, default=8999)
    parser.add_argument(
        "--workflow-deployment", help="Deployment ID enabled for investigation requests"
    )
    parser.add_argument("--workflow-token-block", default="phi-workflow-request-token")
    parser.add_argument("--anthropic-key-block", default="anthropic-api-key")
    args = parser.parse_args()
    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    with inference_bridge(
        None,
        upstream=args.upstream,
        anthropic_api_key=load_inference_key(args.anthropic_key_block),
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
