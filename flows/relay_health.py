"""Turn relay-eval's verdict on the operator's relays into alert events.

Phi used to watch these relays itself, so whether anyone heard about a
regression was a model's decision. This flow owns the state machine instead:
it emits one event when a watched relay falls behind the network or returns,
and one when relay-eval stops scoring at all. The `relay health -> discord`
automation turns those into alert incidents, and `.recovered` resolves them.

The verdict is /api/status's network-absolute `behind_lately`, not the status
in /api/relays. On 2026-08-18 that self-relative classifier re-labelled
zlay's 18% coverage "nominal" within 12 hours, because the outage had become
its own baseline.

State advances only after the Prefect API accepts the events, so a failed
delivery is retried by the next run instead of being forgotten.
"""

import time
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import httpx
from prefect import flow, task
from prefect.client.orchestration import get_client
from prefect.variables import Variable

STATE = "relay-health"
SITE = "https://relay-eval.waow.tech"
STATUS = f"{SITE}/api/status"
WATCHED = ("zlay.waow.tech", "relay.waow.tech", "stream.waow.tech", "jetstream.waow.tech")
SERVICE = "relay-eval"
# relay-eval scores every 30 minutes; two missed runs and the verdict is stale
STALE_AFTER = 75 * 60


def _epoch(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def assess(status: dict, now: float) -> dict[str, dict[str, Any]]:
    """Current condition of relay-eval itself and of each watched relay it scores."""
    until = status["window"]["until"]
    age = now - _epoch(until)
    conditions: dict[str, dict[str, Any]] = {
        SERVICE: {
            "firing": age > STALE_AFTER,
            "at": until,
            "summary": (
                f"relay-eval has not completed an eval run since {until} "
                f"({age / 60:.0f} minutes); relay coverage is unmonitored. {SITE}"
            )
            if age > STALE_AFTER
            else f"relay-eval is scoring relays again (latest run {until}). {SITE}",
        }
    }
    if conditions[SERVICE]["firing"]:
        return conditions
    for relay in status["relays"]:
        host = relay["host"]
        if host not in WATCHED:
            continue
        latest = relay["latest"]
        disconnected = latest["connected"] is not True
        firing = relay["behind_lately"] is True or disconnected
        if firing:
            summary = (
                f"{host} is behind the network in {relay['behind_runs']}/{relay['runs']} recent "
                f"eval runs (average coverage {relay['avg_coverage_pct']:.0f}%, latest "
                f"{latest['coverage_pct']:.0f}%{', disconnected' if disconnected else ''}). "
                f"{SITE}/api/relays/history?name={host}"
            )
        else:
            summary = (
                f"{host} is back in line with the network "
                f"(latest coverage {latest['coverage_pct']:.0f}% at {latest['ts']})."
            )
        conditions[host] = {"firing": firing, "at": latest["ts"], "summary": summary}
    return conditions


def transitions(state: dict, conditions: dict, now: float) -> tuple[dict, list[dict]]:
    """Next state and the events for every condition that changed.

    A relay first seen healthy is recorded silently; first seen firing, it
    alerts. Event IDs derive from the subject, transition and verdict time,
    so re-sending after a failed delivery does not mint a different event.
    """
    occurred = datetime.fromtimestamp(now, UTC).isoformat()
    following = dict(state)
    events = []
    for subject, condition in conditions.items():
        current = "firing" if condition["firing"] else "ok"
        previous = state.get(subject)
        following[subject] = current
        if current == previous or (previous is None and current == "ok"):
            continue
        name = (
            "relay-eval.recovered"
            if current == "ok"
            else ("relay-eval.stale" if subject == SERVICE else "relay-eval.behind")
        )
        resource = SERVICE if subject == SERVICE else f"relay-eval.relay.{subject}"
        events.append(
            {
                "id": str(uuid5(NAMESPACE_URL, f"{resource}:{name}:{condition['at']}")),
                "occurred": occurred,
                "event": name,
                "resource": {"prefect.resource.id": resource, "prefect.resource.name": subject},
                "related": [],
                "payload": {"summary": condition["summary"], "verdict_at": condition["at"]},
            }
        )
    return following, events


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
async def read_status() -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(STATUS)
        response.raise_for_status()
        return response.json()


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
async def deliver(events: list[dict]) -> None:
    async with get_client() as client:
        response = await client.request("POST", "/events", json=events)
        response.raise_for_status()


@flow(name="relay-health", log_prints=True, timeout_seconds=120)
async def relay_health() -> dict[str, Any]:
    state = await Variable.aget(STATE, default={})
    if not isinstance(state, dict):
        raise ValueError("invalid relay health state")
    status = await read_status()
    now = time.time()
    state, events = transitions(state, assess(status, now), now)
    if events:
        await deliver(events)
    await Variable.aset(STATE, state, overwrite=True)
    report = {"state": state, "emitted": [e["payload"]["summary"] for e in events]}
    print(report)
    return report
