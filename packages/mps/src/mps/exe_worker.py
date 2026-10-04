"""Run the exe.dev worker on heavypad with local inference-grant ownership."""

import argparse
import asyncio
from pathlib import Path

from prefect_exe import ExeWorker

from mps.sprite_worker import grant_environment

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", default="gardener-exe")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--inference-url", required=True)
    args = parser.parse_args()
    environment, release = grant_environment(
        database=args.database, inference_url=args.inference_url
    )
    worker = ExeWorker(
        work_pool_name=args.pool,
        name="pi-exe-worker",
        run_environment=environment,
        release_environment=release,
    )
    asyncio.run(worker.start())
