"""a phi-approved gardener pull lands when the operator resumes this run.

stage 2 of the approval ladder in docs/autofix-design.md: gardener authors,
phi reviews, and the operator's part shrinks to one press of Resume. this
flow holds the only merge credential (`tangled-merge-ssh-key`, a deploy key
the operator registers on tangled) and nothing that runs pi ever sees it.

the shape, per pull:

1. wait for phi's `VERDICT:` comment on the pull. request-changes and
   escalate end the run named after the verdict; nothing merges.
2. clone the target from the knot, `git am` the latest round, run the
   repo's tests on the worker. a failing patch ends the run as Tests-Failed
   and the discord line says so; the pull stays open for the revise loop.
3. emit `merge.awaiting-approval` (-> discord, with the run link and the
   paths touched, flagging protected ones) and suspend. the worker slot is
   freed; the run sits Paused for up to a day.
4. Resume in the prefect ui reschedules the run. it re-clones, re-applies
   and re-tests against whatever main is now, then pushes to the knot (and
   the github mirror for repos whose worker installs from it), writes the
   merged status record as the operator, and emits `merge.merged`.

everything before the suspend is idempotent, because a resumed run starts
from the top; the pause key on the run's policy is how the second pass knows
it already asked.
"""

import argparse
import os
import subprocess
import tempfile
import time
from typing import Literal

from mps.pi import minimal_env
from mps.tangled import (
    mark_pull_merged,
    pull_patch,
    repo_name_for_did,
    review_verdict,
    touched_paths,
)
from prefect import flow, runtime, task
from prefect.blocks.system import Secret
from prefect.client.orchestration import get_client
from prefect.events import emit_event
from prefect.flow_runs import suspend_flow_run
from prefect.states import Completed

from flows.autofix import PULL_PREFIX
from flows.costs import OPERATOR_CREDS_BLOCK

OPERATOR_DID = "did:plc:xbtmt2zjwlrfegqvch7fboei"
PHI_DID = "did:plc:65sucjiel52gefhcdcypynsr"
KNOT = "git@tangled.org:zzstoatzz.io/{repo}"
UI_RUN_URL = "https://prefect-server.waow.tech/runs/flow-run/{id}"
PAUSE_KEY = "operator-merge-approval"
APPROVAL_TIMEOUT_SECONDS = 86_400

# repos whose flows install from the github mirror: the knot alone is not
# enough, prod would keep running the old code
MIRRORS = {"my-prefect-server": "https://github.com/zzstoatzz/my-prefect-server.git"}

TEST_COMMANDS: dict[str, tuple[list[str], dict[str, str]]] = {
    "bot": (
        ["sh", "-c", "uv sync --frozen && uv run pytest -q"],
        {"BLUESKY_HANDLE": "ci.invalid", "BLUESKY_PASSWORD": "ci"},
    ),
}
DEFAULT_TEST_COMMAND: tuple[list[str], dict[str, str]] = (
    ["sh", "-c", "uv sync && uv run pytest -q"],
    {},
)

# paths whose changes always get the operator's eyes on the diff itself, not
# just phi's verdict. the discord line flags them; nothing is auto-merged
# either way today, so this is a label, and the list is where stage 3's
# "human-merge forever" set will live
PROTECTED_PATHS: dict[str, tuple[str, ...]] = {
    "bot": ("src/bot/core/policy.py", "personalities/phi.md", "deploy/", "fly.toml"),
    "my-prefect-server": (
        "deploy/",
        "prefect.yaml",
        "packages/mps/",
        ".tangled/",
        "flows/merge_approved.py",
    ),
}

Verdict = Literal["approve", "request-changes", "escalate"]


def protected_touches(repo: str, paths: list[str]) -> list[str]:
    rules = PROTECTED_PATHS.get(repo, ())
    return [
        p
        for p in paths
        if any(p == r or (r.endswith("/") and p.startswith(r)) for r in rules)
    ]


def awaiting_summary(
    title: str, repo: str, paths: list[str], protected: list[str], run_url: str
) -> str:
    lines = [f"{title} ({repo}, {len(paths)} files, phi approved, tests green)"]
    if protected:
        lines.append("protected: " + ", ".join(protected))
    lines.append(f"resume to merge: {run_url}")
    return "\n".join(lines)


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
def wait_for_verdict(pull: str, wait_seconds: int) -> dict[str, str] | None:
    """poll phi's PDS for her VERDICT comment; None if she never speaks."""
    deadline = time.monotonic() + wait_seconds
    while True:
        found = review_verdict(pull, PHI_DID)
        if found or time.monotonic() >= deadline:
            return found
        time.sleep(30)


def _git(cwd: str, *args: str, env: dict[str, str], check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if check and proc.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.strip()[:2000]}")
    return proc.stdout.strip()


def _ssh_env(key_dir: str, key: str) -> dict[str, str]:
    """git env whose ssh uses only the merge key, never the worker's own."""
    key_path = os.path.join(key_dir, "id")
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key.rstrip("\n") + "\n")
    known = os.path.join(key_dir, "known_hosts")
    return minimal_env(
        GIT_COMMITTER_NAME="merge-approved",
        GIT_COMMITTER_EMAIL="merge-approved@zat.dev",
        GIT_SSH_COMMAND=(
            f"ssh -i {key_path} -o IdentitiesOnly=yes "
            f"-o UserKnownHostsFile={known} -o StrictHostKeyChecking=accept-new"
        ),
    )


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
def knot_head(repo: str, env: dict[str, str]) -> str:
    """main's sha on the knot over ssh; proves the merge key works before asking."""
    out = _git(
        tempfile.gettempdir(),
        "ls-remote",
        KNOT.format(repo=repo),
        "refs/heads/main",
        env=env,
    )
    if not out:
        raise RuntimeError(f"no refs/heads/main on {KNOT.format(repo=repo)}")
    return out.split()[0]


@task
def clone_and_apply(repo: str, patch: str, cwd: str, env: dict[str, str]) -> str | None:
    """fresh clone of main with the round applied; the base sha, or None on conflict."""
    _git(
        tempfile.gettempdir(),
        "clone",
        "--depth",
        "50",
        KNOT.format(repo=repo),
        cwd,
        env=env,
    )
    base = _git(cwd, "rev-parse", "HEAD", env=env)
    proc = subprocess.run(
        ["git", "am"],
        cwd=cwd,
        input=patch,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if proc.returncode:
        print(proc.stdout[-2000:], proc.stderr[-2000:])
        return None
    return base


@task
def run_tests(repo: str, cwd: str) -> tuple[bool, str]:
    argv, extra = TEST_COMMANDS.get(repo, DEFAULT_TEST_COMMAND)
    proc = subprocess.run(
        argv,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=minimal_env(**extra),
        check=False,
    )
    tail = proc.stdout[-4000:]
    print(tail)
    return proc.returncode == 0, tail


@task
def push_merge(repo: str, cwd: str, env: dict[str, str]) -> str:
    sha = _git(cwd, "rev-parse", "HEAD", env=env)
    _git(cwd, "push", KNOT.format(repo=repo), "HEAD:refs/heads/main", env=env)
    mirror = MIRRORS.get(repo)
    if mirror:
        token = Secret.load("github-token").get()
        helper = '!f() { echo username=x-access-token; echo "password=$GIT_MIRROR_TOKEN"; }; f'
        _git(
            cwd,
            "-c",
            f"credential.helper={helper}",
            "push",
            mirror,
            "HEAD:refs/heads/main",
            env={**env, "GIT_MIRROR_TOKEN": token},
        )
    return sha


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
def record_merged(pull: str) -> str:
    import json

    raw = Secret.load(OPERATOR_CREDS_BLOCK).get()
    if isinstance(raw, dict) and "handle" not in raw and "value" in raw:
        raw = raw["value"]
    creds = json.loads(raw) if isinstance(raw, str) else raw
    return mark_pull_merged(pull, creds["handle"], creds["password"])


def _already_asked() -> bool:
    """true on the pass after Resume: the pause key is on the run's policy."""
    run_id = runtime.flow_run.id
    if not run_id:
        return False
    with get_client(sync_client=True) as client:
        fr = client.read_flow_run(run_id)
    return PAUSE_KEY in (fr.empirical_policy.pause_keys or set())


@flow(name="merge-approved", log_prints=True, timeout_seconds=3600)
def merge_approved(pull: str, verdict_wait_seconds: int = 1800) -> Completed:
    if not pull.startswith(PULL_PREFIX):
        return Completed(name="Skipped", message=f"not a gardener pull: {pull}")

    verdict = wait_for_verdict(pull, verdict_wait_seconds)
    if verdict is None:
        return Completed(name="No-Verdict", message="phi did not review in time")
    if verdict["verdict"] != "approve":
        return Completed(name=verdict["verdict"].title(), message=verdict["text"][:500])

    details = pull_patch(pull)
    repo = repo_name_for_did(OPERATOR_DID, details["target_repo_did"])
    paths = touched_paths(details["patch"])
    protected = protected_touches(repo, paths)
    print(f"{repo}: {details['title']!r}, {len(paths)} files, protected={protected}")

    resumed = _already_asked()
    run_id = str(runtime.flow_run.id)

    with tempfile.TemporaryDirectory(prefix="merge-key-") as key_dir:
        env = _ssh_env(key_dir, Secret.load("tangled-merge-ssh-key").get())
        try:
            head = knot_head(repo, env)
        except RuntimeError as exc:
            return Completed(
                name="Blocked", message=f"merge key cannot read the knot: {exc}"[:500]
            )

        with tempfile.TemporaryDirectory(prefix="merge-") as cwd:
            base = clone_and_apply(repo, details["patch"], cwd, env)
            if base is None:
                return Completed(
                    name="Stale",
                    message=f"round {details['rounds']} no longer applies to main@{head[:8]}",
                )
            ok, tail = run_tests(repo, cwd)
            if not ok:
                emit_event(
                    event="merge.tests-failed",
                    resource={
                        "prefect.resource.id": f"merge.{run_id}",
                        "prefect.resource.name": repo,
                    },
                    payload={
                        "title": details["title"],
                        "pull": pull,
                        "tail": tail[-1500:],
                    },
                )
                return Completed(name="Tests-Failed", message=tail[-500:])

            if not resumed:
                emit_event(
                    event="merge.awaiting-approval",
                    resource={
                        "prefect.resource.id": f"merge.{run_id}",
                        "prefect.resource.name": repo,
                    },
                    payload={
                        "title": details["title"],
                        "pull": pull,
                        "summary": awaiting_summary(
                            details["title"],
                            repo,
                            paths,
                            protected,
                            UI_RUN_URL.format(id=run_id),
                        ),
                    },
                )
                suspend_flow_run(timeout=APPROVAL_TIMEOUT_SECONDS, key=PAUSE_KEY)

            sha = push_merge(repo, cwd, env)

    status_uri = record_merged(pull)
    emit_event(
        event="merge.merged",
        resource={
            "prefect.resource.id": f"merge.{run_id}",
            "prefect.resource.name": repo,
        },
        payload={
            "title": details["title"],
            "pull": pull,
            "sha": sha,
            "status": status_uri,
        },
    )
    return Completed(message=f"{repo} main -> {sha[:12]} ({details['title']})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("pull")
    args = parser.parse_args()
    print(merge_approved(args.pull))
