"""Run the Gardener worker with VM-attached exe.dev inference."""

import argparse
import asyncio
import re
from pathlib import Path

from prefect_exe import ExeWorker

from mps.inference_models import resolve_inference_model


def integration_environment(integration: str):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", integration):
        raise ValueError("Invalid Exe integration name")

    async def environment(attempt, timeout, flow_run):
        agent = flow_run.parameters.get("agent") or {}
        if not isinstance(agent, dict):
            raise ValueError("Invalid agent configuration")
        selected = resolve_inference_model(agent.get("model"), backend="exe")
        return {
            "PHI_INFERENCE_BACKEND": "exe",
            "PHI_INFERENCE_URL": f"https://{integration}.int.exe.xyz",
            "PHI_INFERENCE_MODEL": selected.name,
            "PHI_INFERENCE_TOKEN": "",
        }

    return environment


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", default="gardener-exe")
    parser.add_argument("--integration", default="llm")
    parser.add_argument("--observations", type=Path, required=True)
    args = parser.parse_args()
    worker = ExeWorker(
        work_pool_name=args.pool,
        name="pi-exe-worker",
        integrations=(args.integration,),
        run_environment=integration_environment(args.integration),
        observations_path=args.observations,
    )
    asyncio.run(worker.start())
