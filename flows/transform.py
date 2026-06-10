import os
import subprocess
from pathlib import Path

from prefect import flow, get_run_logger, task

ANALYTICS_DIR = Path(__file__).parent.parent / "analytics"

# Tables hub serves. Anything not on this list is dropped from hub.duckdb so
# the file stays small (hub mmaps everything in it). Keep this in sync with
# web/src/lib/server/{loaders,discovery}.ts.
HUB_TABLES = ("hub_action_items", "raw_github_issues", "raw_liked_posts", "raw_llm_spend")

EMPTY_HUB_TABLES = {
    "raw_llm_spend": """
        CREATE OR REPLACE TABLE raw_llm_spend (
            id VARCHAR,
            recorded_at TIMESTAMP,
            flow_name VARCHAR,
            flow_run_id VARCHAR,
            task_name VARCHAR,
            provider VARCHAR,
            model VARCHAR,
            request_count INTEGER,
            input_tokens INTEGER,
            cache_write_tokens INTEGER,
            cache_read_tokens INTEGER,
            output_tokens INTEGER,
            total_tokens INTEGER,
            input_cost_usd DOUBLE,
            output_cost_usd DOUBLE,
            total_cost_usd DOUBLE,
            metadata_json VARCHAR
        )
    """,
}


@task
def export_hub_db(src: Path, dst: Path) -> int:
    """Write a hub-only duckdb containing just the tables hub queries.

    Hub used to mount the full analytics.duckdb (~1 GB, mostly
    raw_phi_observations which hub never queries) and OOMed periodically
    because duckdb mmaps the whole file. This pulls just the four tables
    hub actually reads into a separate file (~50–100 MB).

    Atomic via tmp-file + rename: hub's snapshot logic mtime-checks the
    source path on each request, so the rename triggers a clean refresh
    on the next request.
    """
    import duckdb

    logger = get_run_logger()
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
            try:
                con.execute(
                    f"CREATE OR REPLACE TABLE {tbl} AS SELECT * FROM analytics.main.{tbl}"
                )
            except duckdb.CatalogException:
                if tbl not in EMPTY_HUB_TABLES:
                    raise
                con.execute(EMPTY_HUB_TABLES[tbl])
        con.execute("DETACH analytics")
    finally:
        con.close()

    os.replace(tmp, dst)
    size_mb = dst.stat().st_size / 1024 / 1024
    logger.info(f"wrote {dst} ({size_mb:.1f} MB) with tables: {', '.join(HUB_TABLES)}")
    return int(size_mb)


@flow(name="transform", log_prints=True)
def transform():
    # lazy imports: dbt-common -> mashumaro has Python 3.14 compat issues at
    # module load time; importing inside the function defers until flow runs
    from datetime import timedelta
    from prefect_dbt.core._orchestrator import (
        CacheConfig,
        ExecutionMode,
        PrefectDbtOrchestrator,
        TestStrategy,
    )
    from prefect_dbt.core.settings import PrefectDbtSettings

    logger = get_run_logger()

    # compile manifest.json so PrefectDbtOrchestrator can parse the project
    logger.info("compiling dbt project...")
    result = subprocess.run(
        ["uv", "run", "dbt", "compile",
         "--project-dir", str(ANALYTICS_DIR),
         "--profiles-dir", str(ANALYTICS_DIR / "profiles")],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"dbt compile failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
    logger.info("dbt compile OK")

    settings = PrefectDbtSettings(
        project_dir=ANALYTICS_DIR,
        profiles_dir=ANALYTICS_DIR / "profiles",
    )
    orchestrator = PrefectDbtOrchestrator(
        settings=settings,
        execution_mode=ExecutionMode.PER_NODE,
        concurrency=1,
        cache=CacheConfig(expiration=timedelta(hours=1)),
        test_strategy=TestStrategy.DEFERRED,
        create_summary_artifact=True,
    )
    orchestrator.run_build()

    src = Path(os.environ.get("ANALYTICS_DB_PATH", "/prefect-analytics/analytics.duckdb"))
    dst = src.parent / "hub.duckdb"
    export_hub_db(src, dst)


if __name__ == "__main__":
    transform()
