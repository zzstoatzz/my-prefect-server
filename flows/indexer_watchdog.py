"""
indexer-watchdog — catch the failure mode a state hook can't: a `typeahead-index`
build that never *ran*. A failed run alerts via its own on_failure hook; but a run
that's silently not scheduled/picked-up emits no event, so nothing fires. This flow
runs daily, checks the age of the last Completed `typeahead-index` run, and pings
Discord (the `discord-alerts` block) if it's older than the staleness threshold.

Limitation: it runs on the same home worker it watches, so it can't alert if that
worker is wholly down (neither would run). It catches build failures, schedule
breakage, and no-runs — the common cases. Whole-box outage is covered separately by
the logfire heartbeat/absence alerting on the ingester.
"""

from datetime import datetime, timezone

from prefect import flow, get_run_logger
from prefect.blocks.notifications import DiscordWebhook
from prefect.client.orchestration import get_client
from prefect.client.schemas.filters import (
    DeploymentFilter,
    DeploymentFilterName,
    FlowRunFilter,
    FlowRunFilterState,
    FlowRunFilterStateName,
)
from prefect.client.schemas.sorting import FlowRunSort

WATCHED = "typeahead-index"
STALE_HOURS = 84  # every-3-day cadence (72h) + ~build time + margin


def _alert(body: str, subject: str) -> None:
    try:
        DiscordWebhook.load("discord-alerts").notify(body=body, subject=subject)
    except Exception as e:  # noqa: BLE001 — alerting must never raise
        print(f"discord alert failed: {e}")


@flow(name="indexer-watchdog", log_prints=True)
async def indexer_watchdog(stale_hours: int = STALE_HOURS):
    logger = get_run_logger()
    async with get_client() as c:
        runs = await c.read_flow_runs(
            deployment_filter=DeploymentFilter(name=DeploymentFilterName(any_=[WATCHED])),
            flow_run_filter=FlowRunFilter(
                state=FlowRunFilterState(name=FlowRunFilterStateName(any_=["Completed"]))
            ),
            sort=FlowRunSort.END_TIME_DESC,
            limit=1,
        )

    if not runs:
        _alert(
            body=f"⚠️ **{WATCHED}** has NO completed runs on record — has it ever published a snapshot?",
            subject=f"{WATCHED} watchdog",
        )
        logger.warning("no completed runs found")
        return

    last = runs[0]
    end = last.end_time or last.expected_start_time
    age_h = (datetime.now(timezone.utc) - end).total_seconds() / 3600
    print(f"last completed: {last.name} at {end} ({age_h:.1f}h ago); threshold {stale_hours}h")

    if age_h > stale_hours:
        _alert(
            body=(
                f"⚠️ **{WATCHED}** last completed **{age_h:.0f}h ago** (`{last.name}`), "
                f"past the {stale_hours}h threshold. The snapshot is going stale — "
                f"check the home worker / the last build."
            ),
            subject=f"{WATCHED} STALE",
        )
        logger.warning(f"STALE: {age_h:.1f}h since last completed run")
    else:
        logger.info(f"healthy: last completed {age_h:.1f}h ago (< {stale_hours}h)")
