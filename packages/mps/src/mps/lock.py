"""single-writer coordination for analytics.duckdb.

duckdb allows exactly one read-write process per file. flows on independent
schedules (transform's dbt build, docket's archive, ingest) collide on the
file lock unless every writer holds the `analytics-duckdb-writer` slot — a
server-side global concurrency limit with limit=1 — so writers queue instead
of dying on the lock.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager

ANALYTICS_WRITER_LIMIT = "analytics-duckdb-writer"


@contextmanager
def analytics_write_slot() -> Iterator[None]:
    """hold the single analytics.duckdb writer slot; blocks until free.

    strict=True so a missing limit or unreachable server fails loudly rather
    than silently not enforcing; lease renewal failures are tolerated so a
    long dbt build survives a transient server blip. skipped entirely when no
    API URL is configured (direct local runs against a scratch db have no
    cross-process contention and no server to coordinate through).
    """
    if not os.environ.get("PREFECT_API_URL"):
        from prefect.settings import PREFECT_API_URL

        if not PREFECT_API_URL.value():
            yield
            return
    from prefect.concurrency.sync import concurrency

    with concurrency(
        ANALYTICS_WRITER_LIMIT,
        strict=True,
        raise_on_lease_renewal_failure=False,
    ):
        yield
