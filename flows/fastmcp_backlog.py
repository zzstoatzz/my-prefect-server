"""Snapshot PrefectHQ/fastmcp's open issue and PR counts into an append-only series.

Every 30 minutes. Each run lists the open issues and PRs (one paginated
`/issues?state=open` walk, which includes PRs), buckets them by age, and
appends one row to `fastmcp-backlog.jsonl` beside analytics.duckdb on the home
box. The row shape is documented in `mps.github_backlog`. The same counts go to
the `fastmcp-backlog` table artifact, whose version history is readable from
anywhere through the Prefect API.

`backfill_days` > 0 also walks every issue and PR ever opened and rewrites the
reconstructed daily rows (`source="backfill"`), leaving live rows alone.

ad hoc:  uv run python flows/fastmcp_backlog.py               # writes /tmp/fastmcp-backlog.jsonl
         uv run python flows/fastmcp_backlog.py --backfill 365
read:    just fastmcp-backlog
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import httpx
from mps.blocks import secret_sync
from mps.github import gh_headers
from mps.github_backlog import BacklogItem, append_rows, backfill, replace_backfill, snapshot
from prefect import flow, task
from prefect.artifacts import create_table_artifact
from prefect.cache_policies import NONE

GITHUB_API = "https://api.github.com"
REPO = "PrefectHQ/fastmcp"


def backlog_path() -> Path:
    if explicit := os.environ.get("FASTMCP_BACKLOG_PATH"):
        return Path(explicit)
    if analytics := os.environ.get("ANALYTICS_DB_PATH"):
        return Path(analytics).parent / "fastmcp-backlog.jsonl"
    return Path("/tmp/fastmcp-backlog.jsonl")


@task(cache_policy=NONE, retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
def fetch_items(token: str, state: str) -> list[BacklogItem]:
    items: list[BacklogItem] = []
    url: str | None = f"{GITHUB_API}/repos/{REPO}/issues"
    params: dict | None = {"state": state, "per_page": 100}
    with httpx.Client(headers=gh_headers(token), timeout=30.0) as client:
        while url:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            items.extend(BacklogItem.from_api(raw) for raw in resp.json())
            url, params = resp.links.get("next", {}).get("url"), None
    print(f"  {len(items)} {state} issues + PRs")
    return items


@flow(name="fastmcp-backlog", log_prints=True, timeout_seconds=600)
def fastmcp_backlog(backfill_days: int = 0) -> dict:
    """Append one bucketed snapshot of fastmcp's open issues and PRs."""
    token = secret_sync("github-token")
    path = backlog_path()
    now = dt.datetime.now(dt.UTC)

    if backfill_days > 0:
        rows = backfill(fetch_items(token, "all"), now, REPO, backfill_days)
        kept = replace_backfill(path, rows, REPO)
        print(f"  backfilled {len(rows)} daily rows into {path} ({kept} other rows kept)")

    row = snapshot(fetch_items(token, "open"), now, REPO)
    append_rows(path, [row])
    print(f"  appended {row['at']} to {path}")

    create_table_artifact(
        key="fastmcp-backlog",
        table=[{"kind": kind, **row[kind]} for kind in ("issues", "prs")],
        description=f"{REPO} open backlog at {row['at']}",
    )
    return row


if __name__ == "__main__":
    import sys

    days = int(sys.argv[sys.argv.index("--backfill") + 1]) if "--backfill" in sys.argv else 0
    fastmcp_backlog(backfill_days=days)
