"""Atlas builds clean up failed clones and select their own Python runtime."""

from pathlib import Path
from unittest.mock import patch

from flows.atlas import build_atlas, clone_repo


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


def test_build_atlas_does_not_pass_the_worker_python(tmp_path: Path, monkeypatch):
    # the worker units export UV_PYTHON, which would override the script's
    # own requires-python and force an interpreter numba cannot build on
    monkeypatch.setenv("UV_PYTHON", "3.15")
    monkeypatch.setenv("TURBOPUFFER_API_KEY", "kept")
    (tmp_path / "site").mkdir()
    (tmp_path / "site" / "atlas.json.gz").write_bytes(b"")

    with (
        patch("flows.atlas.get_run_logger"),
        patch("flows.atlas.subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        build_atlas.fn(tmp_path)

    env = run.call_args.kwargs["env"]
    assert "UV_PYTHON" not in env
    assert env["TURBOPUFFER_API_KEY"] == "kept"
