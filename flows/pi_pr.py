"""pi proposes a change to one of the operator's repos; it lands as a gardener pull.

the security shape is the point. pi runs in a throwaway clone with an
environment built from scratch and holds no write credential of any kind.
the one credential it can read is the worker's own Codex login in
`~/.pi/agent/auth.json`, a session minted on the box by device-code login
(never copied from a laptop); a hijacked prompt can spend that subscription
and nothing else.
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
import subprocess
import tempfile
from typing import Any, Literal

from mps.pi import minimal_env, run_pi, screen_prompt
from mps.tangled import build_patch, create_pull
from prefect import flow, runtime
from prefect.blocks.system import Secret
from prefect.events import emit_event
from pydantic import BaseModel, Field

Repo = Literal["my-prefect-server", "find-bufo", "bot", "tangled-mcp"]

OWNER = "zzstoatzz.io"
CLONE_URL = "https://tangled.sh/{owner}/{repo}.git"
APPVIEW = "https://tangled.org"


class Agent(BaseModel):
    """which brain pi gets. luna is the configured openai-codex subscription."""

    provider: str = Field(default="openai-codex", json_schema_extra={"position": 0})
    model: str = Field(default="gpt-5.6-luna", json_schema_extra={"position": 1})
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
    """have pi attempt `task` in `repo` and open a tangled PR as gardener.

    `title` and `body` are the caller's own words and are published verbatim;
    `requested_by` names who asked, and is appended to the body so the pull
    says whose intent it carries even though gardener signs the record.
    """
    agent = agent or Agent()
    anthropic_key = Secret.load("anthropic-api-key").get()
    screen_prompt(task, "full", anthropic_key)

    cwd = tempfile.mkdtemp(prefix="pi-pr-")
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
        task,
        cwd=cwd,
        provider=agent.provider,
        model=agent.model,
        thinking=agent.thinking,
        # pi must edit files and run tests here; it still holds no credential,
        # and everything it produces is reviewed as a patch before merge
        tool_mode="full",
        env=env,
    )

    patch = build_patch(cwd, base, title, "gardener", email="gardener@zat.dev")
    if not patch:
        print("pi made no changes — nothing to propose")
        return {"changed": False, "output": output}

    print(f"patch: {len(patch)} bytes")
    if dry_run:
        return {"changed": True, "dry_run": True, "patch_bytes": len(patch)}

    if requested_by:
        body = f"{body}\n\nrequested by {requested_by}; implemented by pi, published by gardener."
    handle = Secret.load("gardener-handle").get()
    password = Secret.load("gardener-password").get()
    pull = create_pull(OWNER, repo, title, patch, body, handle, password)
    print(f"pull created: {pull['uri']}")
    this_run = runtime.flow_run.id
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
