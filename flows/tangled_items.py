"""
Fetch issues, PRs, and comments from the tangled.org PDS and persist to DuckDB.

No auth needed — PDS records are public. Low volume, full resync each run.
"""

import os

import httpx
from prefect import flow, get_run_logger, task

from mps.db import write_tangled_items
from mps.tangled import (
    PDS_BASE,
    TangledItem,
    fetch_items,
    fetch_repo_at_uris,
)

COLLECTIONS = [
    "sh.tangled.repo.issue",
    "sh.tangled.repo.pull",
    "sh.tangled.repo.issue.comment",
    "sh.tangled.repo.pull.comment",
]


@task
def fetch_all_items() -> list[TangledItem]:
    """Fetch issues, PRs, and comments from the PDS."""
    logger = get_run_logger()
    with httpx.Client(base_url=PDS_BASE, timeout=30) as client:
        repo_uris = fetch_repo_at_uris(client)
        logger.info(f"found {len(repo_uris)} target repos on PDS")

        items: list[TangledItem] = []
        for collection in COLLECTIONS:
            batch = fetch_items(client, collection, repo_uris)
            logger.info(f"{collection}: {len(batch)} records")
            items.extend(batch)

    return items


@task
def persist_to_duckdb(items: list[TangledItem]) -> int:
    db_path = os.environ.get(
        "ANALYTICS_DB_PATH",
        os.environ.get("PREFECT_LOCAL_STORAGE_PATH", "/tmp") + "/analytics.duckdb",
    )
    return write_tangled_items(items, db_path)


@flow(name="tangled-items", log_prints=True)
def tangled_items():
    logger = get_run_logger()

    items = fetch_all_items()
    if not items:
        logger.info("no tangled items found")
        return

    total = persist_to_duckdb(items)
    logger.info(f"persisted {len(items)} items; {total} total in raw_tangled_items")


if __name__ == "__main__":
    tangled_items()
