"""Store private presence and optionally apply the agreed lighting preset."""

import os
import subprocess
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


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1, persist_result=False)
def apply_presence_lighting(record: PresenceRecord) -> str:
    """Called only under the writer lock; failed/partial writes never advance the marker."""
    marker = Path(os.environ["PRESENCE_LIGHTING_STATE_FILE"])
    state = record.value.state
    if state == "unknown":
        return "NO_CHANGE"
    if marker.exists() and marker.read_text().strip() == state:
        return "NO_CHANGE"
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            "/home/stoat/.local/bin/uv",
            "run",
            "--with",
            "smart-home@git+https://github.com/PrefectHQ/fastmcp.git@e3fb4af36892e6477399df2597f0dd5abd469799#subdirectory=examples/smart_home",
            "--with",
            "fastmcp==4.0.3",
            "python",
            str(root / "scripts/presence_lights.py"),
            state,
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=120,
    )
    if result.returncode:
        raise RuntimeError(f"Lighting failed: {result.stderr[-4000:]}")
    temporary = marker.with_suffix(".tmp")
    temporary.write_text(state + "\n")
    temporary.replace(marker)
    print(result.stdout.strip())
    return "ARRIVAL_LOOK" if state == "home" else "LIGHTS_OFF"
