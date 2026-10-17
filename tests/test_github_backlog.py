"""fastmcp backlog snapshots: age bucket edges, open-at reconstruction for
backfill, live-only label/draft counts, and backfill replacement that never
touches live rows."""

from __future__ import annotations

import datetime as dt
import json

import pytest
from mps.github_backlog import (
    BacklogItem,
    age_bucket,
    append_rows,
    backfill,
    replace_backfill,
    snapshot,
)

NOW = dt.datetime(2026, 10, 16, 19, 30, tzinfo=dt.UTC)
REPO = "PrefectHQ/fastmcp"


def _ago(days: float) -> dt.datetime:
    return NOW - dt.timedelta(days=days)


def _api(number: int, created: dt.datetime, **extra) -> dict:
    return {"number": number, "created_at": created.isoformat(), "labels": [], **extra}


@pytest.mark.parametrize(
    ("days", "bucket"),
    [
        (0, "lt_7d"),
        (6.99, "lt_7d"),
        (7, "d7_30"),
        (29.9, "d7_30"),
        (30, "d30_90"),
        (90, "gt_90d"),
        (900, "gt_90d"),
    ],
)
def test_age_bucket_edges(days, bucket):
    assert age_bucket(_ago(days), NOW) == bucket


def test_age_bucket_rejects_future_items():
    with pytest.raises(ValueError):
        age_bucket(NOW + dt.timedelta(seconds=1), NOW)


def test_from_api_reads_pr_draft_bot_and_labels():
    pr = BacklogItem.from_api(
        _api(
            1, _ago(1), pull_request={}, draft=True, user={"type": "Bot"}, labels=[{"name": "bug"}]
        )
    )
    issue = BacklogItem.from_api(_api(2, _ago(1), user={"type": "User"}))
    assert (pr.is_pr, pr.draft, pr.bot, pr.labels) == (True, True, True, ["bug"])
    assert (issue.is_pr, issue.draft, issue.bot, issue.labels) == (False, False, False, [])


def test_live_snapshot_counts_buckets_labels_drafts_and_bots():
    items = [
        BacklogItem.from_api(_api(1, _ago(2))),
        BacklogItem.from_api(_api(2, _ago(100), labels=[{"name": "needs MRE"}])),
        BacklogItem.from_api(
            _api(3, _ago(40), labels=[{"name": "bug"}, {"name": "needs more info"}])
        ),
        BacklogItem.from_api(_api(4, _ago(10), pull_request={}, draft=True)),
        BacklogItem.from_api(
            _api(
                5,
                _ago(10),
                pull_request={},
                user={"type": "Bot"},
                labels=[{"name": "dependencies"}],
            )
        ),
    ]
    row = snapshot(items, NOW, REPO)
    assert row["source"] == "live"
    assert row["issues"] == {
        "open": 3, "lt_7d": 1, "d7_30": 0, "d30_90": 1, "gt_90d": 1,
        "unlabeled": 1, "awaiting_reporter": 2,
    }  # fmt: skip
    assert row["prs"] == {
        "open": 2, "lt_7d": 0, "d7_30": 2, "d30_90": 0, "gt_90d": 0,
        "unlabeled": 1, "awaiting_reporter": 0, "draft": 1, "bot": 1,
    }  # fmt: skip


def test_backfill_reconstructs_open_counts_per_midnight():
    items = [
        BacklogItem(number=1, is_pr=False, created_at=_ago(10)),
        BacklogItem(number=2, is_pr=False, created_at=_ago(10), closed_at=_ago(5)),
        BacklogItem(number=3, is_pr=True, created_at=_ago(3), closed_at=_ago(2)),
    ]
    rows = backfill(items, NOW, REPO, days=4)
    midnights = [dt.datetime.fromisoformat(r["at"]) for r in rows]
    assert midnights == [dt.datetime(2026, 10, d, tzinfo=dt.UTC) for d in (12, 13, 14, 15)]
    assert [r["issues"]["open"] for r in rows] == [1, 1, 1, 1]
    assert [r["prs"]["open"] for r in rows] == [0, 0, 1, 0]
    assert set(rows[0]["issues"]) == {"open", "lt_7d", "d7_30", "d30_90", "gt_90d"}
    assert all(r["source"] == "backfill" for r in rows)


def test_replace_backfill_keeps_live_rows_and_other_repos(tmp_path):
    path = tmp_path / "backlog.jsonl"
    live = {"at": "b", "repo": REPO, "source": "live"}
    other = {"at": "c", "repo": "other/repo", "source": "backfill"}
    append_rows(path, [{"at": "a", "repo": REPO, "source": "backfill"}, live, other])

    kept = replace_backfill(path, [{"at": "z", "repo": REPO, "source": "backfill"}], REPO)
    rows = [json.loads(line) for line in path.read_text().splitlines()]

    assert kept == 2
    assert rows == [{"at": "z", "repo": REPO, "source": "backfill"}, live, other]
    append_rows(path, [{"at": "d", "repo": REPO, "source": "live"}])
    assert len(path.read_text().splitlines()) == 4
