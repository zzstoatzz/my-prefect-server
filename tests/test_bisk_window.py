from __future__ import annotations

from datetime import datetime, timezone

from flows.bisk import round_bounds


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def test_round_is_todays_calendar_day_after_lock() -> None:
    # after 06:00 UTC, trading is open on today's posting day
    start, end = round_bounds(_utc("2026-06-30T18:40:00"))
    assert start == _utc("2026-06-30T00:00:00")
    assert end == _utc("2026-07-01T00:00:00")


def test_overtime_hours_belong_to_yesterdays_round() -> None:
    # 00:00–06:00 UTC is still the prior day's trading window
    start, end = round_bounds(_utc("2026-06-30T04:30:00"))
    assert start == _utc("2026-06-29T00:00:00")
    assert end == _utc("2026-06-30T00:00:00")


def test_round_rolls_exactly_at_the_lock() -> None:
    start, end = round_bounds(_utc("2026-06-30T06:00:00"))
    assert start == _utc("2026-06-30T00:00:00")
    assert end == _utc("2026-07-01T00:00:00")


def test_fresh_overtime_post_excluded_from_open_round() -> None:
    # a post at 05:30 belongs to the next round, not the one still trading
    start, end = round_bounds(_utc("2026-06-30T05:00:00"))
    posted = _utc("2026-06-30T05:30:00")
    assert not (start <= posted < end)
