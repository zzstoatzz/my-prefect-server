"""Summary caching follows model inputs rather than hourly activity statistics."""

from flows.compact import BySummaryInputs


def test_summary_cache_ignores_hourly_stats_and_follows_real_inputs():
    """2026-10-17: the key covered only observations and expired after 4h, so
    every hourly run resynthesized every user (~590 calls/day) with nothing
    new to say. The key now tracks what the summary is written from, and the
    entry lives a week."""
    policy = BySummaryInputs()

    def key(**overrides):
        inputs = {
            "handle": "alice.test",
            "stats_text": "recency: 0.91",
            "observations_text": "- likes zig",
            "interactions_text": "- hi",
            "bsky_profile": {"display_name": "Alice", "bio": "hello"},
            **overrides,
        }
        return policy.compute_key(None, inputs, {})

    base = key()
    assert key(stats_text="recency: 0.42") == base
    assert key(observations_text="- likes rust") != base
    assert key(interactions_text="- bye") != base
    assert key(bsky_profile={"display_name": "Alice", "bio": "new bio"}) != base
    assert key(bsky_profile=None) != base
    assert key(observations_text="") is None
