"""Open issue/PR counts for a GitHub repo, bucketed by age, as an append-only series.

One row per snapshot, appended to a JSONL file next to analytics.duckdb (same
shape as llm-spend.jsonl). A row looks like:

    {"at": "2026-10-16T19:30:00+00:00", "repo": "PrefectHQ/fastmcp", "source": "live",
     "issues": {"open": 379, "lt_7d": 12, "d7_30": 40, "d30_90": 80, "gt_90d": 247,
                "unlabeled": 30, "awaiting_reporter": 9},
     "prs": {"open": 146, ..., "draft": 13, "bot": 4}}

`source="backfill"` rows are reconstructed from created_at/closed_at, so they
carry only `open` and the age buckets: label and draft state at a past instant
is not recoverable from the current issue list. An item that was closed and
reopened counts as open across its closed gap.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel

AGE_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("lt_7d", 0, 7),
    ("d7_30", 7, 30),
    ("d30_90", 30, 90),
    ("gt_90d", 90, None),
)
AWAITING_REPORTER_LABELS = frozenset({"needs more info", "needs MRE"})


class BacklogItem(BaseModel):
    number: int
    is_pr: bool
    created_at: dt.datetime
    closed_at: dt.datetime | None = None
    draft: bool = False
    bot: bool = False
    labels: list[str] = []

    @classmethod
    def from_api(cls, raw: dict) -> BacklogItem:
        return cls(
            number=raw["number"],
            is_pr="pull_request" in raw,
            created_at=raw["created_at"],
            closed_at=raw.get("closed_at"),
            draft=bool(raw.get("draft")),
            bot=(raw.get("user") or {}).get("type") == "Bot",
            labels=[label["name"] for label in raw.get("labels", [])],
        )

    def open_at(self, at: dt.datetime) -> bool:
        return self.created_at <= at and (self.closed_at is None or self.closed_at > at)


def age_bucket(created_at: dt.datetime, at: dt.datetime) -> str:
    days = (at - created_at).total_seconds() / 86400
    for name, lo, hi in AGE_BUCKETS:
        if days >= lo and (hi is None or days < hi):
            return name
    raise ValueError(f"negative age: created {created_at} after {at}")


def _counts(items: list[BacklogItem], at: dt.datetime, *, live: bool, pr: bool) -> dict[str, int]:
    counts = {"open": len(items)} | {name: 0 for name, _, _ in AGE_BUCKETS}
    for item in items:
        counts[age_bucket(item.created_at, at)] += 1
    if live:
        counts["unlabeled"] = sum(not item.labels for item in items)
        counts["awaiting_reporter"] = sum(
            bool(AWAITING_REPORTER_LABELS & set(item.labels)) for item in items
        )
        if pr:
            counts["draft"] = sum(item.draft for item in items)
            counts["bot"] = sum(item.bot for item in items)
    return counts


def snapshot(
    items: Iterable[BacklogItem], at: dt.datetime, repo: str, *, source: str = "live"
) -> dict:
    """Bucket the items open at `at`. Live rows also count labels, drafts and bots."""
    live = source == "live"
    open_items = [item for item in items if item.open_at(at)]
    return {
        "at": at.isoformat(),
        "repo": repo,
        "source": source,
        "issues": _counts([i for i in open_items if not i.is_pr], at, live=live, pr=False),
        "prs": _counts([i for i in open_items if i.is_pr], at, live=live, pr=True),
    }


def backfill(items: list[BacklogItem], now: dt.datetime, repo: str, days: int) -> list[dict]:
    """One reconstructed row per UTC midnight for the last `days` days, oldest first."""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return [
        snapshot(items, midnight - dt.timedelta(days=d), repo, source="backfill")
        for d in range(days, 0, -1)
    ]


@contextmanager
def _locked(path: Path) -> Generator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def append_rows(path: Path, rows: list[dict]) -> None:
    with _locked(path), open(path, "a") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def replace_backfill(path: Path, rows: list[dict], repo: str) -> int:
    """Swap this repo's backfill rows for `rows`, keeping every live row. Returns rows kept."""
    with _locked(path):
        kept = [
            line
            for line in (path.read_text().splitlines() if path.exists() else [])
            if line.strip()
            and not ((row := json.loads(line))["source"] == "backfill" and row["repo"] == repo)
        ]
        tmp = path.with_name(path.name + ".new")
        tmp.write_text(
            "".join(json.dumps(r) + "\n" for r in rows) + "".join(k + "\n" for k in kept)
        )
        os.replace(tmp, path)
        return len(kept)
