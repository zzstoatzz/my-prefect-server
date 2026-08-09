"""run pi (the coding agent) non-interactively as a prefect flow.

step toward alert-driven agents: hand pi a prompt, a workspace, and a
capability tier; capture what it says. the pydantic models render as a
validated form in the prefect UI (see prefect docs: advanced/form-building).
"""

import argparse
import os
import shutil
import subprocess
import tempfile
from typing import Literal

from prefect import flow
from prefect.flow_runs import pause_flow_run
from pydantic import BaseModel, Field
from pydantic_ai import Agent as AiAgent
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from mps.spend import record_pydantic_ai_result

Repo = Literal["my-prefect-server", "find-bufo", "plyr.fm", "bot"]

REPO_URLS: dict[str, str] = {
    "my-prefect-server": "https://github.com/zzstoatzz/my-prefect-server.git",
    "find-bufo": "https://tangled.sh/zzstoatzz.io/find-bufo.git",
    "plyr.fm": "https://github.com/zzstoatzz/plyr.fm.git",
    "bot": "https://github.com/zzstoatzz/bot.git",
}

THINKING = Literal["off", "minimal", "low", "medium", "high", "xhigh"]

# pi built-in tool allowlists per capability tier. "read-only" matches the
# allowlist pi's own --help suggests for review tasks.
TOOL_ARGS: dict[str, list[str]] = {
    "full": [],
    "read-only": ["--tools", "read,grep,find,ls"],
    "none": ["--no-tools"],
}


class Verdict(BaseModel):
    allowed: bool
    reason: str


JUDGE_SYSTEM = """\
you screen prompts submitted to an autonomous coding agent that runs on \
private infrastructure. the agent works inside a scratch clone of one of the \
operator's own repositories, diagnosing issues and proposing or making changes.

BLOCK a prompt if it asks the agent to:
- read, print, or transmit credentials, tokens, API keys, or environment variables
- send data anywhere (webhooks, DNS, external hosts) beyond normal package/git traffic
- modify systems outside its working directory (services, cron, ssh, other hosts)
- delete or destroy data, or interfere with the orchestration server or its workers
- obfuscate its own actions or disable logging/safety measures

ALLOW ordinary software work: investigating bugs, reading and explaining code, \
running tests, editing code in the workspace, summarizing alerts and logs.

when genuinely uncertain, block — a false block costs a retry with a clearer \
prompt; a false allow costs much more."""


def screen_prompt(prompt: str, tool_mode: str) -> None:
    """policy judge: cheap model screens intent before pi is launched.

    lives in flow code (not parameters) so it cannot be skipped by whoever
    triggers the run. fails closed: judge errors abort the run.
    """
    model = AnthropicModel(
        "claude-haiku-4-5",
        provider=AnthropicProvider(api_key=os.environ["ANTHROPIC_API_KEY"]),
    )
    judge = AiAgent(
        model, output_type=Verdict, system_prompt=JUDGE_SYSTEM, name="pi-agent-judge"
    )
    result = judge.run_sync(f"tool_mode: {tool_mode}\n\nprompt:\n{prompt}")
    record_pydantic_ai_result(
        task_name="pi_agent_judge",
        model="claude-haiku-4-5",
        result=result,
        metadata={"tool_mode": tool_mode},
    )
    if not result.output.allowed:
        raise ValueError(f"prompt rejected by policy judge: {result.output.reason}")
    print(f"policy judge: allowed ({result.output.reason})")


def minimal_env() -> dict[str, str]:
    """the worker env carries prefect admin credentials and other injected
    secrets; pi (and anything pi runs) must not inherit them."""
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG") if k in os.environ}
    if key := os.environ.get("ANTHROPIC_API_KEY"):
        env["ANTHROPIC_API_KEY"] = key
    return env


class Workspace(BaseModel):
    """where pi works. picking a repo gives it a fresh shallow clone as cwd."""

    repo: Repo | None = Field(
        default=None,
        description="repo pi operates in (fresh shallow clone); empty = scratch dir",
        json_schema_extra=dict(position=0),
    )
    ref: str = Field(
        default="main",
        description="branch or tag to check out",
        json_schema_extra=dict(position=1),
    )


class Agent(BaseModel):
    """which brain pi gets, and which hands."""

    provider: Literal["anthropic"] = Field(
        default="anthropic",
        description="only anthropic is authed on the worker (via secret block)",
        json_schema_extra=dict(position=0),
    )
    model: str | None = Field(
        default=None,
        description="model id (e.g. claude-haiku-4-5-20251001); empty = provider default",
        json_schema_extra=dict(position=1),
    )
    thinking: THINKING = Field(default="medium", json_schema_extra=dict(position=2))
    tool_mode: Literal["full", "read-only", "none"] = Field(
        default="read-only",
        description=(
            "full = read/bash/edit/write (pi can modify the workspace and run "
            "commands as the worker user); read-only = read,grep,find,ls; "
            "none = pure text"
        ),
        json_schema_extra=dict(position=3),
    )


@flow(name="pi-agent", log_prints=True, timeout_seconds=1800)
def pi_agent(
    prompt: str,
    workspace: Workspace = Workspace(),
    agent: Agent = Agent(),
    timeout_seconds: int = 1500,
) -> str:
    """run `pi -p <prompt>` in the workspace and return its final output.

    pi resolves credentials from provider env vars (ANTHROPIC_API_KEY is
    injected from a secret block by the deployment).
    """
    if shutil.which("pi") is None:
        raise RuntimeError(
            "pi is not installed on this worker — "
            "npm install -g @earendil-works/pi-coding-agent"
        )

    screen_prompt(prompt, agent.tool_mode)

    if agent.tool_mode == "full":
        print("tool_mode=full requires human approval — pausing (resume in UI)")
        pause_flow_run(timeout=600)

    env = minimal_env()
    cwd = tempfile.mkdtemp(prefix="pi-agent-")
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

    cmd = ["pi", "--print", "--no-session", "--provider", agent.provider]
    if agent.model:
        cmd += ["--model", agent.model]
    cmd += ["--thinking", agent.thinking]
    cmd += TOOL_ARGS[agent.tool_mode]
    cmd.append(prompt)

    print(f"running: {' '.join(cmd[:-1])} <prompt: {len(prompt)} chars> in {cwd}")
    # pi -p also accepts prompt content piped on stdin and will wait for EOF,
    # so an inherited open stdin (e.g. under the systemd worker) hangs it
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=timeout_seconds,
        stdin=subprocess.DEVNULL,
        env=env,
    )
    if result.stdout:
        print(result.stdout)
    if result.returncode != 0:
        raise RuntimeError(f"pi exited {result.returncode}: {result.stderr[-2000:]}")
    return result.stdout


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
