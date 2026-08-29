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
import os
import subprocess
import traceback
from datetime import timedelta
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID

from mps.pi import minimal_env, run_pi, screen_prompt
from prefect import flow, get_client, runtime
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
SUMMARY_LIMIT = 240

PROMPT = """\
you are diagnosing a failed prefect flow run for the operator of this repo.
the working directory is the repo at the commit the run executed, with recent
history. you have read-only tools: read the flow's entrypoint and whatever it
calls; `git log` is available.

your first line must be exactly `SUMMARY: <one sentence, under 200 characters>`
naming the cause and the action you propose. it is all the operator sees in
chat; the rest is read on demand.

then, under 200 words:
1. what failed and where (cite file:line)
2. the most likely cause
3. what you would change — you cannot edit anything
4. what you could not determine

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


def ui_url(kind: str, id_: UUID) -> str:
    base = os.environ.get("PREFECT_API_URL", "").removesuffix("/api")
    return f"{base}/{kind}/{kind[:-1]}/{id_}"


def checkout_as_of(cwd: str, when) -> str:
    """clone main and check out the commit the run's pull step would have seen."""
    env = minimal_env()
    since = (when - timedelta(days=14)).strftime("%Y-%m-%d")
    subprocess.run(
        ["git", "clone", f"--shallow-since={since}", "--branch", "main", REPO_URL, cwd],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    sha = subprocess.run(
        ["git", "rev-list", "-1", f"--before={when.isoformat()}", "main"],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()
    subprocess.run(
        ["git", "checkout", "-q", sha],
        cwd=cwd,
        check=True,
        capture_output=True,
        env=env,
    )
    return sha


def split_summary(diagnosis: str) -> tuple[str, str]:
    first, _, rest = diagnosis.partition("\n")
    if first.upper().startswith("SUMMARY:"):
        return first[len("SUMMARY:") :].strip()[:SUMMARY_LIMIT], rest.strip()
    return first.strip()[:SUMMARY_LIMIT], diagnosis


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

        with TemporaryDirectory(prefix="autofix-") as cwd:
            sha = checkout_as_of(cwd, ctx["run"].start_time)
            print(f"diagnosing against {sha}")
            diagnosis = run_pi(
                prompt,
                cwd=cwd,
                provider="anthropic",
                thinking="medium",
                tool_mode="read-only",
                env=minimal_env(ANTHROPIC_API_KEY=anthropic_key),
            ).strip()

        summary, _ = split_summary(diagnosis)
        this_run = runtime.flow_run.id
        emit_event(
            event="autofix.diagnosed",
            resource={
                "prefect.resource.id": f"autofix.{flow_run_id}",
                "prefect.resource.name": f"{dep_name} / {ctx['run'].name}",
            },
            payload={
                "deployment": dep_name,
                "sha": sha,
                "summary": summary,
                "failed_run_url": ui_url("flow-runs", flow_run_id),
                "autofix_url": ui_url("flow-runs", this_run) if this_run else "",
            },
        )
        return Completed(name="Diagnosed", message=summary)
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
