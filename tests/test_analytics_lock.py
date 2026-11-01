"""single-writer coordination for analytics.duckdb (mps.lock)."""

from unittest import mock

import duckdb
from mps.db import write_liked_posts
from mps.likes import LikedPost
from mps.lock import ANALYTICS_WRITER_LIMIT, analytics_write_slot


def test_no_api_url_skips_coordination(monkeypatch):
    monkeypatch.delenv("PREFECT_API_URL", raising=False)
    with mock.patch("prefect.settings.PREFECT_API_URL") as setting:
        setting.value.return_value = None
        with mock.patch("prefect.concurrency.sync.concurrency") as cm, analytics_write_slot():
            pass
    cm.assert_not_called()


def test_api_url_acquires_writer_slot(monkeypatch):
    monkeypatch.setenv("PREFECT_API_URL", "http://example.test/api")
    with mock.patch("prefect.concurrency.sync.concurrency") as cm, analytics_write_slot():
        pass
    cm.assert_called_once_with(
        ANALYTICS_WRITER_LIMIT,
        strict=True,
        raise_on_lease_renewal_failure=False,
    )


def test_db_write_helpers_hold_the_slot(monkeypatch, tmp_path):
    monkeypatch.setenv("PREFECT_API_URL", "http://example.test/api")
    db_path = str(tmp_path / "analytics.duckdb")
    with mock.patch("prefect.concurrency.sync.concurrency") as cm:
        count = write_liked_posts(
            [
                LikedPost(
                    subject_uri="at://y/post/1",
                    author_handle="y.bsky.social",
                    author_did="did:plc:y",
                    text="hello",
                    created_at="2026-08-05",
                    liked_at="2026-08-05",
                    embed_type="",
                    embed_text="",
                )
            ],
            db_path,
        )
    assert count == 1
    cm.assert_called_once()

    con = duckdb.connect(db_path, read_only=True)
    assert con.execute("SELECT count(*) FROM raw_liked_posts").fetchone()[0] == 1
    con.close()
