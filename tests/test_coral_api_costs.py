"""Coral's metered model spend becomes monthly anthropic and typesafe lines."""

import datetime as dt

import pytest
from mps.costs.connectors.coral_api import line_items

NOW = dt.datetime(2026, 10, 27, 12, tzinfo=dt.UTC)


def _stats(window_h: float, haiku_usd: float, jev_usd: float) -> dict:
    since = NOW - dt.timedelta(hours=window_h)
    return {
        "usage_since_ms": int(since.timestamp() * 1000),
        "curator_llm": {
            "model": "claude-haiku-5-5",
            "calls_today": 18,
            "usd_since_start": haiku_usd,
        },
        "jev": {"model": "jev-1.13.0", "calls_today": 18, "usd_since_start": jev_usd},
    }


def test_since_start_spend_scales_to_thirty_days():
    items, gaps = line_items(_stats(24, 0.53, 0.144), NOW)

    assert gaps == []
    by_provider = {i.provider: i for i in items}
    assert by_provider["anthropic"].amount == 1590
    assert by_provider["anthropic"].service == "coral:curator"
    assert by_provider["typesafe"].amount == 432
    assert by_provider["typesafe"].service == "coral:jev"
    assert all(i.project == "trending" and i.estimated for i in items)


def test_short_window_is_unmeasured_not_zero():
    items, gaps = line_items(_stats(0.25, 0.01, 0.003), NOW)

    assert items == []
    assert len(gaps) == 1 and "15 min" in gaps[0]


def test_missing_window_raises():
    stats = _stats(24, 0.5, 0.1)
    del stats["usage_since_ms"]
    with pytest.raises(RuntimeError, match="usage_since_ms"):
        line_items(stats, NOW)


def test_missing_meter_raises():
    stats = _stats(24, 0.5, 0.1)
    del stats["jev"]
    with pytest.raises(RuntimeError, match="jev"):
        line_items(stats, NOW)
