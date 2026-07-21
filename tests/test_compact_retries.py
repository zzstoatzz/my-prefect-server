"""Regression tests for transient model-provider failures in compact."""

from flows.compact import extract_likes_observations, synthesize_summary


def test_anthropic_tasks_retry_with_jittered_backoff():
    for task in (synthesize_summary, extract_likes_observations):
        assert task.retries == 3
        assert task.retry_delay_seconds == [15, 30, 60]
        assert task.retry_jitter_factor == 1
