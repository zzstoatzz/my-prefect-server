"""typeahead-enrich-backfill logged `progress: processed=1500 found=1492
not_found=8 hidden=1 cursor=(1789924132, 920838)` once per 500-row page: no
rate, no sense of how much run was left, and a cursor that only reads as a
position once the epoch is converted by hand. The log lines are built from
these formatters, so their wording is pinned here."""

import logging

import httpx

from flows import typeahead_enrich_backfill as backfill
from flows.typeahead_enrich_backfill import (
    _fmt_counts,
    _fmt_duration,
    _fmt_queue_head,
    _fmt_remaining,
)


def test_duration_picks_the_two_most_significant_units():
    assert _fmt_duration(0) == "0s"
    assert _fmt_duration(59.4) == "59s"
    assert _fmt_duration(61) == "1m01s"
    assert _fmt_duration(12600) == "3h30m"
    assert _fmt_duration(21 * 86400 + 7 * 3600) == "21d07h"
    assert _fmt_duration(-5) == "0s"


def test_queue_head_reads_as_a_time_and_a_lag():
    now = 1789924132 + 21 * 86400 + 3 * 3600
    assert _fmt_queue_head(1789924132, now) == "queue head 2026-09-20 17:08Z (21d03h behind now)"


def test_queue_head_before_the_first_page():
    assert _fmt_queue_head(0, 1789924132) == "queue head not reached"


def test_counts_omit_hidden_when_none_were_hidden():
    assert _fmt_counts(1500, 1492, 8, 1) == "1,500 actors (1,492 found, 8 not found, 1 hidden)"
    assert _fmt_counts(500, 500, 0, 0) == "500 actors (500 found, 0 not found)"


def test_remaining_is_the_budget_when_no_limit_is_set():
    assert _fmt_remaining(1500, None, 48.0, 11100) == "3h05m of budget left"


def test_remaining_is_the_limit_when_it_lands_inside_the_budget():
    assert _fmt_remaining(2500, 10000, 50.0, 11100) == "25% of limit 10,000, ~2m30s left"


def test_remaining_falls_back_to_the_budget_when_the_limit_is_out_of_reach():
    assert _fmt_remaining(2500, 10_000_000, 50.0, 11100) == "3h05m of budget left"
    assert _fmt_remaining(0, 100, 0.0, 600) == "10m00s of budget left"


class QueueClient:
    """one page of three queued actors, then an empty queue; the appview
    knows two of them and bsky-mod hides one"""

    def __init__(self, *args, **kwargs):
        self.pages = [
            [
                [{"value": "1"}, {"value": "did:plc:a"}, {"value": "1789924132"}],
                [{"value": "2"}, {"value": "did:plc:b"}, {"value": "1789924132"}],
                [{"value": "3"}, {"value": "did:plc:c"}, {"value": "1789924132"}],
            ],
            [],
        ]

    def post(self, url, headers, json, timeout):
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "type": "ok",
                        "response": {"type": "execute", "result": {"rows": self.pages.pop(0)}},
                    },
                    {"type": "ok", "response": {"type": "close"}},
                ]
            },
            request=httpx.Request("POST", url),
        )

    def get(self, url, params):
        hide = {"src": backfill.BSKY_MOD_DID, "val": "!hide"}
        return httpx.Response(
            200,
            json={
                "profiles": [
                    {"did": "did:plc:a", "handle": "a.test", "followersCount": 10},
                    {"did": "did:plc:b", "handle": "b.test", "labels": [hide]},
                ]
            },
            request=httpx.Request("GET", url),
        )


def test_a_run_logs_a_start_line_progress_and_a_one_line_summary(monkeypatch, caplog):
    monkeypatch.setenv("TURSO_URL", "libsql://example.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "t")
    monkeypatch.setattr(backfill.httpx, "Client", QueueClient)
    monkeypatch.setattr(backfill, "get_run_logger", lambda: logging.getLogger("backfill"))
    monkeypatch.setattr(backfill, "PROGRESS_EVERY_S", 0.0)
    monkeypatch.setattr(backfill.time, "time", lambda: 1789924132 + 3600.0)

    with caplog.at_level(logging.INFO, logger="backfill"):
        summary = backfill.typeahead_enrich_backfill.fn(
            limit=None, dids_per_second=1e6, dry_run=True
        )

    lines = [r.getMessage() for r in caplog.records]
    head = "queue head 2026-09-20 17:08Z (1h00m behind now)"
    counts = "3 actors (2 found, 1 not found, 1 hidden)"
    assert (
        lines[0]
        == "dry run over the whole queue, oldest first, at 1e+06 DIDs/s with a 3h30m budget"
    )
    assert lines[1].startswith(f"{counts} at ")
    assert lines[1].endswith(f"/s, {head}, 3h30m of budget left")
    assert lines[2].startswith(f"dry run finished in 0s (queue drained): {counts} at ")
    assert lines[2].endswith(f"/s overall, {head}")
    assert lines[3].startswith("samples:\na.test: score=")
    assert len(lines) == 4
    assert summary["drained"] is True
    assert summary["processed"] == 3
