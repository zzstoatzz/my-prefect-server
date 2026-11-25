"""Atlas builds clean up failed clones and select their own Python runtime."""

import gzip
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest

from flows import atlas
from flows.atlas import PAGES_ASSET_LIMIT, build_atlas, clone_repo, validate_atlas_asset_sizes


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


def test_pages_limit_is_inclusive_and_names_the_oversized_asset(tmp_path: Path):
    asset = tmp_path / "atlas-summaries.json.gz"
    with asset.open("wb") as stream:
        stream.truncate(PAGES_ASSET_LIMIT)
        validate_atlas_asset_sizes(tmp_path)
        stream.truncate(PAGES_ASSET_LIMIT + 1)

    with pytest.raises(RuntimeError, match=r"atlas-summaries\.json\.gz.*26,214,401 bytes"):
        validate_atlas_asset_sizes(tmp_path)


@pytest.mark.parametrize("outcome", ["ready", "unavailable", "changed"])
def test_summary_refresh_keeps_the_live_map_and_guards_publication(
    tmp_path: Path, monkeypatch, outcome
):
    site = tmp_path / "site"
    site.mkdir()
    sidecar = site / "atlas-summaries.json.gz"
    sidecar.write_bytes(
        gzip.compress(
            json.dumps(
                {
                    "status": "unavailable" if outcome == "unavailable" else "ready",
                    "evidenceWatermark": "snapshot",
                    "atlasGeneratedAt": "map",
                }
            ).encode()
        )
    )
    monkeypatch.setattr(atlas, "clone_repo", lambda _: tmp_path)
    monkeypatch.setattr(atlas, "restore_published_atlas", lambda _: ("map", "old"))
    monkeypatch.setattr(atlas, "build_summaries", lambda _: sidecar)
    live = ("map", "new") if outcome == "changed" else ("map", "old")
    monkeypatch.setattr(atlas, "published_atlas_version", lambda: live)
    with (
        patch.object(atlas, "build_atlas") as vector_build,
        patch.object(atlas, "build_facts") as facts,
        patch.object(atlas, "archive_atlas") as archive,
        patch.object(atlas, "deploy_to_pages") as deploy,
    ):
        if outcome == "ready":
            atlas.rebuild_atlas.fn(reuse_published_map=True)
            archive.assert_called_once_with(tmp_path)
            deploy.assert_called_once_with(site)
        else:
            error = ValueError if outcome == "unavailable" else RuntimeError
            with pytest.raises(error, match=r"Atlas|summaries"):
                atlas.rebuild_atlas.fn(reuse_published_map=True)
            archive.assert_not_called()
            deploy.assert_not_called()
    vector_build.assert_not_called()
    facts.assert_not_called()


def test_restore_selects_the_published_archive_entry(tmp_path: Path, monkeypatch):
    database = tmp_path / "history.sqlite3"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.executescript("""
            CREATE TABLE snapshots (
                id TEXT, archived_at TEXT, atlas_generated_at TEXT,
                summary_generated_at TEXT, status TEXT
            );
            INSERT INTO snapshots VALUES ('current', 'yesterday', 'map', 'summaries', 'ready');
            INSERT INTO snapshots VALUES ('other', 'today', 'map', 'unpublished', 'ready');
        """)
    monkeypatch.setattr(atlas, "atlas_history_path", lambda: database)
    monkeypatch.setattr(atlas, "published_atlas_version", lambda: ("map", "summaries"))
    monkeypatch.setattr(atlas, "public_atlas_asset", lambda _: b"{}")
    site = tmp_path / "site"
    site.mkdir()

    with patch.object(atlas.subprocess, "run") as restore:
        assert atlas.restore_published_atlas.fn(tmp_path) == ("map", "summaries")
        assert restore.call_args.args[0][-4:] == [str(database), "restore", "current", str(site)]
    assert (site / "facts.json").read_bytes() == b"{}"
