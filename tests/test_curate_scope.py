"""curate reviews private observations and nothing else.

It used to run a daily janitor agent over phi's semble library: it deleted her
note cards, refiled cards against her own shelving, duplicated collection
links, and orphaned edges, all outside her telemetry. The library has one
curator, and it is not this flow.
"""

import inspect

from flows import curate


def test_curate_agent_only_has_observation_tools():
    agent = curate._build_agent("claude-haiku-4-5", "dummy-key")
    assert set(agent._function_toolset.tools) == {
        "recall",
        "list_users",
        "list_user_observations",
        "deprecate_observation",
        "update_observation",
    }


def test_curate_cannot_reach_phis_repo_or_semble():
    source = inspect.getsource(curate)
    assert "import semble" not in source and "from semble" not in source
    assert "create_bsky_session" not in source
    assert "com.atproto.repo" not in source
