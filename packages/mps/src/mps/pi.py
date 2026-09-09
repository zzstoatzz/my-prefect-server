"""Trusted prompt screening and the single isolated Pi execution entrypoint."""

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

# pi built-in tool allowlists. "read-only" is the allowlist pi's own --help
# suggests for review tasks.
TOOL_ARGS: dict[str, list[str]] = {
    "full": [],
    "read-only": ["--tools", "read,grep,find,ls"],
    "none": ["--no-tools"],
}

ToolMode = Literal["full", "read-only", "none"]


class Verdict(BaseModel):
    allowed: bool
    reason: str


JUDGE_SYSTEM = """\
you screen prompts submitted to an autonomous coding agent that runs on \
private infrastructure. the agent works inside a scratch clone of one of the \
operator's own repositories, diagnosing issues and proposing changes as \
patches the operator reviews before anything is merged.

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


def screen_prompt(prompt: str, tool_mode: str, api_key: str) -> None:
    """policy judge: a cheap model screens intent before pi is launched.

    call this from flow code, never from a parameter, so whoever triggers a
    run cannot skip it. fails closed — judge errors abort the run.
    """
    from pydantic_ai import Agent
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.providers.anthropic import AnthropicProvider

    from mps.spend import record_pydantic_ai_result

    judge = Agent[None, Verdict](
        AnthropicModel("claude-haiku-4-5", provider=AnthropicProvider(api_key=api_key)),
        output_type=Verdict,
        system_prompt=JUDGE_SYSTEM,
        name="pi-judge",
    )
    result = judge.run_sync(f"tool_mode: {tool_mode}\n\nprompt:\n{prompt}")
    record_pydantic_ai_result(
        task_name="pi_judge",
        model="claude-haiku-4-5",
        result=result,
        metadata={"tool_mode": tool_mode},
    )
    if not result.output.allowed:
        raise ValueError(f"prompt rejected by policy judge: {result.output.reason}")
    print(f"policy judge: allowed ({result.output.reason})")


def minimal_env(**extra: str) -> dict[str, str]:
    """child env built from scratch, not inherited.

    HOME is included because pi reads provider credentials from
    ~/.pi/agent/auth.json; pass a dedicated HOME to scope which ones it sees.
    """
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG") if k in os.environ}
    env.update({k: v for k, v in extra.items() if v})
    return env


def run_pi(
    prompt: str,
    *,
    cwd: str,
    provider: str = "aperture",
    model: str | None = None,
    thinking: str = "medium",
    tool_mode: ToolMode = "read-only",
    timeout_seconds: int = 1500,
    skills: list[str] | None = None,
) -> str:
    """Run Pi inside the prepared Sprite boundary and return its final output.

    Inference credentials remain with the trusted bridge. Callers cannot supply
    an agent environment or select a provider outside the run grant.
    """
    from mps.pi_execution import run_isolated_pi

    if provider != "aperture" or model not in (None, "openai/gpt-5.6-luna"):
        raise ValueError("Phi uses Aperture model openai/gpt-5.6-luna")
    if tool_mode not in TOOL_ARGS:
        raise ValueError(f"Unknown Pi tool mode: {tool_mode}")
    result = run_isolated_pi(
        prompt,
        workspace=Path(cwd),
        tool_args=TOOL_ARGS[tool_mode],
        thinking=thinking,
        timeout_seconds=timeout_seconds,
        skills=skills or [],
    )
    if result:
        print(result)
    return result
