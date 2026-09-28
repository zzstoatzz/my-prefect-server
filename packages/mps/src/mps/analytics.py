import fcntl
import json
import logging
import os
from pathlib import Path

from mps.spend import RAW_LLM_SPEND_SCHEMA

# Tables hub serves. Anything not on this list is dropped from hub.duckdb so
# the file stays small (hub mmaps everything in it). Keep this in sync with
# web/src/lib/server/{loaders,discovery}.ts.
HUB_TABLES = ("hub_action_items", "raw_github_issues", "raw_liked_posts")


def export_hub_db(src: Path, dst: Path) -> int:
    """Write a hub-only duckdb containing just the tables hub queries.

    Hub used to mount the full analytics.duckdb (~1 GB, mostly
    raw_phi_observations which hub never queries) and OOMed periodically
    because duckdb mmaps the whole file. This pulls just the three tables
    hub actually reads into a separate file (~50–100 MB).

    Atomic via tmp-file + rename: hub's snapshot logic mtime-checks the
    source path on each request, so the rename triggers a clean refresh
    on the next request.
    """
    import duckdb

    logger = logging.getLogger(__name__)
    tmp = dst.with_suffix(dst.suffix + ".new")
    if tmp.exists():
        tmp.unlink()

    # open the new hub file as the RW main connection, then ATTACH source
    # READ_ONLY (dbt may still be reading it). attaching the other way
    # around inherits the parent connection's read-only mode and CREATE
    # TABLE fails with "database does not exist" on the writer side.
    con = duckdb.connect(str(tmp))
    try:
        con.execute(f"ATTACH '{src}' AS analytics (READ_ONLY)")
        for tbl in HUB_TABLES:
            con.execute(f"CREATE OR REPLACE TABLE {tbl} AS SELECT * FROM analytics.main.{tbl}")
        con.execute("DETACH analytics")
    finally:
        con.close()

    os.replace(tmp, dst)
    size_mb = dst.stat().st_size / 1024 / 1024
    logger.info(f"wrote {dst} ({size_mb:.1f} MB) with tables: {', '.join(HUB_TABLES)}")
    return int(size_mb)


def import_spend_log(log_path: Path, analytics_db: Path) -> int:
    """Materialize the live append-only spend log into analytics.duckdb."""
    import duckdb

    logger = logging.getLogger(__name__)
    con = duckdb.connect(str(analytics_db))
    try:
        con.execute(RAW_LLM_SPEND_SCHEMA)
        if not log_path.exists():
            logger.info(f"no LLM spend log found at {log_path}")
            return 0

        rows = []
        with log_path.open("r", encoding="utf-8") as fp:
            fcntl.flock(fp.fileno(), fcntl.LOCK_SH)
            try:
                lines = fp.read().splitlines()
            finally:
                fcntl.flock(fp.fileno(), fcntl.LOCK_UN)

        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("skipping malformed LLM spend log line")
                continue
            if not event.get("id"):
                logger.warning("skipping LLM spend log event without id")
                continue
            rows.append(
                (
                    event.get("id", ""),
                    event.get("recorded_at"),
                    event.get("flow_name", ""),
                    event.get("flow_run_id", ""),
                    event.get("task_name", ""),
                    event.get("provider", ""),
                    event.get("model", ""),
                    int(event.get("request_count") or 0),
                    int(event.get("input_tokens") or 0),
                    int(event.get("cache_write_tokens") or 0),
                    int(event.get("cache_read_tokens") or 0),
                    int(event.get("output_tokens") or 0),
                    int(event.get("total_tokens") or 0),
                    event.get("input_cost_usd"),
                    event.get("output_cost_usd"),
                    event.get("total_cost_usd"),
                    json.dumps(
                        event.get("metadata") or {},
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
            )

        if rows:
            con.begin()
            con.executemany(
                "INSERT OR REPLACE INTO raw_llm_spend VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            con.commit()
        logger.info(f"materialized {len(rows)} LLM spend events from {log_path}")
        return len(rows)
    finally:
        con.close()
