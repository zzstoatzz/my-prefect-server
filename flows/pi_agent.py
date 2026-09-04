"""run pi (the coding agent) non-interactively as a prefect flow.

step toward alert-driven agents: hand pi a prompt, a workspace, and a
capability tier; capture what it says. the pydantic models render as a
validated form in the prefect UI (see prefect docs: advanced/form-building).
"""

import argparse
import subprocess
import tempfile
from typing import Literal

from mps.blocks import secret_sync
from mps.pi import TOOL_ARGS, minimal_env, run_pi, screen_prompt
from prefect import flow
from prefect.flow_runs import pause_flow_run
from pydantic import BaseModel, Field

Repo = Literal["my-prefect-server", "find-bufo", "plyr.fm", "bot"]

REPO_URLS: dict[str, str] = {
    "my-prefect-server": "https://github.com/zzstoatzz/my-prefect-server.git",
    "find-bufo": "https://tangled.sh/zzstoatzz.io/find-bufo.git",
    "plyr.fm": "https://github.com/zzstoatzz/plyr.fm.git",
    "bot": "https://github.com/zzstoatzz/bot.git",
}

THINKING = Literal["off", "minimal", "low", "medium", "high", "xhigh"]


class Workspace(BaseModel):
    """where pi works. picking a repo gives it a fresh shallow clone as cwd."""

    repo: Repo | None = Field(
        default=None,
        description="repo pi operates in (fresh shallow clone); empty = scratch dir",
        json_schema_extra={"position": 0},
    )
    ref: str = Field(
        default="main",
        description="branch or tag to check out",
        json_schema_extra={"position": 1},
    )


class Agent(BaseModel):
    """which brain pi gets, and which hands."""

    provider: Literal["anthropic"] = Field(
        default="anthropic",
        description="only anthropic is authed on the worker (via secret block)",
        json_schema_extra={"position": 0},
    )
    model: str | None = Field(
        default=None,
        description="model id (e.g. claude-haiku-4-5-20251001); empty = provider default",
        json_schema_extra={"position": 1},
    )
    thinking: THINKING = Field(default="medium", json_schema_extra={"position": 2})
    tool_mode: Literal["full", "read-only", "none"] = Field(
        default="read-only",
        description=(
            "full = read/bash/edit/write (pi can modify the workspace and run "
            "commands as the worker user); read-only = read,grep,find,ls; "
            "none = pure text"
        ),
        json_schema_extra={"position": 3},
    )


@flow(name="pi-agent", log_prints=True, timeout_seconds=1800)
def pi_agent(
    prompt: str,
    workspace: Workspace = Workspace(),  # noqa: B008
    agent: Agent = Agent(),  # noqa: B008
    timeout_seconds: int = 1500,
) -> str:
    """run `pi -p <prompt>` in the workspace and return its final output.

    pi resolves credentials from provider env vars (ANTHROPIC_API_KEY is
    injected from a secret block by the deployment).
    """
    anthropic_key = secret_sync("anthropic-api-key")
    screen_prompt(prompt, agent.tool_mode, anthropic_key)

    if agent.tool_mode == "full":
        print("tool_mode=full requires human approval — pausing (resume in UI)")
        pause_flow_run(timeout=600)

    env = minimal_env()
    with tempfile.TemporaryDirectory(prefix="pi-agent-") as cwd:
        if workspace.repo:
            url = REPO_URLS[workspace.repo]
            print(f"cloning {url}@{workspace.ref} into {cwd}")
            subprocess.run(
                ["git", "clone", "--depth", "1", "--branch", workspace.ref, url, cwd],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )

        return run_pi(
            prompt,
            cwd=cwd,
            provider=agent.provider,
            model=agent.model,
            thinking=agent.thinking,
            tool_mode=agent.tool_mode,
            env=env,
            timeout_seconds=timeout_seconds,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--repo", choices=list(REPO_URLS))
    parser.add_argument("--model")
    parser.add_argument("--tool-mode", default="read-only", choices=list(TOOL_ARGS))
    args = parser.parse_args()
    pi_agent(
        args.prompt,
        workspace=Workspace(repo=args.repo),
        agent=Agent(model=args.model, tool_mode=args.tool_mode),
    )
