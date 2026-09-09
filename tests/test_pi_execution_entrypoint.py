"""The shared flow caller must use the isolated execution path exclusively."""

from pathlib import Path

import pytest
from mps.pi import run_pi


def test_caller_passes_capabilities_to_isolated_runner(monkeypatch, tmp_path, capsys):
    calls = []

    def isolated(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return "revision prepared"

    monkeypatch.setattr("mps.pi_execution.run_isolated_pi", isolated)
    assert run_pi("fix it", cwd=str(tmp_path), tool_mode="full") == "revision prepared"
    assert "revision prepared" in capsys.readouterr().out
    assert calls == [
        (
            "fix it",
            {
                "workspace": Path(tmp_path),
                "tool_args": [],
                "thinking": "medium",
                "timeout_seconds": 1500,
                "skills": [],
            },
        )
    ]


@pytest.mark.parametrize(
    "options",
    [{"provider": "anthropic"}, {"model": "unscoped-model"}, {"tool_mode": "unknown"}],
)
def test_caller_rejects_unscoped_execution_before_launch(monkeypatch, tmp_path, options):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid execution must not launch Pi")

    monkeypatch.setattr("mps.pi_execution.run_isolated_pi", unexpected)
    with pytest.raises(ValueError):
        run_pi("fix it", cwd=str(tmp_path), **options)


def test_caller_cannot_inject_an_agent_environment(tmp_path):
    with pytest.raises(TypeError, match="env"):
        run_pi("fix it", cwd=str(tmp_path), env={"PREFECT_API_KEY": "sentinel"})
