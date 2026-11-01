"""Compact analytics.duckdb nightly so deleted row versions never accumulate.

DuckDB keeps deleted and replaced row versions in the file until it is
rewritten. Writers only touch changed rows (`mps.db._upsert_changed`); this
rewrite bounds whatever churn remains and drops relations nothing produces.
"""

import os
from pathlib import Path

from mps.analytics import compact_database
from mps.lock import analytics_write_slot
from prefect import flow
from prefect.artifacts import create_table_artifact


@flow(name="analytics-maintenance", log_prints=True, timeout_seconds=900)
def analytics_maintenance() -> dict[str, int]:
    database = Path(os.environ["ANALYTICS_DB_PATH"])
    with analytics_write_slot():
        result = compact_database(database)
    print(
        f"compacted {database}: {result['bytes_before'] / 2**20:.1f} MiB -> "
        f"{result['bytes_after'] / 2**20:.1f} MiB, {result['tables']} tables, {result['rows']} rows"
    )
    create_table_artifact(
        key="analytics-compaction",
        table=[result],
        description="analytics.duckdb size before and after the nightly rewrite",
    )
    return result


if __name__ == "__main__":
    analytics_maintenance()
