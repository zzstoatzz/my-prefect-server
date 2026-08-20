"""Regression tests for transient model-provider failures in compact."""

from flows.compact import extract_likes_observations, synthesize_summary


def test_anthropic_tasks_retry_with_jittered_backoff():
    for task in (synthesize_summary, extract_likes_observations):
        assert task.retries == 3
        assert task.retry_delay_seconds == [15, 30, 60]
        assert task.retry_jitter_factor == 1


def test_anthropic_provider_constructs_against_installed_sdk():
    """2026-08-20: anthropic 1.0.0 started accepting only httpx2 clients while
    pydantic-ai's anthropic provider still passed an httpx.AsyncClient, and
    every synthesis retry died in AnthropicProvider.__init__ with a TypeError.
    Build the model the way synthesize_summary does, so a dependency bump that
    reintroduces the mismatch fails here instead of in the next hourly run."""
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.providers.anthropic import AnthropicProvider

    AnthropicModel("claude-haiku-4-5", provider=AnthropicProvider(api_key="test"))
