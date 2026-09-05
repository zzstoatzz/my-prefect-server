"""Store private presence and optionally apply the agreed lighting preset."""

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
from mps.presence import (
    PDS,
    PresenceRecord,
    PresenceUpdate,
    apply_presence_update,
    authenticate_presence,
    proposed_action,
    read_presence,
)
from prefect import flow, task
from prefect.concurrency.sync import concurrency


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1, persist_result=False)
def store_presence(report: PresenceUpdate) -> PresenceRecord:
    password = Path(os.environ["PRESENCE_CREDENTIAL_FILE"]).read_text().strip()
    with httpx.Client(base_url=PDS, timeout=10) as client:
        authenticate_presence(client, password)
        return apply_presence_update(client, report, datetime.now(UTC))


@flow(name="report-presence", timeout_seconds=300, log_prints=True)
def report_presence(report: PresenceUpdate, apply_lighting: bool = False) -> str:
    with concurrency("home-presence-writer", strict=True):
        record = store_presence(report)
        if record.value.observedAt != report.observedAt:
            return "NO_CHANGE"
        if apply_lighting:
            return apply_presence_lighting(record)
        return presence_lighting()


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1, persist_result=False)
def current_presence() -> PresenceRecord:
    password = Path(os.environ["PRESENCE_CREDENTIAL_FILE"]).read_text().strip()
    return read_presence(password)


@flow(name="presence-lighting", timeout_seconds=240, log_prints=True)
def presence_lighting() -> str:
    record = current_presence()
    action = proposed_action(record.value, datetime.now(UTC))
    print(f"presence record {record.cid}: {action}; no actuation")
    return action


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1, persist_result=False)
def apply_presence_lighting(record: PresenceRecord) -> str:
    """Called only under the writer lock; failed/partial writes never advance the marker."""
    marker = Path(os.environ["PRESENCE_LIGHTING_STATE_FILE"])
    state = record.value.state
    if state == "unknown":
        return "NO_CHANGE"
    if marker.exists() and marker.read_text().strip() == state:
        return "NO_CHANGE"
    from mps.lighting import apply_lighting

    verified_lights = asyncio.run(apply_lighting(state))
    temporary = marker.with_suffix(".tmp")
    temporary.write_text(state + "\n")
    temporary.replace(marker)
    print(f"{state}: verified {verified_lights} lights")
    return "ARRIVAL_LOOK" if state == "home" else "LIGHTS_OFF"
