"""a failed flow run triggers pi to diagnose it, and optionally propose a fix.

rungs one and two of the autofix ladder (docs/autofix.md). the
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
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID

from mps import tangled
from mps.blocks import secret_sync
from mps.pi import minimal_env, run_pi, screen_prompt
from prefect import flow, get_client
from prefect.client.schemas.filters import (
    FlowRunFilter,
    FlowRunFilterId,
    LogFilter,
    LogFilterFlowRunId,
)
from prefect.client.schemas.sorting import LogSort
from prefect.events import emit_event
from prefect.events.filters import EventFilter, EventNameFilter, EventOccurredFilter
from prefect.exceptions import ObjectNotFound
from prefect.runtime import flow_run as run_context
from prefect.states import Completed, State

REPO_URL = "https://github.com/zzstoatzz/my-prefect-server.git"
# home-pool runs this flow on the mps wheel frozen before the Sprite migration
# (the autofix command in prefect.yaml); that wheel predates these names and
# review_verdict's round arguments, so both are resolved at runtime.
GARDENER_DID = getattr(tangled, "GARDENER_DID", "did:plc:7vx7exykq2zfxjxxejovrymi")
PHI_DID = getattr(tangled, "PHI_DID", "did:plc:65sucjiel52gefhcdcypynsr")
PULL_PREFIX = f"at://{GARDENER_DID}/sh.tangled.repo.pull/"
LOG_LINES = 150
SUMMARY_LIMIT = 240
PROPOSAL_WINDOW = timedelta(days=7)
RECENT_COMMITS = 30
VERDICT_EXCERPT = 400

PROMPT = """\
you are gardener (gardener.pds.zat.dev), using the Pi harness to diagnose a failed prefect flow run for the operator of this repo.
the working directory is main near the run's start time (or failure time when
startup failed), with recent history. deployment pins may differ; do not assume
this is the exact executed revision. you have read-only tools: read the flow's entrypoint and whatever it
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

the gardener-history skill is loaded; `.gardener/context.md` in the working
directory holds what happened since this run's revision. read it first and
follow the skill; `FIXED-ON-MAIN: <commit>` as your last line means no pull
will be opened.

=== failed run ===
{brief}
"""

FIX_PROMPT = """\
you are gardener (gardener.pds.zat.dev), implementing a fix using the Pi harness.
a prefect flow run failed; a read-only diagnosis of it follows. the working
directory is a fresh clone of the repo at current main. implement the fix the
diagnosis proposes (or a better one the code supports), with the smallest
change that resolves the cause. add or extend a regression test when the fix
is testable. run the test suite for what you touched (`uv run pytest <paths>`)
before finishing.

read AGENTS.md at the repo root first and follow its conventions. the
gardener-history skill is loaded and `.gardener/context.md` holds what has
already landed or been proposed; follow it before changing anything. when a
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
    "gardener-history": ("https://tangled.sh/zzstoatzz.io/skills.git", "gardener-history"),
}
CONTEXT_FILE = ".gardener/context.md"


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
        deployment = None
        if run.deployment_id:
            # Deleted deployments must not hide the surviving run and logs.
            with suppress(ObjectNotFound):
                deployment = await client.read_deployment(run.deployment_id)
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


def match_proposal(events: list[Any], dep_name: str, sha: str) -> str | None:
    """the pull url of an earlier proposal for this deployment at this sha, unless
    a later merge.merged names that pull."""
    merged = {e.payload.get("pull") for e in events if e.event == "merge.merged" and e.payload}
    for e in sorted(events, key=lambda e: e.occurred, reverse=True):
        p = e.payload or {}
        if (
            e.event == "autofix.proposed"
            and p.get("deployment") == dep_name
            and p.get("sha") == sha
            and p.get("pull") not in merged
        ):
            return p.get("pr_url") or p.get("pull")
    return None


async def prior_proposal(dep_name: str, sha: str) -> str | None:
    """one gardener pull per (deployment, sha): a second failure on the same
    revision points at the pull that is already open instead of opening another."""
    return match_proposal(await proposal_events(), dep_name, sha)


def ui_url(kind: str, id_: UUID) -> str:
    base = os.environ.get("PREFECT_API_URL", "").removesuffix("/api")
    return f"{base}/{kind}/{kind[:-1]}/{id_}"


def checkout_as_of(cwd: str, when) -> str:
    """Clone main at an approximate run time; this does not resolve deployment pins."""
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


def recent_changes(cwd: str, sha: str) -> str:
    """commits main gained after the run's revision, newest first. the clone's
    main is as of the clone, so this is what pi's checkout cannot see."""
    env = minimal_env()
    log = subprocess.run(
        [
            "git",
            "log",
            "--format=%h %ad %s",
            "--date=short",
            "-n",
            str(RECENT_COMMITS),
            f"{sha}..main",
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()
    return log or "none"


def describe_proposals(events: list[Any], dep_name: str, verdict_of) -> str:
    """one line per gardener pull proposed for this deployment, newest round first,
    with phi's latest verdict; `verdict_of(pull_uri)` returns (round, verdict, text)."""
    latest: dict[str, Any] = {}
    for e in sorted(events, key=lambda e: e.occurred):
        p = e.payload or {}
        if e.event == "autofix.proposed" and p.get("deployment") == dep_name and p.get("pull"):
            latest[p["pull"]] = p
    lines = []
    for uri, p in reversed(list(latest.items())):
        try:
            rounds, verdict, text = verdict_of(uri)
        except Exception as exc:  # the PDS read is best effort; the pull still gets listed
            rounds, verdict, text = "?", "unknown", f"(verdict unavailable: {exc})"
        excerpt = " ".join(text.split())[:VERDICT_EXCERPT]
        lines.append(
            f"  - {p.get('title', '')} — {p.get('pr_url') or uri} (round {rounds}, sha {str(p.get('sha', ''))[:7] or '?'})"
            f"\n    phi: {verdict}: {excerpt}"
        )
    return "\n".join(lines) or "  none"


def phi_verdict(pull_uri: str) -> tuple[int, str, str]:
    info = tangled.pull_patch(pull_uri)
    rounds = info.get("rounds") or 1
    rounds = len(rounds) if isinstance(rounds, list) else int(rounds)
    try:
        found = tangled.review_verdict(
            pull_uri, PHI_DID, round_index=rounds - 1, expected_cid=info["cid"]
        )
    except (TypeError, KeyError):
        found = tangled.review_verdict(pull_uri, PHI_DID)
    if not found:
        return rounds, "pending", "no verdict on the latest round yet"
    return rounds, found["verdict"], found["text"]


async def proposal_events() -> list[Any]:
    since = datetime.now(UTC) - PROPOSAL_WINDOW
    events: list[Any] = []
    async with get_client() as client:
        page = await client.read_events(
            EventFilter(
                occurred=EventOccurredFilter.model_validate({"since": since}),
                event=EventNameFilter(name=["autofix.proposed", "merge.merged"]),
            ),
            limit=50,
        )
        events.extend(page.events)
        while page.next_page:
            page = await client.read_events_page(str(page.next_page))
            events.extend(page.events)
    return events


def prior_work(dep_name: str) -> str:
    try:
        events = asyncio.run(proposal_events())
    except Exception as exc:
        return f"prior gardener proposals for {dep_name}: unavailable ({exc})"
    return (
        f"prior gardener proposals for {dep_name} (last {PROPOSAL_WINDOW.days} days):\n"
        + describe_proposals(events, dep_name, phi_verdict)
    )


def since_section(cwd: str, sha: str, dep_name: str) -> str:
    """the 'what happened since' part of the brief; a lookup failure degrades
    the section, never the diagnosis."""
    try:
        commits = recent_changes(cwd, sha)
    except Exception as exc:
        commits = f"unavailable ({exc})"
    return f"commits on main since this run's revision ({sha[:7]}):\n{commits}\n\n{prior_work(dep_name)}"


def write_context(cwd: str, text: str) -> str:
    """drop the since-section into the clone where the gardener-history skill
    tells pi to look; pi has no network, so the flow carries these facts in."""
    path = os.path.join(cwd, CONTEXT_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("# what happened since\n\n" + text + "\n")
    return path


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
    missing_deployment = (
        "(deleted or unavailable)" if getattr(run, "deployment_id", None) else "(none)"
    )
    lines = [
        f"flow run: {run.name} ({run.id})",
        f"state: {run.state.name if run.state else '?'} — {run.state.message if run.state else ''}",
        f"deployment: {dep.name if dep else missing_deployment}",
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
    diagnosis: str, brief: str, anthropic_key: str, dep_name: str, since: str = ""
) -> dict[str, str]:
    """second pi round: full tools in a clone of main; the flow publishes the patch."""
    from mps.tangled import build_patch, create_pull

    prompt = FIX_PROMPT.format(diagnosis=diagnosis, brief=brief)
    screen_prompt(prompt, "full", anthropic_key)
    with TemporaryDirectory(prefix="autofix-fix-") as workdir:
        cwd = os.path.join(workdir, "repo")
        subprocess.run(
            ["git", "clone", "--depth", "1", REPO_URL, cwd],
            check=True,
            capture_output=True,
            text=True,
            env=minimal_env(),
        )
        skills = fetch_skills(workdir)
        write_context(cwd, since or f"(no record of prior work for {dep_name})")
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
            provider="aperture",
            thinking="medium",
            tool_mode="full",
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
    handle = secret_sync("gardener-handle")
    password = secret_sync("gardener-password")
    pull = create_pull(PR_OWNER, PR_REPO, title, patch, body, handle, password)
    return {"title": title, **pull}


@flow(name="autofix", log_prints=True, timeout_seconds=2400)
def autofix(
    flow_run_id: UUID,
    dry_run: bool = False,
    propose: bool = False,
    propose_for: list[str] | None = None,
) -> State:
    """diagnose a failed run; open a pull for it when `propose` is on, or when
    the failed deployment is named in `propose_for` (the canary allowlist the
    automation carries while proposing is still earning trust)."""
    try:
        ctx = asyncio.run(gather(flow_run_id))
        dep_name = ctx["deployment"].name if ctx["deployment"] else "ad-hoc"
        if dep_name == "autofix":
            return Completed(name="Skipped", message="not diagnosing my own failures")
        propose = propose or dep_name in (propose_for or [])

        brief = render(ctx)
        print(brief)
        if dry_run:
            return Completed(name="DryRun", message="brief rendered, pi not run")

        anthropic_key = secret_sync("anthropic-api-key")
        prompt = PROMPT.format(brief=brief)
        screen_prompt(prompt, "read-only", anthropic_key)

        with TemporaryDirectory(prefix="autofix-") as workdir:
            cwd = os.path.join(workdir, "repo")
            skills = fetch_skills(workdir)
            failed_run = ctx["run"]
            when = (
                failed_run.start_time
                or (failed_run.state.timestamp if failed_run.state else None)
                or failed_run.created
            )
            sha = checkout_as_of(cwd, when)
            print(f"diagnosing against {sha}")
            since = since_section(cwd, sha, dep_name)
            print(since)
            write_context(cwd, since)
            diagnosis = run_pi(
                prompt,
                cwd=cwd,
                provider="aperture",
                thinking="medium",
                tool_mode="read-only",
                skills=skills,
            ).strip()

        summary, _ = split_summary(diagnosis)
        this_run = run_context.id
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
        if fixed := trailers(diagnosis, ("FIXED-ON-MAIN",)).get("FIXED-ON-MAIN"):
            print(f"not proposing: already fixed on main ({fixed})")
            return Completed(name="Diagnosed", message=f"{summary}\nfixed on main: {fixed}")
        if existing := asyncio.run(prior_proposal(dep_name, sha)):
            print(f"not proposing again: {existing} is open for {dep_name} at {sha}")
            return Completed(name="Diagnosed", message=f"{summary}\nopen pull: {existing}")

        result = propose_fix(diagnosis, brief, anthropic_key, dep_name, since)
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
                "sha": sha,
                "summary": summary,
                "title": result["title"],
                "pull": result["uri"],
                "pr_url": result["url"],
                "autofix_url": ui_url("flow-runs", this_run) if this_run else "",
            },
        )
        return Completed(name="Proposed", message=f"{result['title']} — {result['url']}")
    except Exception:
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
