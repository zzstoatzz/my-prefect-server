"""
Flow run cleanup — deletes old terminal flow runs via the Prefect API.

Adapted from https://github.com/PrefectHQ/canary-flows/blob/main/flows/database-cleanup.py
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Literal

from prefect import flow, get_run_logger, task
from prefect.client.orchestration import get_client
from prefect.client.schemas.filters import (
    FlowRunFilter,
    FlowRunFilterStartTime,
    FlowRunFilterState,
    FlowRunFilterStateName,
)
from prefect.exceptions import ObjectNotFound
from pydantic import BaseModel, Field


class RetentionConfig(BaseModel):
    days_to_keep: int = Field(
        default=30,
        ge=1,
        le=365,
        description="Days of flow run history to retain (1-365)",
        json_schema_extra={"position": 0},
    )
    states_to_clean: list[Literal["Completed", "Failed", "Cancelled", "Crashed"]] = Field(
        default=["Completed", "Failed", "Cancelled", "Crashed"],
        description="Terminal states to include in cleanup",
        json_schema_extra={"position": 1},
    )
    batch_size: int = Field(
        default=100,
        ge=10,
        le=1000,
        description="Deletes per batch (10-1000)",
        json_schema_extra={"position": 2},
    )
    rate_limit_delay: float = Field(
        default=0.5,
        ge=0.0,
        le=10.0,
        description="Seconds between batches (0-10)",
        json_schema_extra={"position": 3},
    )
    dry_run: bool = Field(
        default=True,
        description="Preview only — no deletions",
        json_schema_extra={"position": 4},
    )


@task
async def delete_old_flow_runs(config: RetentionConfig) -> dict:
    logger = get_run_logger()
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.days_to_keep)
    logger.info(f"cutoff: {cutoff.strftime('%Y-%m-%d %H:%M')} UTC")
    logger.info(f"states: {', '.join(config.states_to_clean)}")

    async with get_client() as client:
        flow_run_filter = FlowRunFilter(
            start_time=FlowRunFilterStartTime(before_=cutoff),
            state=FlowRunFilterState(
                name=FlowRunFilterStateName(any_=config.states_to_clean)
            ),
        )

        flow_runs = await client.read_flow_runs(
            flow_run_filter=flow_run_filter, limit=config.batch_size
        )

        if not flow_runs:
            logger.info("nothing to clean up")
            return {"deleted": 0, "failed": 0}

        more = "+" if len(flow_runs) == config.batch_size else ""
        logger.info(f"found: {len(flow_runs)}{more} runs to clean")

        if config.dry_run:
            for fr in flow_runs[:5]:
                logger.info(f"  [dry run] would delete: {fr.name} ({fr.state.name}, started {fr.start_time})")
            if len(flow_runs) > 5:
                logger.info(f"  ... and {len(flow_runs) - 5} more")
            return {"deleted": 0, "failed": 0, "dry_run": True}

        deleted_total = failed_total = 0

        while flow_runs:
            async def _delete(fr_id):
                try:
                    await client.delete_flow_run(fr_id)
                    return None
                except ObjectNotFound:
                    return None  # already gone, idempotent
                except Exception as e:
                    return str(e)

            results = await asyncio.gather(*[_delete(fr.id) for fr in flow_runs])
            errors = [e for e in results if e]
            deleted_total += len(flow_runs) - len(errors)
            failed_total += len(errors)
            logger.info(f"batch: {len(flow_runs) - len(errors)}/{len(flow_runs)} | total deleted: {deleted_total:,}")
            for err in errors[:3]:
                logger.warning(f"  error: {err}")

            if config.rate_limit_delay > 0:
                await asyncio.sleep(config.rate_limit_delay)

            flow_runs = await client.read_flow_runs(
                flow_run_filter=flow_run_filter, limit=config.batch_size
            )

        logger.info(f"done: {deleted_total:,} deleted, {failed_total:,} failed")
        return {"deleted": deleted_total, "failed": failed_total}


def _cleanup_run_name():
    import prefect.runtime

    config = prefect.runtime.flow_run.parameters.get("config", {})
    mode = "dry-run" if config.get("dry_run", True) else "live"
    days = config.get("days_to_keep", 30)
    return f"{mode}-{days}d"


@flow(name="cleanup", flow_run_name=_cleanup_run_name, log_prints=True)
async def cleanup(config: RetentionConfig = RetentionConfig()) -> dict:
    """Delete old terminal flow runs.

    Defaults to dry_run=True — set dry_run=False to actually delete.
    """
    logger = get_run_logger()
    mode = "DRY RUN" if config.dry_run else "LIVE"
    logger.info(f"mode: {mode} | retention: {config.days_to_keep} days")
    return await delete_old_flow_runs(config)


if __name__ == "__main__":
    asyncio.run(cleanup())
