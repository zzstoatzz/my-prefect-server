import math
import re
from datetime import UTC, datetime, timedelta
from typing import Literal, TypeGuard

import httpx
from mps.blocks import secret_sync
from prefect import task
from prefect.cache_policies import NONE
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from flows.evergreen_checks import INVENTORY_URL, Project

MACHINES_API = "https://api.machines.dev/v1/"
METRICS_API = "https://api.fly.io/prometheus/personal/api/v1/query"
LOOKBACK = timedelta(hours=24)
METRICS_MAX_AGE = timedelta(minutes=3)


class Exit(BaseModel):
    model_config = ConfigDict(strict=True)
    oom_killed: bool = False
    requested_stop: bool | None = None
    exit_code: int | None = None
    restarting: bool = False


class EventRequest(BaseModel):
    exit_event: Exit | None = None


class Event(BaseModel):
    id: str
    type: str
    timestamp: int = Field(ge=0)
    request: EventRequest = Field(default_factory=EventRequest)


class Machine(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9]+$")
    state: str


class Observation(BaseModel):
    project: str
    app: str
    machine: str
    state: str
    checked_at: datetime
    available_mib: float | None = None
    available_percent: float | None = None
    recent_failures: list[str] = Field(default_factory=list)
    coverage: str = "Fly's retained machine events; not a complete restart history"


class Metric(BaseModel):
    metric: dict[str, str]
    value: tuple[float, str]


class MetricData(BaseModel):
    resultType: Literal["vector"]
    result: list[Metric]


class MetricResponse(BaseModel):
    status: Literal["success"]
    isPartial: Literal[False] = False
    data: MetricData


def targets(projects: list[Project]) -> list[tuple[str, str]]:
    selected = [
        (project.name, resource.name)
        for project in projects
        for resource in project.resources
        if resource.monitoring == "observe"
    ]
    apps = [app for _, app in selected]
    if len(set(apps)) != len(apps) or any(not re.fullmatch(r"[a-z0-9-]+", app) for app in apps):
        raise ValueError("Operational targets must have unique, valid Fly app names")
    return selected


def failure_events(events: list[Event], now: datetime) -> list[str]:
    failures = []
    for event in sorted(events, key=lambda item: item.timestamp):
        at = datetime.fromtimestamp(event.timestamp / 1000, UTC)
        if at > now + timedelta(seconds=30):
            raise ValueError("Fly returned a future machine event")
        if event.type != "exit" or at < now - LOOKBACK:
            continue
        exit_event = event.request.exit_event
        if exit_event is None:
            raise ValueError("Fly exit event is missing its exit details")
        if exit_event.oom_killed:
            reason = "OOM kill"
        elif exit_event.requested_stop:
            continue
        elif exit_event.exit_code not in (None, 0):
            reason = f"process exit {exit_event.exit_code}"
        elif exit_event.restarting and exit_event.requested_stop is False:
            reason = "unrequested restart"
        else:
            continue
        failures.append(f"{at.isoformat()}: {reason} (event {event.id})")
    return failures


def memory_sample(
    report: MetricResponse, app: str, machine: str, now: datetime
) -> tuple[float, float] | None:
    values: dict[str, float] = {}
    for result in report.data.result:
        if result.metric.get("app") != app or result.metric.get("instance") != machine:
            raise ValueError("Fly memory result does not match the requested machine")
        name = result.metric.get("__name__", "")
        if name not in {"fly_instance_memory_mem_available", "fly_instance_memory_mem_total"}:
            raise ValueError("Unexpected Fly memory metric")
        timestamp, raw_value = result.value
        age = now.timestamp() - timestamp
        value = float(raw_value)
        if (
            not math.isfinite(value)
            or value < 0
            or not -30 <= age <= METRICS_MAX_AGE.total_seconds()
        ):
            raise ValueError("Fly memory sample is invalid or stale")
        if name in values:
            raise ValueError("Duplicate Fly memory series for a machine")
        values[name] = value
    available = values.get("fly_instance_memory_mem_available")
    total = values.get("fly_instance_memory_mem_total")
    if available is None or total is None:
        return None
    if total <= 0 or available > total:
        raise ValueError("Fly memory headroom exceeds its capacity")
    return available / 1024**2, available / total * 100


def collect_app(client: httpx.Client, project: str, app: str, now: datetime) -> list[Observation]:
    response = client.get(f"apps/{app}/machines")
    response.raise_for_status()
    machines = TypeAdapter(list[Machine]).validate_json(response.content)
    if not machines:
        return [
            Observation(
                project=project,
                app=app,
                machine="none",
                state="no machines",
                checked_at=now,
                coverage="No current machines; destroyed-machine history was not queried",
            )
        ]
    observations = []
    for machine in machines:
        response = client.get(f"apps/{app}/machines/{machine.id}/events")
        response.raise_for_status()
        events = TypeAdapter(list[Event]).validate_json(response.content)
        observation = Observation(
            project=project,
            app=app,
            machine=machine.id,
            state=machine.state,
            checked_at=now,
            recent_failures=failure_events(events, now),
        )
        if machine.state == "started":
            response = client.get(
                METRICS_API,
                params={
                    "query": '{__name__=~"fly_instance_memory_mem_(available|total)",'
                    f'app="{app}",instance="{machine.id}"}}'
                },
            )
            response.raise_for_status()
            sample = memory_sample(
                MetricResponse.model_validate_json(response.content), app, machine.id, now
            )
            if sample is None:
                observation.coverage += "; current memory unknown"
            else:
                observation.available_mib, observation.available_percent = sample
        else:
            observation.coverage += "; memory not sampled for a non-running machine"
        observations.append(observation)
    return observations


@task(cache_policy=NONE, retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
def observe_fly(inventory_url: str = INVENTORY_URL) -> list[Observation]:
    with httpx.Client(timeout=5, follow_redirects=True) as client:
        response = client.get(inventory_url)
        response.raise_for_status()
        projects = TypeAdapter(list[Project]).validate_json(response.content)
    selected = targets(projects)
    if not selected:
        return []
    token = secret_sync("fly-api-token")
    headers = {"Authorization": token if token.startswith("FlyV1 ") else f"FlyV1 {token}"}
    now = datetime.now(UTC)
    with httpx.Client(base_url=MACHINES_API, headers=headers, timeout=5) as client:
        return [
            observation
            for project, app in selected
            for observation in collect_app(client, project, app, now)
        ]


def is_observation_list(result: object) -> TypeGuard[list[Observation]]:
    return isinstance(result, list) and all(isinstance(item, Observation) for item in result)


def observation_rows(result: object) -> list[str]:
    if isinstance(result, list) and not result:
        return []
    rows = [
        "",
        "## Fly operational observations",
        "",
        "Observation only; these findings do not emit alerts. VM headroom is not process RSS.",
        "",
        "| project / app / machine | state | available VM memory | retained failures (24h) | coverage |",
        "| --- | --- | --- | --- | --- |",
    ]
    if not is_observation_list(result):
        return [
            *rows,
            "| Fly observations | **UNKNOWN** | unknown | unknown | Collection failed; see task logs |",
        ]
    for item in result:
        headroom = (
            "unknown"
            if item.available_mib is None or item.available_percent is None
            else f"{item.available_mib:.1f} MiB ({item.available_percent:.1f}%)"
        )
        failures = "; ".join(item.recent_failures) or "none in retained events"
        rows.append(
            f"| {item.project} / {item.app} / {item.machine} | {item.state} | {headroom} | {failures} | {item.coverage} |"
        )
    return rows
