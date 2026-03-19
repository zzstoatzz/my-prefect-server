# k8s: prefect uses system python, but uv sync in the pull step creates .venv/
# with extra deps (duckdb, prefect-dbt, etc). inject it into sys.path before any imports.
import sys as _sys
from pathlib import Path as _Path
_site = _Path(__file__).parent.parent / ".venv" / "lib" / f"python{_sys.version_info.major}.{_sys.version_info.minor}" / "site-packages"
if _site.exists() and str(_site) not in _sys.path:
    _sys.path.insert(0, str(_site))

import subprocess
from datetime import timedelta
from pathlib import Path

from prefect import flow, get_run_logger
from prefect_dbt.core._orchestrator import (
    CacheConfig,
    ExecutionMode,
    PrefectDbtOrchestrator,
    TestStrategy,
)
from prefect_dbt.core.settings import PrefectDbtSettings

ANALYTICS_DIR = Path(__file__).parent.parent / "analytics"


@flow(name="enrich", log_prints=True)
def enrich():
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
        raise RuntimeError(f"dbt compile failed:\n{result.stderr}")
    logger.info("dbt compile OK")

    settings = PrefectDbtSettings(
        project_dir=ANALYTICS_DIR,
        profiles_dir=ANALYTICS_DIR / "profiles",
    )
    orchestrator = PrefectDbtOrchestrator(
        settings=settings,
        execution_mode=ExecutionMode.PER_NODE,
        cache=CacheConfig(expiration=timedelta(hours=1)),
        test_strategy=TestStrategy.DEFERRED,
        create_summary_artifact=True,
    )
    orchestrator.run_build()


if __name__ == "__main__":
    enrich()
