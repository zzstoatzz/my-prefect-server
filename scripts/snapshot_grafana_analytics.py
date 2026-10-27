"""Publish an atomic analytics copy for Grafana's read-only DuckDB plugin."""

import os
import shutil
from pathlib import Path

os.umask(0o077)
source = Path.home() / "prefect-analytics/analytics.duckdb"
root = Path.home() / ".local/share/prefect-edge/monitoring/analytics"
root.mkdir(parents=True, exist_ok=True)
target = root / "analytics.duckdb"
before = source.stat()
if not target.exists() or (target.stat().st_size, target.stat().st_mtime_ns) != (
    before.st_size,
    before.st_mtime_ns,
):
    partial = root / "analytics.duckdb.partial"
    try:
        shutil.copy2(source, partial)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) == (
            after.st_size,
            after.st_mtime_ns,
            after.st_ino,
        ):
            partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
