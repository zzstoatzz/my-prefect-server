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
    judge.assert_called_once_with("Investigate", "read-only", "judge-test-key")
    assert execute.call_args.kwargs["provider"] == "aperture"
    assert "env" not in execute.call_args.kwargs
    artifact.assert_called_once_with(
        key="pi-agent-output", markdown="Investigation result", description="Pi workflow result"
    )
