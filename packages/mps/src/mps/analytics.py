import fcntl
import json
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from mps.spend import RAW_LLM_SPEND_SCHEMA

# DuckDB sizes its buffer pool from the cgroup's MemoryMax; under the Dagster
# unit's lower MemoryHigh the kernel throttles it instead, stalling builds.
DUCKDB_MEMORY_LIMIT = "2GB"


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


# Relations the pipeline used to produce and nothing writes or reads any more.
# dbt never drops a model removed from the project, so compaction does.
OBSOLETE_RELATIONS = (
    "hub_action_items",
    "int_emails_scored",
    "int_github_issues_scored",
    "int_tangled_items_scored",
    "known_contributors",
    "raw_likes",
    "raw_tangled_items",
    "stg_likes",
    "stg_tangled_items",
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
            con.execute(f"ATTACH '{staging}' AS dst")
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
