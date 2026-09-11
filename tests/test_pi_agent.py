from unittest.mock import Mock

import pytest
from mps import pi
from pydantic import ValidationError

from flows import pi_agent


@pytest.fixture
def process(monkeypatch):
    run = Mock(return_value=Mock(stdout="done", stderr="", returncode=0))
    monkeypatch.setattr(pi.shutil, "which", lambda _: "/bin/pi")
    monkeypatch.setattr(pi.subprocess, "run", run)
    return run


def test_legacy_runner_defaults(process):
    assert pi.run_pi("review", cwd="/tmp", provider="anthropic") == "done"
    args = process.call_args.args[0]
    assert args[args.index("--tools") + 1] == "read,grep,find,ls"
    assert "--no-extensions" not in args
    assert args[-2:] == ["--", "review"]


def test_explicit_mcp_tools_and_instructions(process):
    pi.run_pi(
        "--turn-off",
        cwd="/tmp",
        provider="openai-codex",
        model="chosen-model",
        toolset=pi.Toolset(names=["mcp"], extensions=["/opt/pi/lights.ts"]),
        instructions="Control the lights.",
    )
    args = process.call_args.args[0]
    assert args[args.index("--tools") + 1] == "mcp"
    assert args[args.index("--extension") + 1] == "/opt/pi/lights.ts"
    assert args[args.index("--system-prompt") + 1] == "Control the lights."
    assert {"--no-extensions", "--no-context-files", "--no-skills"} <= set(args)
    assert args[-2:] == ["--", "--turn-off"]


def test_empty_toolset_does_not_fall_back_to_full(process):
    pi.run_pi(
        "summarize",
        cwd="/tmp",
        provider="anthropic",
        tool_mode="full",
        toolset=pi.Toolset(names=[]),
    )
    assert "--no-tools" in process.call_args.args[0]


@pytest.mark.parametrize("names", [[""], ["read,bash"], ["--bash"]])
def test_tool_names_are_not_cli_fragments(names):
    with pytest.raises(ValidationError):
        pi.Toolset(names=names)


@pytest.mark.parametrize(
    "agent,approval",
    [
        (pi_agent.Agent(), False),
        (pi_agent.Agent(tool_mode="full"), True),
        (pi_agent.Agent(toolset=pi.Toolset(names=["read", "bash"])), True),
        (pi_agent.Agent(toolset=pi.Toolset(names=["mcp"])), False),
    ],
)
def test_flow_preserves_approval_and_passes_configuration(monkeypatch, agent, approval):
    screen, pause, run = Mock(), Mock(), Mock(return_value="done")
    monkeypatch.setattr(pi_agent, "secret_sync", lambda _: "test-key")
    monkeypatch.setattr(pi_agent, "screen_prompt", screen)
    monkeypatch.setattr(pi_agent, "pause_flow_run", pause)
    monkeypatch.setattr(pi_agent, "run_pi", run)
    assert pi_agent.pi_agent.fn("objective", agent=agent, instructions="purpose") == "done"
    assert pause.called == approval
    assert screen.call_args.kwargs == {"instructions": "purpose", "toolset": agent.toolset}
    assert run.call_args.kwargs["toolset"] == agent.toolset
    assert run.call_args.kwargs["instructions"] == "purpose"


def test_rejected_prompt_never_launches(monkeypatch):
    monkeypatch.setattr(pi_agent, "secret_sync", lambda _: "test-key")
    monkeypatch.setattr(pi_agent, "screen_prompt", Mock(side_effect=ValueError("rejected")))
    run = Mock()
    monkeypatch.setattr(pi_agent, "run_pi", run)
    with pytest.raises(ValueError, match="rejected"):
        pi_agent.pi_agent.fn("objective")
    run.assert_not_called()


def test_existing_deployment_keeps_legacy_mode_overrides():
    from pathlib import Path

    import yaml

    config = yaml.safe_load(Path("prefect.yaml").read_text())
    deployment = next(d for d in config["deployments"] if d["name"] == "pi-agent")
    settings = {**deployment["parameters"]["agent"], "tool_mode": "full"}
    agent = pi_agent.Agent.model_validate(settings)
    assert agent.toolset is None
    assert agent.tool_mode == "full"


@pytest.mark.parametrize("provider", ["anthropic", "openai-codex"])
def test_flow_passes_only_selected_provider_credential(monkeypatch, provider):
    monkeypatch.setenv("PREFECT_API_AUTH_STRING", "server-secret")
    monkeypatch.setattr(pi_agent, "secret_sync", lambda _: "provider-key")
    monkeypatch.setattr(pi_agent, "screen_prompt", Mock())
    run = Mock(return_value="done")
    monkeypatch.setattr(pi_agent, "run_pi", run)
    pi_agent.pi_agent.fn("explain code", agent=pi_agent.Agent(provider=provider))
    env = run.call_args.kwargs["env"]
    assert "PREFECT_API_AUTH_STRING" not in env
    if provider == "anthropic":
        assert env["ANTHROPIC_API_KEY"] == "provider-key"
    else:
        assert "ANTHROPIC_API_KEY" not in env
