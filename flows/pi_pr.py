"""one-off: pi proposes a change, it lands as a tangled PR, you get a DM.

the security shape is the point. pi runs in a throwaway clone with an
environment built from scratch and holds no write credential of any kind.
the flow — code an injected prompt cannot rewrite — is what turns its work
into a patch, publishes the pull record, and messages you. worst case, a
confused or hijacked pi produces a bad diff that sits in a PR you review.

tangled pulls are patch-based: the changeset is gzipped, uploaded as a blob
on the *author's* PDS, and referenced from a sh.tangled.repo.pull record. no
push access to the target repo is needed, so nothing here can write to a repo.
"""

import argparse
import subprocess
import tempfile
from typing import Any, Literal

from prefect import flow
from prefect.blocks.system import Secret
from pydantic import BaseModel, Field

from mps.atproto import create_bsky_session, send_dm
from mps.pi import minimal_env, run_pi, screen_prompt
from mps.tangled import create_pull

Repo = Literal["my-prefect-server", "find-bufo", "bot", "tangled-mcp"]

OWNER = "zzstoatzz.io"
CLONE_URL = "https://tangled.sh/{owner}/{repo}.git"
APPVIEW = "https://tangled.org"


class Agent(BaseModel):
    """which brain pi gets. luna is the configured openai-codex subscription."""

    provider: str = Field(default="openai-codex", json_schema_extra=dict(position=0))
    model: str = Field(default="gpt-5.6-luna", json_schema_extra=dict(position=1))
    thinking: Literal["off", "minimal", "low", "medium", "high", "xhigh"] = Field(
        default="medium", json_schema_extra=dict(position=2)
    )


def build_patch(cwd: str, base: str, title: str, author: str) -> str:
    """commit whatever pi changed and render it as a git format-patch."""
    subprocess.run(["git", "add", "-A"], cwd=cwd, check=True)
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True
    ).stdout.strip()
    if not status:
        return ""
    subprocess.run(
        ["git", "-c", f"user.name={author}", "-c", f"user.email={author}@users.noreply",
         "commit", "-m", title],
        cwd=cwd,
        check=True,
        capture_output=True,
    )
    return subprocess.run(
        ["git", "format-patch", f"{base}..HEAD", "--stdout"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


@flow(name="pi-pr", log_prints=True, timeout_seconds=2400)
def pi_pr(
    task: str,
    repo: Repo = "my-prefect-server",
    title: str | None = None,
    agent: Agent = Agent(),
    notify: str = OWNER,
    dry_run: bool = False,
) -> dict[str, Any]:
    """have pi attempt `task` in `repo`, open a tangled PR, and DM the link."""
    anthropic_key = Secret.load("anthropic-api-key").get()
    screen_prompt(task, "full", anthropic_key)

    title = title or task.strip().splitlines()[0][:72]
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
        ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, check=True
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

    patch = build_patch(cwd, base, title, "pi")
    if not patch:
        print("pi made no changes — nothing to propose")
        return {"changed": False, "output": output}

    print(f"patch: {len(patch)} bytes")
    if dry_run:
        return {"changed": True, "dry_run": True, "patch_bytes": len(patch)}

    handle = Secret.load("atproto-handle").get()
    password = Secret.load("atproto-password").get()
    body = f"opened by pi ({agent.model}) for task:\n\n> {task}\n\n---\n{output.strip()[-1500:]}"
    pull = create_pull(OWNER, repo, title, patch, body, handle, password)
    print(f"pull created: {pull['uri']}")

    session = create_bsky_session(handle, password)
    msg_id = send_dm(session, notify, f"opened a PR on {repo}: {title}\n{pull['url']}")
    print(f"dm sent to {notify} ({msg_id})")

    return {"changed": True, **pull}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--repo", default="my-prefect-server")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    pi_pr(args.task, repo=args.repo, dry_run=args.dry_run)
