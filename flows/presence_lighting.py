"""Read private presence and ask the generic Pi flow for a decision, without actuation."""

import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
from mps.pi import Toolset
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

from flows.pi_agent import Agent, pi_agent


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1, persist_result=False)
def store_presence(report: PresenceUpdate) -> PresenceRecord:
    password = Path(os.environ["PRESENCE_CREDENTIAL_FILE"]).read_text().strip()
    with httpx.Client(base_url=PDS, timeout=10) as client:
        authenticate_presence(client, password)
        return apply_presence_update(client, report, datetime.now(UTC))


@flow(name="report-presence", timeout_seconds=300, log_prints=True)
def report_presence(report: PresenceUpdate) -> str:
    with concurrency("home-presence-writer", strict=True):
        store_presence(report)
        return presence_lighting()


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1, persist_result=False)
def current_presence() -> PresenceRecord:
    password = Path(os.environ["PRESENCE_CREDENTIAL_FILE"]).read_text().strip()
    return read_presence(password)


@flow(name="presence-lighting", timeout_seconds=240, log_prints=True)
def presence_lighting() -> str:
    record = current_presence()
    action = proposed_action(record.value, datetime.now(UTC))
    result = pi_agent(
        prompt=f"The validated presence policy proposes {action}. Return that decision.",
        instructions=(
            "You report a proposed lighting decision. Return exactly the supplied "
            "decision: NO_CHANGE, LIGHTS_OFF, or ARRIVAL_LOOK. You have no tools "
            "and must not control lights."
        ),
        agent=Agent(
            provider="openai-codex",
            model="gpt-5.6-luna",
            thinking="low",
            toolset=Toolset(names=[]),
        ),
        timeout_seconds=120,
    ).strip()
    if result != action:
        raise ValueError("Pi returned a decision that differs from the presence policy")
    print(f"presence record {record.cid}: {result}; no actuation")
    return result
