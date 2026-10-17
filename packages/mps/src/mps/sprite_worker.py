"""Run the Sprite worker on heavypad with local inference-grant ownership."""

import argparse
import asyncio
import os
from pathlib import Path

from prefect_sprites import SpritesWorker

from mps.worker_grants import grant_environment


def pi_worker(*, pool: str, database: Path, inference_url: str) -> SpritesWorker:
    environment, release = grant_environment(database=database, inference_url=inference_url)
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
    from mps.blocks import secret_sync

    os.environ["SPRITE_TOKEN"] = secret_sync(args.credentials_block)

    worker = pi_worker(pool=args.pool, database=args.database, inference_url=args.inference_url)
    asyncio.run(worker.start())
