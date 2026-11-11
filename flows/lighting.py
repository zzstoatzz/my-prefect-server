"""Apply any change to the home's lights and verify it by readback."""

import asyncio

from mps.home_lighting import LightChange
from prefect import flow, task
from prefect.concurrency.sync import concurrency


@flow(name="lighting", timeout_seconds=300, log_prints=True)
def lighting(changes: list[LightChange]) -> int:
    """apply the changes in order under the home writer lock; returns the lights verified."""
    with concurrency("home-presence-writer", strict=True):
        return apply_changes(changes)


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1, persist_result=False)
def apply_changes(changes: list[LightChange]) -> int:
    from mps.lighting import apply_changes as apply

    verified_lights = asyncio.run(apply(changes))
    print(f"verified {verified_lights} lights")
    return verified_lights
