import os
from datetime import timedelta
from pathlib import Path

from prefect import flow
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
