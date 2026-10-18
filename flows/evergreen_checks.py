"""Read and validate the same inventory and results used by Evergreen's browser."""

from datetime import UTC, datetime

import httpx
from prefect import task
from prefect.cache_policies import NONE
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

INVENTORY_URL = "https://nate.tngl.io/services.json"
STATUS_URL = "https://evergreen-proxy.n8-3e9.workers.dev/status"


class Service(BaseModel):
    name: str = Field(min_length=1)
    url: str = Field(pattern=r"^https://")


class Project(BaseModel):
    name: str = Field(min_length=1)
    services: list[Service]


class ServiceResult(Service):
    model_config = ConfigDict(strict=True)

    project: str = Field(min_length=1)
    status: int = Field(ge=0, le=599)
    ok: bool
    ms: float = Field(ge=0, allow_inf_nan=False)


class EvergreenStatus(BaseModel):
    checkedAt: datetime
    results: list[ServiceResult] = Field(min_length=1)


def _endpoints(urls: set[str], labels: dict[str, tuple[str, str]], limit: int = 5) -> str:
    named = [f"{'/'.join(labels[u])} ({u})" for u in sorted(urls)]
    more = f", and {len(named) - limit} more" if len(named) > limit else ""
    return ", ".join(named[:limit]) + more


def inventory_mismatch(
    expected: dict[str, tuple[str, str]], actual: dict[str, tuple[str, str]], n_results: int
) -> str:
    missing = expected.keys() - actual.keys()
    extra = actual.keys() - expected.keys()
    relabeled = {u for u in expected.keys() & actual.keys() if expected[u] != actual[u]}
    parts = []
    if missing:
        parts.append(
            f"no result for {len(missing)} inventory endpoint(s): {_endpoints(missing, expected)}"
        )
    if extra:
        parts.append(f"{len(extra)} result(s) not in the inventory: {_endpoints(extra, actual)}")
    if relabeled:
        parts.append(
            f"{len(relabeled)} endpoint(s) under a different project/name: {_endpoints(relabeled, actual)}"
        )
    if n_results != len(actual):
        parts.append(f"{n_results - len(actual)} duplicate result(s)")
    return "Evergreen's results do not match its published inventory: " + "; ".join(parts)


def validate_status(
    projects: list[Project], status: EvergreenStatus, now: datetime
) -> list[ServiceResult]:
    expected = {
        service.url: (project.name, service.name)
        for project in projects
        for service in project.services
    }
    if not expected or len(expected) != sum(len(p.services) for p in projects):
        raise ValueError("Evergreen inventory is empty or contains duplicate URLs")
    if len({p.name for p in projects}) != len(projects):
        raise ValueError("Evergreen inventory contains duplicate projects")
    checked = status.checkedAt
    if checked.tzinfo is None or not -30 <= (now - checked).total_seconds() <= 180:
        raise ValueError("Evergreen status timestamp is stale or invalid")
    actual = {r.url: (r.project, r.name) for r in status.results}
    if actual != expected or len(actual) != len(status.results):
        raise ValueError(inventory_mismatch(expected, actual, len(status.results)))
    if any(r.ok != (200 <= r.status < 300) for r in status.results):
        raise ValueError("Evergreen result contradicts its HTTP status")
    return status.results


@task(cache_policy=NONE, retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
def check_evergreen(
    inventory_url: str = INVENTORY_URL, status_url: str = STATUS_URL
) -> list[ServiceResult]:
    with httpx.Client(timeout=45, follow_redirects=True) as client:
        inventory = client.get(inventory_url)
        inventory.raise_for_status()
        projects = TypeAdapter(list[Project]).validate_json(inventory.content)
        response = client.get(status_url)
        response.raise_for_status()
        status = EvergreenStatus.model_validate_json(response.content)
    return validate_status(projects, status, datetime.now(UTC))
