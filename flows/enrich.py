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
        raise RuntimeError(f"dbt compile failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
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
