"""Alerts as incidents, derived from the Prefect event log.

Every notification the server sends starts as a `prefect.automation.triggered`
event that carries the event which set it off. Firings of one automation about
one subject form an incident; `alert.*` events emitted by agents carry its
claim, handoff and resolution.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

# Automations that notify without reporting a fault. Everything else that sends
# a notification is an alert, so a new automation shows up here by default.
NOTIFICATIONS = frozenset(
    {
        "alert update -> discord",
        "brief ready -> discord",
        "triage ready -> discord",
        "email shortlist -> discord",
        "email triage done -> discord",
        "autofix diagnosed -> discord",
        "autofix proposed -> discord",
        "autofix revised -> discord",
        "merge awaiting approval -> discord",
        "merge tests failed -> discord",
        "merge merged -> discord",
    }
)

ACTIONS = {
    "claim": "alert.claimed",
    "note": "alert.noted",
    "handoff": "alert.handed-off",
    "resolve": "alert.resolved",
}
VERBS = {
    "alert.claimed": "is on it",
    "alert.noted": "noted",
    "alert.handed-off": "handed it off",
    "alert.resolved": "resolved it",
}
RESOURCE_PREFIX = "alert."


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _related(event: dict[str, Any], role: str) -> dict[str, Any]:
    for item in event.get("related") or []:
        if item.get("prefect.resource.role") == role:
            return item
    return {}


def subject_of(trigger: dict[str, Any] | None) -> str:
    """What a firing is about: the deployment for flow runs, else the resource."""
    if not trigger:
        return ""
    resource = trigger.get("resource") or {}
    if str(trigger.get("event", "")).startswith("prefect.flow-run."):
        name = _related(trigger, "deployment").get("prefect.resource.name")
        if name:
            return name
    return resource.get("prefect.resource.id", "")


def key_of(automation: str, trigger: dict[str, Any] | None) -> str:
    base = slug(automation.split(" -> ")[0])
    subject = re.sub(r"[^A-Za-z0-9_.@-]+", "-", subject_of(trigger)).strip("-")
    return f"{base}/{subject}" if subject else base


def describe(trigger: dict[str, Any] | None) -> str:
    if not trigger:
        return "an expected event did not arrive"
    resource = trigger.get("resource") or {}
    payload = trigger.get("payload") or {}
    if str(trigger.get("event", "")).startswith("prefect.flow-run."):
        state = resource.get("prefect.state-name", "")
        message = resource.get("prefect.state-message", "")
        return f"{state}: {message}".strip(": ")
    return str(payload.get("summary") or trigger.get("event", ""))


def _firing(event: dict[str, Any]) -> dict[str, Any]:
    trigger = (event.get("payload") or {}).get("triggering_event") or None
    firing = {
        "at": event["occurred"],
        "event": (trigger or {}).get("event", ""),
        "summary": describe(trigger),
    }
    resource_id = ((trigger or {}).get("resource") or {}).get("prefect.resource.id", "")
    if resource_id.startswith("prefect.flow-run."):
        firing["flow_run_id"] = resource_id.removeprefix("prefect.flow-run.")
        deployment = _related(trigger or {}, "deployment").get("prefect.resource.id")
        if deployment:
            firing["deployment"] = deployment
    return firing


def _update(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") or {}
    update = {
        "at": event["occurred"],
        "action": event["event"],
        "agent": str(payload.get("agent", "")),
        "note": str(payload.get("note", "")),
    }
    if payload.get("link"):
        update["link"] = str(payload["link"])
    return update


def incidents(
    automations: dict[str, str],
    triggered: Iterable[dict[str, Any]],
    lifecycle: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Fold firings and agent updates into one record per incident.

    `automations` maps the resource ID of each alerting automation to its name.
    Updates for an incident with no firing in the window are dropped: the
    firing is what makes it an alert.
    """
    result: dict[str, dict[str, Any]] = {}
    timeline: dict[str, list[tuple[str, int, dict[str, Any]]]] = {}

    for event in triggered:
        name = automations.get((event.get("resource") or {}).get("prefect.resource.id", ""))
        if name is None:
            continue
        trigger = (event.get("payload") or {}).get("triggering_event") or None
        if str((trigger or {}).get("event", "")).startswith(RESOURCE_PREFIX):
            continue
        key = key_of(name, trigger)
        subject = subject_of(trigger)
        title = name.split(" -> ")[0]
        result.setdefault(
            key,
            {
                "key": key,
                "title": f"{title}: {subject}" if subject else title,
                "automation": name,
                "subject": subject,
            },
        )
        timeline.setdefault(key, []).append((event["occurred"], 0, _firing(event)))

    for event in lifecycle:
        resource_id = (event.get("resource") or {}).get("prefect.resource.id", "")
        key = resource_id.removeprefix(RESOURCE_PREFIX)
        if event.get("event") in VERBS and key in result:
            timeline[key].append((event["occurred"], 1, _update(event)))

    for key, incident in result.items():
        status, owner, needs = "open", "", ""
        firings: list[dict[str, Any]] = []
        updates: list[dict[str, Any]] = []
        for _, kind, item in sorted(timeline[key], key=lambda entry: entry[:2]):
            if kind == 0:
                firings.append(item)
                if item["event"].endswith(".recovered"):
                    status, owner, needs = "resolved", "", ""
                elif status == "resolved":
                    status, owner = "open", ""
                continue
            updates.append(item)
            if item["action"] == "alert.claimed":
                status, owner, needs = "claimed", item["agent"], ""
            elif item["action"] == "alert.handed-off":
                status, owner, needs = "open", "", item["note"]
            elif item["action"] == "alert.resolved":
                status, owner, needs = "resolved", item["agent"], ""
        incident.update(
            status=status,
            owner=owner,
            needs=needs,
            fired=len(firings),
            first_fired=firings[0]["at"],
            last_fired=firings[-1]["at"],
            latest=firings[-1]["summary"],
            firings=firings,
            updates=updates,
        )
    return result


def recover(incident: dict[str, Any], at: str) -> None:
    """A run of the deployment completed after its last failure."""
    incident.update(status="resolved", owner="", needs="", recovered_at=at)


def update_line(action: str, agent: str, note: str, link: str) -> str:
    """The one line a human reads about an agent's update."""
    line = f"{agent} {VERBS[action]}"
    if note:
        line += f": {note}"
    if link:
        line += f"\n{link}"
    return line
