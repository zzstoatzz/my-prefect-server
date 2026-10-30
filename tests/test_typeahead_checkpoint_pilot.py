import sqlite3

import pytest
from mps.typeahead_checkpoint_pilot import sample_checkpoint

from tests.test_typeahead_sqlite_seed import source


def test_bounded_sample_preserves_source_and_complete_policy(tmp_path):
    raw, db = source(tmp_path, count=64)
    target = tmp_path / "sample.db"
    try:
        sampled = sample_checkpoint(raw, target, 32)
        assert sampled["sample_bytes"] < 128 * 1024**2
        with sqlite3.connect(target) as read:
            assert read.execute("SELECT COUNT(*) FROM actors").fetchone()[0] == 28
            assert (
                read.execute(
                    "SELECT followers_count FROM actors WHERE did='did:fixture:000000'"
                ).fetchone()[0]
                == 12345
            )
            assert read.execute("SELECT * FROM mod_overrides").fetchall() == [
                ("did:fixture:000003", "show")
            ]
            assert read.execute("SELECT * FROM blocked_domains").fetchall() == [("blocked.test",)]
            assert read.execute("SELECT COUNT(*) FROM tombstones").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM actors").fetchone()[0] == 64
        assert (
            db.execute(
                "SELECT followers_count FROM actors WHERE did='did:fixture:000000'"
            ).fetchone()[0]
            == 12345
        )
        with pytest.raises(ValueError, match="new bounded"):
            sample_checkpoint(raw, target, 32)
    finally:
        db.close()
