"""Run the Sprite worker on heavypad with local inference-grant ownership."""

import argparse
import asyncio
import os
from pathlib import Path
from urllib.parse import urlsplit

from prefect.client.schemas.objects import FlowRun
from prefect_sprites import SpritesWorker

from mps.inference_grants import InferenceGrants
from mps.inference_models import resolve_inference_model


def pi_worker(*, pool: str, database: Path, inference_url: str) -> SpritesWorker:
    endpoint = urlsplit(inference_url)
    if endpoint.scheme != "https" or not endpoint.hostname or endpoint.username:
        raise ValueError("Inference endpoint must use HTTPS")
    grants = InferenceGrants(database)

    async def environment(attempt: str, timeout: int, flow_run: FlowRun) -> dict[str, str]:
        agent = flow_run.parameters.get("agent") or {}
        if not isinstance(agent, dict):
            raise ValueError("Invalid agent configuration")
        selected = resolve_inference_model(agent.get("model"))
        token = grants.acquire(
            attempt,
            model=selected.name,
            lifetime=min(timeout + 300, 86400),
            request_limit=32,
        )
        return {
            "PHI_INFERENCE_URL": inference_url,
            "PHI_INFERENCE_TOKEN": token,
            "PHI_INFERENCE_MODEL": selected.name,
        }

    async def release(attempt: str) -> None:
        grants.revoke(attempt)

    return SpritesWorker(
        work_pool_name=pool,
        name="pi-sprites-worker",
        run_environment=environment,
        release_environment=release,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", default="phi-sprites-spike")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--inference-url", required=True)
    parser.add_argument("--credentials-block", default="phi-sprites-api-token")
    args = parser.parse_args()
    from prefect.blocks.system import Secret

    os.environ["SPRITE_TOKEN"] = Secret.load(args.credentials_block).get()

    worker = pi_worker(pool=args.pool, database=args.database, inference_url=args.inference_url)
    asyncio.run(worker.start())
