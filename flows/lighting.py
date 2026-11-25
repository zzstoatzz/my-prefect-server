"""Run ad hoc lighting changes and scheduled native Hue fades."""

import asyncio
from datetime import UTC, datetime, timedelta

from mps.home_lighting import LightChange
from mps.lighting import WindDownResult, apply_wind_down
from mps.wind_down import HOME_TIMEZONE, MAX_START_DELAY, WindDown, override_name
from prefect import flow, task
from prefect.concurrency.sync import concurrency
from prefect.runtime import flow_run
from prefect.variables import Variable


@flow(name="lighting", timeout_seconds=300, log_prints=True, retries=0)
def lighting(
    changes: list[LightChange] | None = None, wind_down: WindDown | None = None
) -> int | WindDownResult | str:
    """Apply requested changes or hand off a gradual wind-down to Hue."""
    if (changes is None) == (wind_down is None):
        raise ValueError("Give exactly one of changes or wind_down")
    with concurrency("home-presence-writer", strict=True):
        if changes is not None:
            return apply_changes(changes)
        assert wind_down is not None
        return start_wind_down(wind_down)


def start_wind_down(plan: WindDown) -> WindDownResult | str:
    start = flow_run.scheduled_start_time.astimezone(UTC)
    override = Variable.get(override_name(start.astimezone(HOME_TIMEZONE).date()))
    if (plan.override and override != flow_run.id) or (not plan.override and override is not None):
        print("Skipped wind-down because a different run owns this night's schedule")
        return "SUPERSEDED"
    if not timedelta(0) <= datetime.now(UTC) - start <= MAX_START_DELAY:
        print("Skipped wind-down outside its scheduled start window")
        return "STALE"
    end = start + timedelta(minutes=plan.minutes)
    result = asyncio.run(apply_wind_down(end, plan.brightness))
    print(f"Hue accepted fades ending at {end.astimezone(HOME_TIMEZONE)}: {result.accepted}")
    print(f"Skipped unreachable lights: {result.unreachable}; unconfirmed: {result.unconfirmed}")
    return result


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1, persist_result=False)
def apply_changes(changes: list[LightChange]) -> int:
    from mps.lighting import apply_changes as apply

    verified_lights = asyncio.run(apply(changes))
    print(f"verified {verified_lights} lights")
    return verified_lights
