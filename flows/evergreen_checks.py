"""Read and validate the same inventory and results used by Evergreen's browser."""

from datetime import UTC, datetime
from typing import Literal, Self

import httpx
from prefect import task
from prefect.cache_policies import NONE
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

INVENTORY_URL = "https://nate.tngl.io/services.json"
STATUS_URL = "https://evergreen-proxy.n8-3e9.workers.dev/status"


class Service(BaseModel):
    name: str = Field(min_length=1)
    url: str = Field(pattern=r"^https://")


class Project(BaseModel):
    name: str = Field(min_length=1)
    services: list[Service]


class ProbeError(BaseModel):
    model_config = ConfigDict(strict=True)

    category: Literal["timeout", "network", "unknown"]
    message: str = Field(min_length=1, max_length=160)


class ProbeAttempt(BaseModel):
    model_config = ConfigDict(strict=True)

    status: int = Field(ge=0, le=599)
    ok: bool
    ms: float = Field(ge=0, allow_inf_nan=False)
    error: ProbeError | None = None

    @model_validator(mode="after")
    def consistent_evidence(self) -> Self:
        if self.ok != (200 <= self.status < 300):
            raise ValueError("Evergreen attempt contradicts its HTTP status")
        if (self.status == 0) != (self.error is not None):
            raise ValueError("Evergreen attempt contradicts its transport error")
        return self


class ServiceResult(Service):
    model_config = ConfigDict(strict=True)

    project: str = Field(min_length=1)
    status: int = Field(ge=0, le=599)
    ok: bool
    ms: float = Field(ge=0, allow_inf_nan=False)
    attempt_count: int | None = Field(default=None, ge=1, le=2)
    attempts: list[ProbeAttempt] | None = Field(default=None, min_length=1, max_length=2)
    error: ProbeError | None = None

    @model_validator(mode="after")
    def consistent_diagnostics(self) -> Self:
        if self.error is not None and self.status != 0:
            raise ValueError("Evergreen HTTP result cannot have a transport error")
        if (self.attempt_count is None) != (self.attempts is None):
            raise ValueError("Evergreen attempt count and evidence must be supplied together")
        if self.attempts is not None:
            if self.attempt_count != len(self.attempts):
                raise ValueError("Evergreen attempt count does not match evidence")
            final = self.attempts[-1]
            if (self.status, self.ok, self.error) != (final.status, final.ok, final.error):
                raise ValueError("Evergreen result contradicts its final attempt")
            if self.ms < sum(attempt.ms for attempt in self.attempts):
                raise ValueError("Evergreen total timing is shorter than its attempts")
            if any(attempt.ok for attempt in self.attempts[:-1]):
                raise ValueError("Evergreen retried an already healthy attempt")
        return self


class EvergreenStatus(BaseModel):
    checkedAt: datetime
    results: list[ServiceResult] = Field(min_length=1)


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
        raise ValueError("Evergreen results do not match the complete published inventory")
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
