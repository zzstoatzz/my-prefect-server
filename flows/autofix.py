"""a failed flow run triggers pi to diagnose it, and optionally propose a fix.

rungs one and two of the autofix ladder (docs/autofix-design.md). the
automation hands this flow the failed run's id; the flow — not pi — pulls the
run's state, logs, and task tracebacks with the orchestrator credential,
renders a brief, and runs pi in a fresh clone with read-only tools. pi's
diagnosis is emitted as `autofix.diagnosed` so an automation can forward it
to discord.

with `propose=True`, a second pi round gets full tools in a fresh clone of
main and attempts the fix; the flow builds the patch and publishes it as a
tangled pull authored by gardener, the maintenance identity dep-bump already
uses — pi holds no credential either round. the automation passes
propose=false until the operator has reviewed a few manual proposals.

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
# gardener.pds.zat.dev — the maintenance identity that authors autofix pulls
GARDENER_DID = "did:plc:7vx7exykq2zfxjxxejovrymi"
PULL_PREFIX = f"at://{GARDENER_DID}/sh.tangled.repo.pull/"
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

FIX_PROMPT = """\
a prefect flow run failed; a read-only diagnosis of it follows. the working
directory is a fresh clone of the repo at current main. implement the fix the
diagnosis proposes (or a better one the code supports), with the smallest
change that resolves the cause. add or extend a regression test when the fix
is testable. run the test suite for what you touched (`uv run pytest <paths>`)
before finishing.

read CLAUDE.md at the repo root first and follow its conventions. when a
pr-authoring skill is loaded, compose TITLE and NOTE by its prose rules; the
flow — not you — publishes the pull (patch-based, no gh), so only
composition guidance applies, not git/gh mechanics.

your last lines must be exactly:
TITLE: <the PR title>
NOTE: <the PR body>

if the diagnosis is wrong, the fix is already on main, or you cannot fix it
safely, change nothing and end with `NO-CHANGE: <reason>` instead.

=== diagnosis ===
{diagnosis}

=== failed run ===
{brief}
"""

PR_REPO = "my-prefect-server"
PR_OWNER = "zzstoatzz.io"

# canonical skill sources, cloned at runtime and passed to pi via --skill —
# the same files the operator's local tooling uses, never a paraphrase.
# sources must be publicly clonable: fetch_skills runs with a from-scratch
# env, and a private source "working" here would mean it leaked in through
# ambient worker credentials.
SKILL_SOURCES: dict[str, tuple[str, str]] = {
    "pr-body": ("https://tangled.sh/zzstoatzz.io/skills.git", "pr-body"),
}


def fetch_skills(workdir: str) -> list[str]:
    paths = []
    for name, (url, subpath) in SKILL_SOURCES.items():
        dest = os.path.join(workdir, f"skill-{name}")
        subprocess.run(
            ["git", "clone", "--depth", "1", url, dest],
            check=True,
            capture_output=True,
            text=True,
            env=minimal_env(),
        )
        paths.append(os.path.join(dest, subpath))
    return paths


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


def trailers(output: str, keys: tuple[str, ...]) -> dict[str, str]:
    """parse KEY: blocks from the end of pi's output.

    a value runs from its KEY: line until the next known key (or the end),
    so a multi-line NOTE — the pr-body skill produces those — survives.
    """
    values: dict[str, list[str]] = {}
    current: str | None = None
    for line in output.strip().splitlines():
        head = line.split(":", 1)[0]
        if head in keys and line.startswith(f"{head}:"):
            current = head
            values[current] = [line[len(head) + 1 :].strip()]
        elif current is not None:
            values[current].append(line)
    return {k: "\n".join(v).strip() for k, v in values.items()}


def trailer(output: str, key: str, *, siblings: tuple[str, ...] = ()) -> str:
    return trailers(output, (key, *siblings)).get(key, "")


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


def propose_fix(
    diagnosis: str, brief: str, anthropic_key: str, dep_name: str
) -> dict[str, str]:
    """second pi round: full tools in a clone of main; the flow publishes the patch."""
    from mps.tangled import build_patch, create_pull

    prompt = FIX_PROMPT.format(diagnosis=diagnosis, brief=brief)
    screen_prompt(prompt, "full", anthropic_key)
    with TemporaryDirectory(prefix="autofix-fix-") as workdir:
        cwd = os.path.join(workdir, "repo")
        env = minimal_env(ANTHROPIC_API_KEY=anthropic_key)
        subprocess.run(
            ["git", "clone", "--depth", "1", REPO_URL, cwd],
            check=True,
            capture_output=True,
            text=True,
            env=minimal_env(),
        )
        skills = fetch_skills(workdir)
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        output = run_pi(
            prompt,
            cwd=cwd,
            provider="anthropic",
            thinking="medium",
            tool_mode="full",
            env=env,
            skills=skills,
        ).strip()
        parsed = trailers(output, ("TITLE", "NOTE", "NO-CHANGE"))
        if reason := parsed.get("NO-CHANGE"):
            return {"reason": reason}
        title = (parsed.get("TITLE") or f"autofix: {dep_name}").splitlines()[0]
        note = parsed.get("NOTE", "")
        patch = build_patch(cwd, base, title, "gardener", email="gardener@zat.dev")
    if not patch:
        return {"reason": "pi made no changes"}

    body = "\n\n".join(
        part
        for part in (
            note,
            f"proposed by autofix for a failed `{dep_name}` run. full diagnosis in the autofix run logs.",
            diagnosis,
        )
        if part
    )
    handle = Secret.load("gardener-handle").get()
    password = Secret.load("gardener-password").get()
    pull = create_pull(PR_OWNER, PR_REPO, title, patch, body, handle, password)
    return {"title": title, **pull}


@flow(name="autofix", log_prints=True, timeout_seconds=2400)
def autofix(
    flow_run_id: UUID, dry_run: bool = False, propose: bool = False
) -> Completed:
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
        if not propose:
            return Completed(name="Diagnosed", message=summary)

        result = propose_fix(diagnosis, brief, anthropic_key, dep_name)
        if "url" not in result:
            print(f"no proposal: {result.get('reason')}")
            return Completed(name="Diagnosed", message=summary)
        emit_event(
            event="autofix.proposed",
            resource={
                "prefect.resource.id": f"autofix.{flow_run_id}",
                "prefect.resource.name": f"{dep_name} / {ctx['run'].name}",
            },
            payload={
                "deployment": dep_name,
                "summary": summary,
                "title": result["title"],
                "pr_url": result["url"],
                "autofix_url": ui_url("flow-runs", this_run) if this_run else "",
            },
        )
        return Completed(
            name="Proposed", message=f"{result['title']} — {result['url']}"
        )
    except Exception:  # noqa: BLE001 — a failing autofix would trigger itself
        err = traceback.format_exc()
        print(err)
        return Completed(name="Degraded", message=err[-500:])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("flow_run_id", type=UUID)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--propose", action="store_true")
    args = parser.parse_args()
    autofix(args.flow_run_id, dry_run=args.dry_run, propose=args.propose)
