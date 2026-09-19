#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["prefect>=3"]
# ///
"""Answer the paused email-triage run in your own words.

    PREFECT_API_URL=... PREFECT_API_AUTH_STRING=... ./scripts/triage_reply.py "archive 1 and 3, I'll reply to 2"

Finds the newest Paused run of the email-triage deployment and resumes it
with the text as `instructions`. Refuses when nothing is paused.
"""

import asyncio
import sys

from prefect.client.orchestration import get_client
from prefect.client.schemas.filters import (
    DeploymentFilter,
    DeploymentFilterName,
    FlowRunFilter,
    FlowRunFilterState,
    FlowRunFilterStateType,
)
from prefect.client.schemas.objects import StateType
from prefect.client.schemas.sorting import FlowRunSort
from prefect.flow_runs import resume_flow_run


async def paused_run_id() -> str:
    async with get_client() as client:
        runs = await client.read_flow_runs(
            deployment_filter=DeploymentFilter(name=DeploymentFilterName(any_=["email-triage"])),
            flow_run_filter=FlowRunFilter(
                state=FlowRunFilterState(type=FlowRunFilterStateType(any_=[StateType.PAUSED]))
            ),
            sort=FlowRunSort.START_TIME_DESC,
            limit=1,
        )
    if not runs:
        raise SystemExit("no paused email-triage run")
    return str(runs[0].id)


def main() -> None:
    text = " ".join(sys.argv[1:]).strip()
    if not text:
        raise SystemExit(__doc__)
    run_id = asyncio.run(paused_run_id())
    resume_flow_run(run_id, run_input={"instructions": text})
    print(f"resumed {run_id}")


if __name__ == "__main__":
    main()
