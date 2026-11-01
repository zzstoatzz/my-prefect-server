import fcntl
import json
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from mps.spend import RAW_LLM_SPEND_SCHEMA

# Tables hub serves. Anything not on this list is dropped from hub.duckdb so
# the file stays small (hub mmaps everything in it). Keep this in sync with
# web/src/lib/server/{loaders,discovery}.ts.
HUB_TABLES = ("hub_action_items", "raw_github_issues", "raw_liked_posts")

# DuckDB sizes its buffer pool from the cgroup's MemoryMax; under the Dagster
# unit's lower MemoryHigh the kernel throttles it instead, stalling builds.
DUCKDB_MEMORY_LIMIT = "2GB"

# Flows resolve DuckDB unpinned (2.x) while Dagster's dbt is locked to 1.5, and
# a new file gets the writing version's storage format. A rewrite must keep the
# original format (storage version 64) so every reader can still open it.
ANALYTICS_STORAGE_VERSION = "v1.0.0"


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
    con = duckdb.connect(str(tmp), config={"memory_limit": DUCKDB_MEMORY_LIMIT})
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
    con = duckdb.connect(str(analytics_db), config={"memory_limit": DUCKDB_MEMORY_LIMIT})
    try:
        con.execute("SET threads = 1")
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
            columns = [
                column[0]
                for column in con.execute("SELECT * FROM raw_llm_spend LIMIT 0").description
            ]
            with TemporaryDirectory(prefix="mps-spend-") as directory:
                batch = Path(directory) / "batch.jsonl"
                with batch.open("w", encoding="utf-8") as fp:
                    for ordinal, row in enumerate(rows):
                        json.dump(dict(zip(columns, row, strict=True)) | {"ordinal": ordinal}, fp)
                        fp.write("\n")
                con.begin()
                con.execute(
                    """
                    INSERT OR REPLACE INTO raw_llm_spend
                    WITH batch AS MATERIALIZED (
                        SELECT id, CAST(recorded_at AS TIMESTAMP) AS recorded_at,
                               * EXCLUDE (id, recorded_at)
                        FROM read_json(
                        ?, format='newline_delimited', columns={
                            id: 'VARCHAR', recorded_at: 'VARCHAR', flow_name: 'VARCHAR',
                            flow_run_id: 'VARCHAR', task_name: 'VARCHAR', provider: 'VARCHAR',
                            model: 'VARCHAR', request_count: 'INTEGER', input_tokens: 'INTEGER',
                            cache_write_tokens: 'INTEGER', cache_read_tokens: 'INTEGER',
                            output_tokens: 'INTEGER', total_tokens: 'INTEGER',
                            input_cost_usd: 'DOUBLE', output_cost_usd: 'DOUBLE',
                            total_cost_usd: 'DOUBLE', metadata_json: 'VARCHAR', ordinal: 'BIGINT'
                        }
                    ))
                    SELECT * EXCLUDE (ordinal) FROM batch
                    QUALIFY row_number() OVER (PARTITION BY id ORDER BY ordinal DESC) = 1
                    """,
                    [str(batch)],
                )
                con.commit()
        logger.info(f"materialized {len(rows)} LLM spend events from {log_path}")
        return len(rows)
    finally:
        con.close()


# Relations nothing writes or reads any more.
# dbt never drops a model removed from the project, so compaction does.
OBSOLETE_RELATIONS = (
    "raw_likes",
    "stg_likes",
)


def compact_database(analytics_db: Path) -> dict[str, int]:
    """Rewrite analytics.duckdb into a fresh file without deleted row versions.

    Run under the analytics writer slot. The copy is checked table by table
    before it replaces the original, so a failed or short copy leaves the
    original untouched.
    """
    import duckdb

    def count(con: duckdb.DuckDBPyConnection, relation: str) -> int:
        row = con.execute(f"SELECT count(*) FROM {relation}").fetchone()
        if row is None:
            raise RuntimeError(f"count(*) on {relation} returned no row")
        return int(row[0])

    before = analytics_db.stat()
    with duckdb.connect(str(analytics_db), config={"memory_limit": DUCKDB_MEMORY_LIMIT}) as con:
        kinds = dict(
            con.execute(
                "SELECT table_name, 'TABLE' FROM duckdb_tables() "
                "UNION ALL SELECT view_name, 'VIEW' FROM duckdb_views() WHERE NOT internal"
            ).fetchall()
        )
        for name in OBSOLETE_RELATIONS:
            if name in kinds:
                con.execute(f'DROP {kinds[name]} "{name}"')
        con.execute("CHECKPOINT")
        counts = {
            name: count(con, f'"{name}"')
            for (name,) in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()
        }

    staging = analytics_db.with_name(analytics_db.name + ".compacting")
    staging.unlink(missing_ok=True)
    staging.with_name(staging.name + ".wal").unlink(missing_ok=True)
    try:
        with duckdb.connect(config={"memory_limit": DUCKDB_MEMORY_LIMIT}) as con:
            con.execute(f"ATTACH '{analytics_db}' AS src (READ_ONLY)")
            con.execute(
                f"ATTACH '{staging}' AS dst (STORAGE_VERSION '{ANALYTICS_STORAGE_VERSION}')"
            )
            con.execute("COPY FROM DATABASE src TO dst")
            con.execute("CHECKPOINT dst")
            for name, expected in counts.items():
                copied = count(con, f'dst.main."{name}"')
                if copied != expected:
                    raise RuntimeError(f"{name}: copied {copied} of {expected} rows")
        os.chmod(staging, before.st_mode & 0o777)
        os.replace(staging, analytics_db)
    finally:
        staging.unlink(missing_ok=True)
        staging.with_name(staging.name + ".wal").unlink(missing_ok=True)
    return {
        "bytes_before": before.st_size,
        "bytes_after": analytics_db.stat().st_size,
        "tables": len(counts),
        "rows": sum(counts.values()),
    }
