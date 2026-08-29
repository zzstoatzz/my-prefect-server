"""a failed flow run triggers pi, read-only, to diagnose it.

first rung of the autofix ladder (docs/autofix-design.md). the automation
hands this flow the failed run's id; the flow — not pi — pulls the run's
state, logs, and task tracebacks with the orchestrator credential, renders a
brief, and runs pi in a fresh clone with read-only tools. pi's diagnosis is
emitted as `autofix.diagnosed` so an automation can forward it to discord.

this flow must never reach Failed: the trigger that starts it fires on any
failed run, so a failing autofix would trigger itself. every error path ends
in Completed(name="Degraded").
"""

import argparse
import asyncio
import subprocess
import tempfile
import traceback
from typing import Any
from uuid import UUID

from mps.pi import minimal_env, run_pi, screen_prompt
from prefect import flow, get_client
from prefect.blocks.system import Secret
from prefect.client.schemas.filters import (
    FlowRunFilter,
    FlowRunFilterId,
    LogFilter,
    LogFilterFlowRunId,
)
from prefect.client.schemas.sorting import LogSort
from prefect.events import emit_event
from prefect.states import Completed

REPO_URL = "https://github.com/zzstoatzz/my-prefect-server.git"
LOG_LINES = 150
DISCORD_LIMIT = 1900

PROMPT = """\
you are diagnosing a failed prefect flow run for the operator of this repo.
the repo checked out in your working directory is the one the run executed.
you have read-only tools: read the flow's entrypoint and whatever it calls.

report, in plain language addressed to the operator, under 250 words:
1. what failed and where (cite file:line where you can)
2. the most likely cause
3. what you would change, as a concrete proposal — you cannot edit anything
4. what you could not determine and would need to ask

do not speculate about credentials or environment variables beyond what the
logs show.

=== failed run ===
{brief}
"""


async def gather(flow_run_id: UUID) -> dict[str, Any]:
    async with get_client() as client:
        run = await client.read_flow_run(flow_run_id)
        deployment = (
            await client.read_deployment(run.deployment_id)
            if run.deployment_id
            else None
        )
        logs = await client.read_logs(
            log_filter=LogFilter(flow_run_id=LogFilterFlowRunId(any_=[flow_run_id])),
            limit=LOG_LINES,
            sort=LogSort.TIMESTAMP_DESC,
        )
        task_runs = await client.read_task_runs(
            flow_run_filter=FlowRunFilter(id=FlowRunFilterId(any_=[flow_run_id])),
            limit=200,
        )
    failed_tasks = [t for t in task_runs if t.state and t.state.type.value == "FAILED"]
    return {
        "run": run,
        "deployment": deployment,
        "logs": list(reversed(logs)),
        "failed_tasks": failed_tasks,
    }


def render(ctx: dict[str, Any]) -> str:
    run = ctx["run"]
    dep = ctx["deployment"]
    lines = [
        f"flow run: {run.name} ({run.id})",
        f"state: {run.state.name if run.state else '?'} — {run.state.message if run.state else ''}",
        f"deployment: {dep.name if dep else '(none)'}",
        f"entrypoint: {dep.entrypoint if dep else '?'}",
        f"parameters: {run.parameters}",
        f"started: {run.start_time}  ended: {run.end_time}",
        "",
        "failed task runs:",
        *(f"  - {t.name}: {t.state.message}" for t in ctx["failed_tasks"]),
        "",
        f"last {len(ctx['logs'])} log lines:",
        *(f"  [{log.level}] {log.message}" for log in ctx["logs"]),
    ]
    return "\n".join(lines)


@flow(name="autofix", log_prints=True, timeout_seconds=1500)
def autofix(flow_run_id: UUID, dry_run: bool = False) -> Completed:
    try:
        ctx = asyncio.run(gather(flow_run_id))
        dep_name = ctx["deployment"].name if ctx["deployment"] else None
        if dep_name == "autofix":
            return Completed(name="Skipped", message="not diagnosing my own failures")

        brief = render(ctx)
        print(brief)
        if dry_run:
            return Completed(name="DryRun", message="brief rendered, pi not run")

        anthropic_key = Secret.load("anthropic-api-key").get()
        prompt = PROMPT.format(brief=brief)
        screen_prompt(prompt, "read-only", anthropic_key)

        env = minimal_env(ANTHROPIC_API_KEY=anthropic_key)
        cwd = tempfile.mkdtemp(prefix="autofix-")
        subprocess.run(
            ["git", "clone", "--depth", "1", REPO_URL, cwd],
            check=True,
            capture_output=True,
            text=True,
            env=minimal_env(),
        )
        diagnosis = run_pi(
            prompt,
            cwd=cwd,
            provider="anthropic",
            thinking="medium",
            tool_mode="read-only",
            env=env,
        ).strip()

        emit_event(
            event="autofix.diagnosed",
            resource={
                "prefect.resource.id": f"autofix.{flow_run_id}",
                "prefect.resource.name": f"{dep_name} / {ctx['run'].name}",
            },
            payload={
                "deployment": dep_name,
                "failed_flow_run_id": str(flow_run_id),
                "diagnosis": diagnosis[:DISCORD_LIMIT],
            },
        )
        return Completed(name="Diagnosed", message=diagnosis[:500])
    except Exception:  # noqa: BLE001 — a failing autofix would trigger itself
        err = traceback.format_exc()
        print(err)
        return Completed(name="Degraded", message=err[-500:])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("flow_run_id", type=UUID)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    autofix(args.flow_run_id, dry_run=args.dry_run)
