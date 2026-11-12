import datetime as dt

import pytest
from mps.costs.connectors.voyage import Day, line_items, merge_day
from pydantic import ValidationError

NOW = dt.datetime(2026, 11, 2, 12, tzinfo=dt.UTC)


def observation(day=1, tokens=1000, pixels=50_000, unmetered=0):
    return Day(
        day=NOW.date().replace(day=day),
        observed_to=NOW,
        spans=2,
        calls=1,
        unmetered=unmetered,
        text_tokens=tokens,
        image_pixels=pixels,
    )


def test_requery_deduplicates_and_retention_cannot_erase_earlier_usage():
    original = observation(tokens=1_000_000)
    smaller = observation(tokens=10)
    assert merge_day(original, smaller).text_tokens == 1_000_000
    items, issues = line_items([original, original, smaller, observation(day=2)], NOW)
    assert items[0].amount == 12
    assert items[0].basis == "month_to_date"
    assert items[0].estimated is True
    assert "1001000 text tokens" in items[0].usage
    assert not any("no Find Bufo telemetry" in issue for issue in issues)


def test_missing_days_and_usage_are_unknown_not_zero():
    items, issues = line_items([observation(unmetered=1)], NOW)
    assert len(items) == 1
    assert any("2026-11-02" in issue for issue in issues)
    assert any("1 observed calls" in issue for issue in issues)
    assert line_items([], NOW)[0] == []


def test_month_rollover_excludes_prior_month_and_rejects_invalid_usage():
    later = NOW.replace(month=12)
    assert line_items([observation()], later)[0] == []
    with pytest.raises(ValidationError):
        observation(tokens=-1)
    with pytest.raises(ValueError, match="different usage days"):
        merge_day(observation(), observation(day=2))


def test_pixels_and_tokens_use_distinct_rates_and_round_only_after_summing():
    items, _ = line_items([observation(tokens=1_000_000, pixels=1_000_000_000)], NOW)
    assert items[0].amount == 72
    items, _ = line_items(
        [observation(pixels=4_000_000), observation(day=2, pixels=4_000_000)], NOW
    )
    assert items[0].amount == 1
