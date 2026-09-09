"""Check Evergreen's shared inventory; terminal failures reach phi via Logfire."""

from datetime import UTC, datetime

import httpx
from prefect import flow, get_run_logger, task
from prefect.cache_policies import NONE
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

INVENTORY_URL = "https://nate.tngl.io/services.json"
STATUS_URL = "https://evergreen-proxy.n8-3e9.workers.dev/status"


class Service(BaseModel):
    name: str = Field(min_length=1)
    url: str = Field(pattern=r"^https://")


class Project(BaseModel):
    name: str = Field(min_length=1)
    services: list[Service] = Field(min_length=1)


class ServiceResult(Service):
    model_config = ConfigDict(strict=True)

    project: str = Field(min_length=1)
    status: int = Field(ge=0, le=599)
    ok: bool
    ms: float = Field(ge=0, allow_inf_nan=False)


class EvergreenStatus(BaseModel):
    checkedAt: datetime
    results: list[ServiceResult] = Field(min_length=1)


def validate_status(
    projects: list[Project], status: EvergreenStatus, now: datetime
) -> list[ServiceResult]:
    """Reject stale, partial, duplicated, or unhealthy reports before claiming health."""
    expected = {
        service.url: (project.name, service.name)
        for project in projects
        for service in project.services
    }
    if not expected or len(expected) != sum(len(p.services) for p in projects):
        raise ValueError("Evergreen inventory is empty or contains duplicate URLs")
    checked = status.checkedAt
    if checked.tzinfo is None or not -30 <= (now - checked).total_seconds() <= 180:
        raise ValueError("Evergreen status timestamp is stale or invalid")
    actual = {r.url: (r.project, r.name) for r in status.results}
    if actual != expected or len(actual) != len(status.results):
        raise ValueError("Evergreen results do not match the complete published inventory")
    failures = [r for r in status.results if not r.ok or not 200 <= r.status < 300]
    if failures:
        details = "; ".join(f"{r.project}/{r.name}: HTTP {r.status} ({r.url})" for r in failures)
        raise RuntimeError(f"Evergreen unhealthy: {details}")
    return status.results


@task(
    cache_policy=NONE,
    retries=3,
    retry_delay_seconds=[2, 5, 10],
    retry_jitter_factor=1,
)
def check_evergreen() -> list[ServiceResult]:
    with httpx.Client(timeout=45, follow_redirects=True) as client:
        inventory = client.get(INVENTORY_URL)
        inventory.raise_for_status()
        projects = TypeAdapter(list[Project]).validate_json(inventory.content)
        response = client.get(STATUS_URL)
        response.raise_for_status()
        status = EvergreenStatus.model_validate_json(response.content)
    return validate_status(projects, status, datetime.now(UTC))


@flow(name="evergreen-health", timeout_seconds=300)
def evergreen_health() -> None:
    results = check_evergreen()
    get_run_logger().info("Evergreen: %s services healthy", len(results))


if __name__ == "__main__":
    evergreen_health()
