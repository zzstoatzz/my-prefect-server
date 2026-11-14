"""Turn a public status page's incident feed into alert events.

The page offers webhook subscriptions, but nothing here accepts inbound
requests from the internet: the alerts MCP is tailnet-only and derives alerts
from the event log. Polling the page's Atom feed needs no receiver. Each
incident is its own event resource, so it becomes one alert incident that
collects every update and is resolved by `status-page.recovered`.

State advances only after the Prefect API accepts the events, so a failed
delivery is retried by the next run instead of being forgotten.
"""

import html
import re
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5
from xml.etree import ElementTree

import httpx
from prefect import flow, task
from prefect.client.orchestration import get_client
from prefect.variables import Variable

ATOM = "{http://www.w3.org/2005/Atom}"
INCIDENT = re.compile(r":Incident/([\w-]+)$")
UPDATE = re.compile(r"</small>\s*<br\s*/?>\s*<strong>(.*?)</strong>\s*-\s*(.*?)</p>", re.S)
TAG = re.compile(r"<[^>]+>")
CLOSED = frozenset({"resolved", "completed"})
BODY_LIMIT = 500


def _text(fragment: str) -> str:
    return " ".join(html.unescape(TAG.sub(" ", fragment)).split())


def incidents(feed: str) -> list[dict[str, str]]:
    """Incidents in the feed, each with its newest update. Maintenance is left out."""
    found = []
    for entry in ElementTree.fromstring(feed).iter(f"{ATOM}entry"):
        match = INCIDENT.search(entry.findtext(f"{ATOM}id", ""))
        if match is None:
            continue
        link = entry.find(f"{ATOM}link")
        latest = UPDATE.search(entry.findtext(f"{ATOM}content", ""))
        if latest is None:
            raise ValueError(f"incident {match.group(1)} has no readable update")
        found.append(
            {
                "id": match.group(1),
                "title": _text(entry.findtext(f"{ATOM}title", "")),
                "url": link.get("href", "") if link is not None else "",
                "updated": entry.findtext(f"{ATOM}updated", ""),
                "status": _text(latest.group(1)),
                "body": _text(latest.group(2))[:BODY_LIMIT],
            }
        )
    return found


def transitions(
    page: str, state: dict[str, Any], found: list[dict[str, str]], now: datetime
) -> tuple[dict[str, Any], list[dict]]:
    """Next state and one event per incident whose newest update changed.

    An incident first seen closed is recorded silently; first seen open, it
    alerts. State keeps only incidents still in the feed. Event IDs derive
    from the incident and its update time, so re-sending after a failed
    delivery does not mint a different event.
    """
    following: dict[str, Any] = {}
    events = []
    for incident in found:
        closed = incident["status"].lower() in CLOSED
        previous = state.get(incident["id"])
        following[incident["id"]] = {"updated": incident["updated"], "closed": closed}
        if previous == following[incident["id"]] or (previous is None and closed):
            continue
        if closed and previous is not None and previous.get("closed"):
            continue
        name = "status-page.recovered" if closed else "status-page.incident"
        resource = f"status-page.{page}.{incident['id']}"
        events.append(
            {
                "id": str(uuid5(NAMESPACE_URL, f"{resource}:{incident['updated']}")),
                "occurred": now.isoformat(),
                "event": name,
                "resource": {
                    "prefect.resource.id": resource,
                    "prefect.resource.name": f"{page}: {incident['title']}",
                },
                "related": [],
                "payload": {
                    "summary": (
                        f"{page} · {incident['title']} · {incident['status']}: "
                        f"{incident['body']} {incident['url']}"
                    ),
                    "status": incident["status"],
                    "updated": incident["updated"],
                },
            }
        )
    return following, events


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
async def read_feed(feed: str) -> str:
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        response = await client.get(feed)
        response.raise_for_status()
        return response.text


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
async def deliver(events: list[dict]) -> None:
    async with get_client() as client:
        response = await client.request("POST", "/events", json=events)
        response.raise_for_status()


@flow(name="status-page", log_prints=True, timeout_seconds=120)
async def status_page(page: str, feed: str) -> dict[str, Any]:
    variable = f"status-page-{page}"
    state = await Variable.aget(variable, default={})
    if not isinstance(state, dict):
        raise ValueError("invalid status page state")
    state, events = transitions(page, state, incidents(await read_feed(feed)), datetime.now(UTC))
    if events:
        await deliver(events)
    await Variable.aset(variable, state, overwrite=True)
    report = {"open": [k for k, v in state.items() if not v["closed"]], "emitted": len(events)}
    print(report, [e["payload"]["summary"] for e in events])
    return report
