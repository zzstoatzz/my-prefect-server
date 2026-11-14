"""Notice when the operator's PDS runs an older zds than pds.zat.dev.

pds.zat.dev (Evergreen project "zds") redeploys on every zds push, so it is the
canary. The operator's PDS is pinned in pds-infra and moves only with
`just zds-upgrade`. Once the canary has run a newer zds for SOAK, this emits one
`zds.behind` event per version so a person decides whether to roll; nothing here
deploys. Once the operator's PDS reaches that version, `zds.recovered` closes the
alert incident. The operator's PDS comes from the DID document, never from a hostname.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from mps.atproto import resolve_pds
from mps.blocks import secret_sync
from mps.tangled import DID as OPERATOR_DID
from prefect import task
from prefect.cache_policies import NONE
from prefect.events import emit_event
from prefect.variables import Variable

from flows.evergreen_checks import INVENTORY_URL, Project
from flows.fleet_events import run_link
from flows.fly_observations import MACHINES_API

CANARY_PROJECT = "zds"
SOAK = timedelta(hours=24)
STATE = "zds-follow-notified"
HEALTH = "/xrpc/_health"
UPGRADE = "cd ~/tangled.org/zzstoatzz.io/pds-infra && just zds-upgrade {version}"
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


@dataclass
class Follow:
    operator: str
    operator_version: str | None
    canary_version: str | None
    canary_since: datetime | None
    notice: str | None = None

    def row(self) -> str:
        since = self.canary_since.isoformat(timespec="minutes") if self.canary_since else "unknown"
        state = "**ROLL AVAILABLE**" if self.notice else "ok"
        return (
            f"| zds follow | {state} | {self.operator} runs {self.operator_version}; "
            f"pds.zat.dev runs {self.canary_version} since {since} |"
        )


def semver(value: str | None) -> tuple[int, int, int] | None:
    match = SEMVER.match(value or "")
    if not match:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def canary(projects: list[Project]) -> tuple[str, str]:
    """The canary's health base URL and Fly app, from Evergreen's inventory."""
    project = next((p for p in projects if p.name == CANARY_PROJECT), None)
    if project is None:
        raise ValueError(f"Evergreen inventory has no {CANARY_PROJECT!r} project")
    base = next((s.url[: -len(HEALTH)] for s in project.services if s.url.endswith(HEALTH)), None)
    app = next((r.name for r in project.resources if r.provider == "fly" and r.kind == "app"), None)
    if base is None or app is None:
        raise ValueError(f"{CANARY_PROJECT!r} needs a {HEALTH} service and a Fly app")
    return base, app


def notice(follow: Follow, now: datetime, notified: str | None) -> str | None:
    """The message to send, or None when the operator is current, the canary has
    not soaked, either version is not a zds release, or this version was sent."""
    behind = semver(follow.operator_version)
    ahead = semver(follow.canary_version)
    if behind is None or ahead is None or ahead <= behind:
        return None
    if follow.canary_since is None or now - follow.canary_since < SOAK:
        return None
    if follow.canary_version == notified:
        return None
    hours = int((now - follow.canary_since).total_seconds() // 3600)
    link = run_link()
    return "\n".join(
        [
            f"pds.zat.dev has run zds {follow.canary_version} for {hours}h; "
            f"{follow.operator} still runs {follow.operator_version}.",
            f"roll it when you're ready: {UPGRADE.format(version=follow.canary_version)}",
            *([f"run: {link}"] if link else []),
        ]
    )


def caught_up(follow: Follow, notified: str | None) -> bool:
    """The operator's PDS now runs the release it was last asked to roll to."""
    behind, asked = semver(follow.operator_version), semver(notified)
    return behind is not None and asked is not None and behind >= asked


def _version(client: httpx.Client, base: str) -> str | None:
    response = client.get(f"{base.rstrip('/')}{HEALTH}")
    response.raise_for_status()
    return response.json().get("version")


def _running_since(app: str) -> datetime | None:
    """When the canary's running machine last changed, which a deploy does."""
    token = secret_sync("fly-api-token")
    headers = {"Authorization": token if token.startswith("FlyV1 ") else f"FlyV1 {token}"}
    with httpx.Client(base_url=MACHINES_API, headers=headers, timeout=5) as client:
        response = client.get(f"apps/{app}/machines")
        response.raise_for_status()
    started = [
        datetime.fromisoformat(m["updated_at"].replace("Z", "+00:00"))
        for m in response.json()
        if m.get("state") == "started" and m.get("updated_at")
    ]
    return max(started) if started else None


@task(cache_policy=NONE, retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
def zds_follow(inventory_url: str = INVENTORY_URL) -> Follow:
    with httpx.Client(timeout=10, follow_redirects=True) as client:
        response = client.get(inventory_url)
        response.raise_for_status()
        projects = [Project.model_validate(p) for p in response.json()]
        base, app = canary(projects)
        operator = resolve_pds(OPERATOR_DID)
        follow = Follow(
            operator=operator.removeprefix("https://"),
            operator_version=_version(client, operator),
            canary_version=_version(client, base),
            canary_since=_running_since(app),
        )
    stored = Variable.get(STATE, default=None)
    notified = stored if isinstance(stored, str) and stored else None
    if caught_up(follow, notified):
        emit_event(
            event="zds.recovered",
            resource={"prefect.resource.id": "zds-follow"},
            payload={"summary": f"{follow.operator} now runs zds {follow.operator_version}."},
        )
        Variable.set(STATE, "", overwrite=True)
        notified = None
    follow.notice = notice(follow, datetime.now(UTC), notified)
    if follow.notice:
        emit_event(
            event="zds.behind",
            resource={"prefect.resource.id": "zds-follow"},
            payload={"summary": follow.notice, "version": follow.canary_version},
        )
        Variable.set(STATE, follow.canary_version, overwrite=True)
    return follow
