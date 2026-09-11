"""Gardener proposes changes using the Pi harness and authors the resulting pull.

Pi runs in an isolated Sprite workspace without provider or publishing
credentials. Inference uses the run-scoped Aperture bridge.
the flow — code an injected prompt cannot rewrite — is what turns its work
into a patch and publishes the pull record as gardener, the maintenance
identity every automated pull uses. worst case, a confused or hijacked pi
produces a bad diff that sits in a PR the operator reviews. the pull emits
`autofix.proposed`, so phi reviews it like any other gardener pull.

tangled pulls are patch-based: the changeset is gzipped, uploaded as a blob
on the *author's* PDS, and referenced from a sh.tangled.repo.pull record. no
push access to the target repo is needed, so nothing here can write to a repo.
"""

import argparse
import hashlib
import subprocess
import tempfile
from typing import Any, Literal

from mps.blocks import secret_sync
from mps.pi import minimal_env, run_pi, screen_prompt
from mps.tangled import build_patch, create_pull
from prefect import flow
from prefect.artifacts import create_table_artifact
from prefect.events import emit_event
from prefect.runtime import flow_run as run_context
from pydantic import BaseModel, Field

Repo = Literal["my-prefect-server", "find-bufo", "bot", "tangled-mcp"]

OWNER = "zzstoatzz.io"
CLONE_URL = "https://tangled.sh/{owner}/{repo}.git"
APPVIEW = "https://tangled.org"


class Agent(BaseModel):
    """The Aperture model authorized by the execution grant."""

    provider: Literal["aperture"] = Field(default="aperture", json_schema_extra={"position": 0})
    model: str | None = Field(default=None, json_schema_extra={"position": 1})
    thinking: Literal["off", "minimal", "low", "medium", "high", "xhigh"] = Field(
        default="medium", json_schema_extra={"position": 2}
    )


@flow(name="pi-pr", log_prints=True, timeout_seconds=2400)
def pi_pr(
    task: str,
    title: str,
    body: str,
    repo: Repo = "my-prefect-server",
    agent: Agent | None = None,
    dry_run: bool = False,
    requested_by: str = "",
) -> dict[str, Any]:
    """Have Gardener attempt `task` using Pi and author a Tangled pull.

    `title` and `body` are the caller's own words and are published verbatim;
    `requested_by` names who asked, and is appended to the body so the pull
    says whose intent it carries even though gardener signs the record.
    """
    agent = agent or Agent()
    anthropic_key = secret_sync("anthropic-api-key")
    screen_prompt(
        task,
        "full",
        anthropic_key,
        inputs={
            "repo": repo,
            "title": title,
            "body": body,
            "requested_by": requested_by,
            "dry_run": dry_run,
        },
    )

    with tempfile.TemporaryDirectory(prefix="pi-pr-") as cwd:
        env = minimal_env()

        url = CLONE_URL.format(owner=OWNER, repo=repo)
        print(f"cloning {url} into {cwd}")
        subprocess.run(
            ["git", "clone", "--depth", "1", url, cwd],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        output = run_pi(
            "You are Gardener (gardener.pds.zat.dev), the maintenance agent. "
            "You use the Pi harness; the trusted workflow publishes your patch.\n\n" + task,
            cwd=cwd,
            provider=agent.provider,
            model=agent.model,
            thinking=agent.thinking,
            # pi must edit files and run tests here; it still holds no credential,
            # and everything it produces is reviewed as a patch before merge
            tool_mode="full",
        )

        patch = build_patch(cwd, base, title, "gardener", email="gardener@zat.dev")
        if not patch:
            print("Gardener made no changes — nothing to propose")
            return {"changed": False, "output": output}

        print(f"patch: {len(patch)} bytes")
        if dry_run:
            digest = hashlib.sha256(patch.encode()).hexdigest()
            artifact_id = create_table_artifact(
                key="pi-proposed-patch",
                table=[{"repo": repo, "base": base, "sha256": digest, "patch": patch}],
                description="Unpublished Gardener patch for review",
            )
            return {
                "changed": True,
                "dry_run": True,
                "patch_bytes": len(patch.encode()),
                "patch_sha256": digest,
                "base": base,
                "artifact_id": str(artifact_id),
            }

        if requested_by:
            body = f"{body}\n\nrequested by {requested_by}; implemented by gardener using the Pi harness; published by the trusted workflow as gardener."
        handle = secret_sync("gardener-handle")
        password = secret_sync("gardener-password")
        pull = create_pull(OWNER, repo, title, patch, body, handle, password)
        print(f"pull created: {pull['uri']}")
        this_run = run_context.id
        emit_event(
            event="autofix.proposed",
            resource={
                "prefect.resource.id": f"autofix.{this_run or pull['uri'].rsplit('/', 1)[-1]}",
                "prefect.resource.name": f"pi-pr / {repo}",
            },
            payload={
                "deployment": f"pi-pr ({repo})",
                "summary": task[:240],
                "title": title,
                "pull": pull["uri"],
                "pr_url": pull["url"],
                "autofix_url": "",
            },
        )
        return {"changed": True, **pull}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--title", default="")
    parser.add_argument("--body", default="")
    parser.add_argument("--repo", default="my-prefect-server")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    pi_pr(args.task, args.title, args.body, repo=args.repo, dry_run=args.dry_run)
