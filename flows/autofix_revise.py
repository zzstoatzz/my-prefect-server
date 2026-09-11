"""revise a gardener-authored pull in response to an operator comment.

rung three of the autofix ladder (docs/autofix.md), the other half of
watch-tangled-pulls: given a pull and the comment that triggered the run,
rebuild the pull's proposed state in a scratch clone, let pi address the
feedback with full tools, and publish the result — a new round on the same
pull when code changed, and always a reply comment, both authored by
gardener. pi holds no credential; the flow publishes.

the comment thread is re-read from the PDS here rather than trusted from the
trigger — the PDS is the authority, the stream is just the wake-up. that
also makes this run self-healing for any comment the stream dropped.
"""

import argparse
import gzip
import subprocess
from tempfile import TemporaryDirectory
from typing import get_args

import httpx
from mps.blocks import secret_sync
from mps.pi import minimal_env, run_pi, screen_prompt
from mps.tangled import (
    DID as OPERATOR_DID,
    append_round,
    build_patch,
    comment_on_pull,
    comment_subject,
    comment_text,
    get_record,
    list_pull_comments,
    parse_verdict,
    repo_name_for_did,
    resolve_pds,
)
from prefect import flow
from prefect.events import emit_event
from prefect.states import Completed, State

from flows.autofix import (
    GARDENER_DID,
    PULL_PREFIX,
    fetch_skills,
    trailers,
)
from flows.pi_pr import CLONE_URL, OWNER, Repo

MAX_ROUNDS = 6
PHI_DID = "did:plc:65sucjiel52gefhcdcypynsr"

PROMPT = """\
you are gardener, revising your own pull request on the operator's repo in
response to their review comment. the working directory is a fresh clone of
main with the pull's latest round applied{applied_note}. you have full tools:
read, edit, run the tests for what you touch.

address the latest operator comment. if it asks for a change, make it. if it
asks a question, answer it. keep the change minimal and in the spirit of the
existing rounds. read CLAUDE.md at the repo root and follow its conventions;
compose prose per the pr-body skill when loaded.

your last lines must be exactly:
REPLY: <your comment back to the operator: what you changed, or the answer.
one to three sentences, plain language, cite file:line where useful>
NOTE: <only if you changed code: one line describing the round for the pull
body. omit the NOTE line entirely when nothing changed>

=== pull ===
title: {title}

{body}

=== conversation (operator comments, oldest first) ===
{thread}

=== latest comment (address this) ===
{latest}
"""


def latest_round_patch(record: dict) -> str:
    """Download the patch from the pull snapshot validated for this review."""
    rounds = record.get("rounds", [])
    if not rounds:
        return ""
    cid = rounds[-1]["patchBlob"]["ref"]["$link"]
    resp = httpx.get(
        f"{resolve_pds(GARDENER_DID)}/xrpc/com.atproto.sync.getBlob",
        params={"did": GARDENER_DID, "cid": cid},
        timeout=60,
    )
    resp.raise_for_status()
    return gzip.decompress(resp.content).decode()


def apply_patch(cwd: str, patch: str) -> bool:
    """git am the round onto the clone; on conflict leave the patch on disk."""
    proc = subprocess.run(
        ["git", "am"],
        cwd=cwd,
        input=patch,
        capture_output=True,
        text=True,
        env=minimal_env(GIT_COMMITTER_NAME="gardener", GIT_COMMITTER_EMAIL="gardener@zat.dev"),
        check=False,
    )
    if proc.returncode == 0:
        return True
    subprocess.run(
        ["git", "am", "--abort"],
        cwd=cwd,
        capture_output=True,
        env=minimal_env(),
        check=False,
    )
    with open(f"{cwd}/PULL.patch", "w") as f:
        f.write(patch)
    return False


@flow(name="autofix-revise", log_prints=True, timeout_seconds=2400)
def autofix_revise(pull: str, comment_uri: str = "") -> State:
    if not pull.startswith(PULL_PREFIX):
        return Completed(name="Skipped", message=f"not a gardener pull: {pull}")

    snapshot = get_record(pull)
    record = snapshot["value"]
    rounds = record.get("rounds", [])
    if len(rounds) >= MAX_ROUNDS:
        return Completed(name="Capped", message=f"{len(rounds)} rounds — take it from here by hand")

    thread = list_pull_comments(OPERATOR_DID, pull)
    if comment_uri:
        reviewer = comment_uri.removeprefix("at://").split("/", 1)[0]
        if reviewer not in (OPERATOR_DID, PHI_DID):
            return Completed(name="Skipped", message="comment is not from an authorized reviewer")
        comment = get_record(comment_uri)["value"]
        if comment_subject(comment) != pull:
            return Completed(name="Skipped", message="comment belongs to a different pull")
        if comment.get("pullRoundIdx") != len(rounds) - 1:
            return Completed(name="Stale", message="comment reviews a different round")
        if reviewer == PHI_DID and (comment.get("subject") or {}).get("cid") != snapshot.get("cid"):
            return Completed(name="Stale", message="Phi reviewed a different pull record")
        if reviewer == PHI_DID and parse_verdict(comment_text(comment)) != "request-changes":
            return Completed(name="Skipped", message="Phi did not request changes")
        latest = {"uri": comment_uri, "text": comment_text(comment)}
    elif thread:
        latest = thread[-1]
    else:
        return Completed(name="Skipped", message="no operator comments on this pull")

    target = record.get("target") or {}
    repo = repo_name_for_did(OPERATOR_DID, target.get("repo", ""))
    if repo not in get_args(Repo):
        return Completed(name="Skipped", message="pull target is outside the coding repo allowlist")
    branch = target.get("branch") or "main"
    if branch.startswith("-"):
        raise ValueError("invalid target branch")

    anthropic_key = secret_sync("anthropic-api-key")
    with TemporaryDirectory(prefix="autofix-revise-") as workdir:
        cwd = f"{workdir}/repo"
        subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "50",
                "--branch",
                branch,
                CLONE_URL.format(owner=OWNER, repo=repo),
                cwd,
            ],
            check=True,
            capture_output=True,
            text=True,
            env=minimal_env(),
        )
        # a round's patch must apply cleanly to the target branch on its own,
        # so the base is main BEFORE the previous round is applied — the new
        # round then carries the whole series, not just the delta
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        patch = latest_round_patch(record)
        applied = apply_patch(cwd, patch) if patch else False

        prompt = PROMPT.format(
            applied_note=""
            if applied
            else " (the round no longer applies to main — its patch is at ./PULL.patch; re-apply its intent first)",
            title=record.get("title", ""),
            body=record.get("body", ""),
            thread="\n\n".join(f"[{c['created_at']}] {c['text']}" for c in thread),
            latest=latest["text"],
        )
        screen_prompt(prompt, "full", anthropic_key)
        output = run_pi(
            prompt,
            cwd=cwd,
            provider="aperture",
            thinking="medium",
            tool_mode="full",
            skills=fetch_skills(workdir),
        ).strip()

        parsed = trailers(output, ("REPLY", "NOTE"))
        reply = parsed.get("REPLY") or "revised — see the new round."
        note = parsed.get("NOTE", "")
        new_patch = build_patch(cwd, base, note or "revision", "gardener", email="gardener@zat.dev")

    handle = secret_sync("gardener-handle")
    password = secret_sync("gardener-password")
    round_n = None
    if new_patch:
        if get_record(pull)["value"].get("rounds", []) != rounds:
            return Completed(name="Stale", message="pull changed while Pi was revising")
        round_n = append_round(
            pull, new_patch, note, handle, password, expected_cid=snapshot["cid"]
        )
        print(f"round {round_n} appended")
    comment_on_pull(pull, reply, handle, password)

    if new_patch:
        emit_event(
            event="autofix.proposed",
            resource={
                "prefect.resource.id": f"autofix.{pull.rsplit('/', 1)[-1]}",
                "prefect.resource.name": record.get("title", pull),
            },
            payload={
                "deployment": f"autofix-revise ({repo})",
                "summary": reply[:240],
                "title": record.get("title", ""),
                "pull": pull,
                "pr_url": f"https://tangled.org/{OWNER}/{repo}/pulls",
                "autofix_url": "",
            },
        )

    emit_event(
        event="autofix.revised",
        resource={
            "prefect.resource.id": f"autofix.{pull.rsplit('/', 1)[-1]}",
            "prefect.resource.name": record.get("title", pull),
        },
        payload={
            "pull": pull,
            "round": round_n or 0,
            "reply": reply[:400],
            "changed": bool(new_patch),
        },
    )
    what = f"round {round_n} + reply" if new_patch else "reply only"
    return Completed(name="Revised", message=f"{what}: {reply[:200]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("pull")
    parser.add_argument("--comment-uri", default="")
    args = parser.parse_args()
    autofix_revise(args.pull, comment_uri=args.comment_uri)
