import logging
from pathlib import Path

import pytest

from flows import pub_search_snapshot as snapshot


@pytest.mark.parametrize("outcome", ["published", "failed", "staging", "skip-upload"])
def test_only_a_completed_prod_build_replaces_atlas_input(tmp_path, monkeypatch, outcome):
    handoff = tmp_path / "pub-search-snapshot"
    handoff.mkdir()
    current = handoff / "replica.db"
    current.write_bytes(b"previous")
    monkeypatch.setenv("PREFECT_LOCAL_STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("BUILDER_CHANNEL", "staging" if outcome == "staging" else "prod")
    monkeypatch.delenv("SKIP_UPLOAD", raising=False)
    if outcome == "skip-upload":
        monkeypatch.setenv("SKIP_UPLOAD", "1")
    monkeypatch.setattr(snapshot, "get_run_logger", lambda: logging.getLogger(__name__))
    monkeypatch.setattr(snapshot.shutil, "which", lambda _: "/fixture/rclone")
    monkeypatch.setattr(snapshot, "clone_repo", lambda _ref: tmp_path / "repo")
    monkeypatch.setattr(snapshot, "build_binary", lambda _repo: (tmp_path / "builder", "test"))
    monkeypatch.setattr(snapshot, "run_builder", snapshot.run_builder.fn)

    def build(_cmd, _cwd, env, timeout):
        assert timeout == 6000
        scratch = Path(env["BUILD_DIR"])
        assert scratch != handoff
        (scratch / "replica.db").write_bytes(b"next")
        assert current.read_bytes() == b"previous"
        if outcome == "failed":
            raise RuntimeError("publication failed")

    monkeypatch.setattr(snapshot, "_stream", build)
    if outcome == "failed":
        with pytest.raises(RuntimeError, match="publication failed"):
            snapshot.pub_search_snapshot.fn()
    else:
        snapshot.pub_search_snapshot.fn()
    assert current.read_bytes() == (b"next" if outcome == "published" else b"previous")
    assert list(handoff.iterdir()) == [current]
