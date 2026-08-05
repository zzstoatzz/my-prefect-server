"""single-writer coordination for analytics.duckdb (mps.lock)."""

from unittest import mock

import duckdb

from mps.db import write_likes
from mps.likes import LikeRecord
from mps.lock import ANALYTICS_WRITER_LIMIT, analytics_write_slot


def test_no_api_url_skips_coordination(monkeypatch):
    monkeypatch.delenv("PREFECT_API_URL", raising=False)
    with mock.patch("prefect.settings.PREFECT_API_URL") as setting:
        setting.value.return_value = None
        with mock.patch("prefect.concurrency.sync.concurrency") as cm:
            with analytics_write_slot():
                pass
    cm.assert_not_called()


def test_api_url_acquires_writer_slot(monkeypatch):
    monkeypatch.setenv("PREFECT_API_URL", "http://example.test/api")
    with mock.patch("prefect.concurrency.sync.concurrency") as cm:
        with analytics_write_slot():
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
        count = write_likes(
            [LikeRecord(at_uri="at://x/like/1", subject_uri="at://y/post/1", created_at="2026-08-05")],
            db_path,
        )
    assert count == 1
    cm.assert_called_once()

    con = duckdb.connect(db_path, read_only=True)
    assert con.execute("SELECT count(*) FROM raw_likes").fetchone()[0] == 1
    con.close()
