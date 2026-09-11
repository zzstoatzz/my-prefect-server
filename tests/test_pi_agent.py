"""Keep the requested workflow's result available after Sprite cleanup."""

from unittest.mock import Mock

from flows import pi_agent as module


def test_result_is_saved_as_prefect_artifact(monkeypatch):
    monkeypatch.setattr(module, "secret_sync", lambda name: "judge-test-key")
    judge = Mock()
    execute = Mock(return_value="Investigation result")
    artifact = Mock()
    monkeypatch.setattr(module, "screen_prompt", judge)
    monkeypatch.setattr(module, "run_pi", execute)
    monkeypatch.setattr(module, "create_markdown_artifact", artifact)
    assert module.pi_agent.fn("Investigate") == "Investigation result"
    judge.assert_called_once_with(
        "Investigate",
        "read-only",
        "judge-test-key",
        inputs={
            "workspace": module.Workspace().model_dump(),
            "agent": module.Agent().model_dump(),
            "timeout_seconds": 1500,
        },
    )
    assert execute.call_args.kwargs["provider"] == "aperture"
    assert "Gardener (gardener.pds.zat.dev)" in execute.call_args.args[0]
    assert execute.call_args.args[0].endswith("Investigate")
    assert "env" not in execute.call_args.kwargs
    artifact.assert_called_once_with(
        key="pi-agent-output",
        markdown="Investigation result",
        description="Gardener investigation result (Pi harness)",
    )


def test_rejected_input_never_reaches_clone_or_pi(monkeypatch):
    import pytest

    monkeypatch.setattr(module, "secret_sync", lambda name: "judge-test-key")
    monkeypatch.setattr(module, "screen_prompt", Mock(side_effect=ValueError("rejected")))
    clone = Mock()
    execute = Mock()
    monkeypatch.setattr(module.subprocess, "run", clone)
    monkeypatch.setattr(module, "run_pi", execute)
    with pytest.raises(ValueError, match="rejected"):
        module.pi_agent.fn("malicious request", workspace=module.Workspace(repo="bot"))
    clone.assert_not_called()
    execute.assert_not_called()
