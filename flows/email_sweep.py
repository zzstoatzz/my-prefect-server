"""Daily Proton inbox sweep: archive aged bulk and automated mail.

Rules live in ``mps.email_sweep``. Messages move to Archive, never Trash,
and keep their read state. The result lists every archived Message-ID so a
sweep can be reversed from the run page.
"""

import os
import shutil
import tempfile

import duckdb
from mps.blocks import secret_mapping_sync
from mps.email_sweep import sweep_inbox
from prefect import flow, get_run_logger, task


def _db_path() -> str:
    return os.environ.get(
        "ANALYTICS_DB_PATH",
        os.environ.get("PREFECT_LOCAL_STORAGE_PATH", "/tmp") + "/analytics.duckdb",
    )


@task
def load_categories() -> dict[str, tuple[str, float | None]]:
    """Classifier labels by Message-ID, read from a snapshot of the analytics db."""
    if not os.path.exists(_db_path()):
        return {}
    with tempfile.TemporaryDirectory(prefix="email-sweep-") as tmp:
        snapshot = os.path.join(tmp, "analytics.duckdb")
        shutil.copyfile(_db_path(), snapshot)
        con = duckdb.connect(snapshot, read_only=True)
        try:
            rows = con.execute(
                "SELECT message_id, category, confidence FROM raw_email_classifications"
            ).fetchall()
        except duckdb.CatalogException:
            rows = []
        finally:
            con.close()
    return {r[0]: (r[1], r[2]) for r in rows}


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1, persist_result=False)
def sweep(categories: dict, min_age_days: int, dry_run: bool) -> dict:
    creds = secret_mapping_sync("proton-bridge-creds")
    return sweep_inbox(
        os.environ.get("PROTON_BRIDGE_HOST", "127.0.0.1"),
        int(os.environ.get("PROTON_BRIDGE_PORT", "1143")),
        creds["username"],
        creds["password"],
        categories,
        min_age_days,
        dry_run,
    )


@flow(name="email-sweep", timeout_seconds=1800)
def email_sweep(min_age_days: int = 7, dry_run: bool = False) -> dict:
    result = sweep(load_categories(), min_age_days, dry_run)
    get_run_logger().info(
        "inbox %d: %s %d (%s), kept %d",
        result["inbox_before"],
        "would archive" if dry_run else "archived",
        result["would_archive"],
        ", ".join(f"{k} {v}" for k, v in sorted(result["reasons"].items())) or "none",
        result["kept"],
    )
    return result
