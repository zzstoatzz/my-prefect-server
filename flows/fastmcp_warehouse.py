"""Sync PrefectHQ/fastmcp issues and PRs into analytics.duckdb for the triage models.

Each run fetches what changed since the newest stored `updated_at` (with an
hour of overlap); the first run walks every issue and PR. The dbt models in
analytics/models/fastmcp/ turn the rows into throughput, contributor wait and
stuck-PR tables that phi and Grafana read.
"""

import datetime as dt
import os

import httpx
from mps.blocks import secret_sync
from mps.db import fastmcp_watermark, write_fastmcp_items
from mps.fastmcp_items import FastmcpItem, fetch_updated
from mps.github import gh_headers
from prefect import flow, task
from prefect.cache_policies import NONE

OVERLAP = dt.timedelta(hours=1)


def _db_path() -> str:
    return os.environ.get(
        "ANALYTICS_DB_PATH",
        os.environ.get("PREFECT_LOCAL_STORAGE_PATH", "/tmp") + "/analytics.duckdb",
    )


@task(cache_policy=NONE, retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
def fetch_changed(token: str, kind: str, since: dt.datetime | None) -> list[FastmcpItem]:
    with httpx.Client(headers=gh_headers(token), timeout=60.0) as client:
        items = list(fetch_updated(client, kind, since))
    print(f"  {len(items)} {kind}s updated since {since or 'the beginning'}")
    return items


@flow(name="fastmcp-warehouse", log_prints=True, timeout_seconds=1800)
def fastmcp_warehouse() -> int:
    token = secret_sync("github-token")
    db = _db_path()
    watermark = fastmcp_watermark(db)
    since = watermark - OVERLAP if watermark else None
    items = [*fetch_changed(token, "issue", since), *fetch_changed(token, "pr", since)]
    total = write_fastmcp_items(items, db)
    print(f"  {total} fastmcp issues and PRs stored")
    return total


if __name__ == "__main__":
    fastmcp_warehouse()
