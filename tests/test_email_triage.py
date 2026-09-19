"""email-triage: candidate rules, shortlist trimming, Discord renderings, persistence.

The flow's model calls and its pause are Prefect and network; everything
around them is pure and tested here.
"""

from pathlib import Path
from unittest import mock

import duckdb
from mps.db import recent_classified_emails, write_email_triage
from mps.email_triage import (
    DISCORD_BUDGET,
    SHORTLIST_MAX,
    Candidate,
    Shortlist,
    ShortlistItem,
    TriageAction,
    TriageActions,
    render_outcome,
    render_shortlist,
    select_candidates,
    trim_shortlist,
)


def _c(
    i: int, category: str, confidence: float | None, received: str = "2026-09-19T08:00"
) -> Candidate:
    return Candidate(
        message_id=f"m{i}",
        sender_name=f"sender {i}",
        sender_address=f"s{i}@example.com",
        subject=f"subject {i}",
        snippet="body " * 20,
        received_at=received,
        category=category,
        confidence=confidence,
    )


def test_select_keeps_personal_work_and_uncertain_only():
    rows = [
        _c(0, "personal", 0.99),
        _c(1, "work", 0.55),
        _c(2, "promotional", 0.95),
        _c(3, "notification", 0.41),
        _c(4, "notification", 0.9),
        _c(5, "promotional", None),
    ]
    kept = {c.message_id for c in select_candidates(rows)}
    assert kept == {"m0", "m1", "m3", "m5"}


def test_select_orders_newest_first_and_caps():
    rows = [_c(i, "personal", 0.9, received=f"2026-09-{1 + i % 28:02d}T00:00") for i in range(80)]
    kept = select_candidates(rows)
    assert len(kept) == 60
    assert kept[0].received_at >= kept[-1].received_at


def test_trim_drops_bad_indexes_duplicates_and_extras():
    items = [
        ShortlistItem(index=i, reason="r", suggested="read")
        for i in (3, 3, 99, -1, 0, 1, 2, 4, 5, 6, 7, 8, 9)
    ]
    trimmed = trim_shortlist(Shortlist(summary="s", items=items), n_candidates=10)
    assert [i.index for i in trimmed.items] == [3, 0, 1, 2, 4, 5, 6, 7]
    assert len(trimmed.items) == SHORTLIST_MAX


def test_render_shortlist_fits_discord_and_carries_the_run_link():
    candidates = [_c(i, "personal", 0.9) for i in range(8)]
    shortlist = Shortlist(
        summary="a busy day",
        items=[ShortlistItem(index=i, reason="x" * 120, suggested="reply") for i in range(8)],
    )
    text = render_shortlist(shortlist, candidates, total=40, run_url="https://p/runs/flow-run/abc")
    assert len(text) <= DISCORD_BUDGET
    assert "https://p/runs/flow-run/abc" in text
    assert "8 of 40" in text
    assert "just triage-reply" in text


def test_render_shortlist_truncates_items_when_over_budget():
    candidates = [_c(i, "personal", 0.9) for i in range(8)]
    for c in candidates:
        c.subject = "s" * 400
    shortlist = Shortlist(
        summary="long subjects",
        items=[ShortlistItem(index=i, reason="r" * 119, suggested="act") for i in range(8)],
    )
    text = render_shortlist(shortlist, candidates, total=8, run_url="https://p/r")
    assert len(text) <= DISCORD_BUDGET
    assert "more" in text


def test_render_outcome_names_each_decision():
    candidates = [_c(0, "work", 0.7), _c(1, "personal", 0.9)]
    shortlist = Shortlist(
        summary="s",
        items=[
            ShortlistItem(index=1, reason="r", suggested="reply"),
            ShortlistItem(index=0, reason="r", suggested="act"),
        ],
    )
    actions = TriageActions(
        summary="two decided",
        actions=[
            TriageAction(index=0, action="reply", note="answer tonight"),
            TriageAction(index=1, action="archive", note="done"),
            TriageAction(index=7, action="ignore", note=""),
        ],
    )
    text = render_outcome(actions, shortlist, candidates)
    assert "_reply_ **sender 1**" in text
    assert "_archive_ **sender 0**" in text
    assert text.count("\n- ") == 2


def test_db_round_trip(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("PREFECT_API_URL", raising=False)
    db = str(tmp_path / "analytics.duckdb")
    con = duckdb.connect(db)
    con.execute(
        "CREATE TABLE raw_emails (message_id VARCHAR PRIMARY KEY, subject VARCHAR, sender_name VARCHAR, "
        "sender_address VARCHAR, snippet VARCHAR, received_at VARCHAR, unread BOOLEAN, mailbox VARCHAR)"
    )
    con.execute(
        "CREATE TABLE raw_email_classifications (message_id VARCHAR PRIMARY KEY, category VARCHAR, "
        "classified_at TIMESTAMP DEFAULT now(), confidence DOUBLE)"
    )
    con.execute(
        "INSERT INTO raw_emails VALUES "
        "('new', 'hi', 'Alice', 'a@x', 'snippet', strftime(now(), '%Y-%m-%dT%H:%M:%S+00:00'), true, 'INBOX'), "
        "('old', 'ancient', 'Bob', 'b@x', 'snippet', '2020-01-01T00:00:00+00:00', false, 'INBOX')"
    )
    con.execute(
        "INSERT INTO raw_email_classifications VALUES ('new', 'personal', now(), 0.9), ('old', 'work', now(), 0.8)"
    )
    con.close()

    rows = recent_classified_emails(db, hours=36)
    assert [r.message_id for r in rows] == ["new"]
    assert rows[0].category == "personal" and rows[0].confidence == 0.9

    with mock.patch("prefect.settings.PREFECT_API_URL") as setting:
        setting.value.return_value = None
        total = write_email_triage(
            [("new", "reply", "answer alice")], "run-1", "reply to alice", db
        )
    assert total == 1
    con = duckdb.connect(db, read_only=True)
    assert con.execute(
        "SELECT message_id, action, note, run_id FROM raw_email_triage"
    ).fetchall() == [("new", "reply", "answer alice", "run-1")]
    con.close()
