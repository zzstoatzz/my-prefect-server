from __future__ import annotations

from datetime import datetime, timezone

from flows.bisk import round_start


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def test_round_opens_at_todays_lock_after_noon() -> None:
    # the bug: at 18:40 UTC the snapshot still led with garrison's post, which
    # had already been crowned at 13:05 — it belonged to the round that closed at
    # 12:00, not the one now open. the round must start at today's 12:00 lock.
    assert round_start(_utc("2026-06-30T18:40:00")) == _utc("2026-06-30T12:00:00")


def test_round_uses_prior_lock_before_noon() -> None:
    # before today's lock, the open round is still yesterday's (its winner isn't
    # crowned until ~13:05), so the window reaches back to yesterday's 12:00.
    assert round_start(_utc("2026-06-30T09:30:00")) == _utc("2026-06-29T12:00:00")


def test_round_rolls_exactly_at_the_lock() -> None:
    assert round_start(_utc("2026-06-30T12:00:00")) == _utc("2026-06-30T12:00:00")


def test_just_crowned_post_falls_outside_the_open_round() -> None:
    # garrison's winning post (posted 06-29 19:30, crowned 06-30 13:05) must be
    # excluded from the round open at 18:40 — it already won.
    posted = _utc("2026-06-29T19:30:06")
    assert posted < round_start(_utc("2026-06-30T18:40:00"))
