"""Regression tests for rebuild-atlas resilience.

rebuild-atlas was failing intermittently on transient turbopuffer blips
(connection resets, slow exports tripping the subprocess timeout) because
its tasks had no retries. Adding retries surfaced a latent hazard: a retry
re-enters clone_repo, but `git clone` refuses a non-empty destination, so
the retry would itself fail on a leftover dir from the prior attempt.

What we test:
  - the atlas tasks carry retries (so transient failures get a second shot)
  - clone_repo is idempotent: it wipes an existing destination before
    cloning, so retries don't trip over the prior attempt's directory
"""

from pathlib import Path
from unittest.mock import patch

from flows.atlas import build_atlas, clone_repo, deploy_to_pages


def test_atlas_tasks_have_retries():
    for task in (clone_repo, build_atlas, deploy_to_pages):
        assert task.retries >= 1, f"{task.name} must retry transient failures"
        assert task.retry_jitter_factor, f"{task.name} should jitter retry delays"


def test_clone_repo_is_retry_safe(tmp_path: Path):
    dest = tmp_path / "repo"
    dest.mkdir()
    (dest / "stale").write_text("leftover from a prior failed attempt")

    with patch("flows.atlas.subprocess.run") as run:
        clone_repo.fn(dest)

    # the leftover dir must have been removed before cloning, else
    # `git clone` would have failed on a non-empty destination
    assert not (dest / "stale").exists()
    run.assert_called_once()
    assert run.call_args.args[0][:2] == ["git", "clone"]
