"""review comments on gardener pulls that ask for a new autofix round.

rung three of the autofix ladder (docs/autofix.md). two producers find these
comments and both emit the same `autofix.revise-requested` event, which the
`autofix revise requested -> autofix-revise` automation turns into a run:

  - `mps.pull_comment_bridge`, a service on heavypad holding stream.waow.tech
    open (the fast path)
  - the `watch-tangled-pulls` flow, an hourly listRecords against each
    reviewer's PDS (the authority — catches anything the bridge missed)

dedupe is by comment uri in a shared Variable. only the operator and Phi can
request a revision; Phi only with a request-changes verdict. gardener cannot
trigger itself. autofix-revise re-validates the comment against the pull's
current round and CID before running pi.
"""

from datetime import datetime
from typing import Any

import httpx
from prefect.variables import Variable

from mps.tangled import (
    DID as OPERATOR_DID,
    FEED_COMMENT_NSID,
    GARDENER_DID,
    LEGACY_COMMENT_NSID,
    PHI_DID,
    comment_subject,
    comment_text,
    parse_verdict,
    resolve_pds,
)

STREAM_URL = "wss://stream.waow.tech/subscribe"
PULL_PREFIX = f"at://{GARDENER_DID}/sh.tangled.repo.pull/"
REVIEWERS = (OPERATOR_DID, PHI_DID)
REVISE_EVENT = "autofix.revise-requested"

HANDLED_VAR = "autofix_handled_comments"
HANDLED_KEEP = 300
RECONCILE_LIMIT = 25


def subscribe_url(cursor: int | None = None) -> str:
    params = "&".join(
        [
            *(f"wantedDids={did}" for did in REVIEWERS),
            f"wantedCollections={FEED_COMMENT_NSID}",
            f"wantedCollections={LEGACY_COMMENT_NSID}",
            *([f"cursor={cursor}"] if cursor else []),
        ]
    )
    return f"{STREAM_URL}?{params}"


def requests_revision(reviewer: str, record: dict[str, Any]) -> bool:
    if reviewer not in REVIEWERS:
        return False
    if reviewer == PHI_DID and parse_verdict(comment_text(record)) != "request-changes":
        return False
    return comment_subject(record).startswith(PULL_PREFIX)


def _comment(uri: str, record: dict[str, Any]) -> dict[str, str]:
    return {
        "uri": uri,
        "pull": comment_subject(record),
        "text": comment_text(record),
        "created_at": record.get("createdAt", ""),
    }


def relevant_comment(event: dict[str, Any]) -> dict[str, str] | None:
    """reduce a jetstream event to an actionable comment, or None."""
    commit = event.get("commit") or {}
    if commit.get("operation") != "create":
        return None
    if commit.get("collection") not in (FEED_COMMENT_NSID, LEGACY_COMMENT_NSID):
        return None
    record = commit.get("record") or {}
    reviewer = event.get("did", "")
    if not requests_revision(reviewer, record):
        return None
    return _comment(f"at://{reviewer}/{commit['collection']}/{commit.get('rkey')}", record)


def reconcile_reviewer(reviewer: str) -> list[dict[str, str]]:
    """a reviewer's newest comments straight from their PDS (the authority)."""
    resp = httpx.get(
        f"{resolve_pds(reviewer)}/xrpc/com.atproto.repo.listRecords",
        params={"repo": reviewer, "collection": FEED_COMMENT_NSID, "limit": RECONCILE_LIMIT},
        timeout=20,
    )
    resp.raise_for_status()
    return [
        _comment(rec["uri"], rec.get("value", {}))
        for rec in resp.json().get("records", [])
        if requests_revision(reviewer, rec.get("value", {}))
    ]


def reconcile() -> list[dict[str, str]]:
    return [c for reviewer in REVIEWERS for c in reconcile_reviewer(reviewer)]


def created_before(comment: dict[str, str], cutoff: datetime) -> bool:
    """True when the comment's createdAt is before `cutoff`, or unreadable."""
    try:
        return datetime.fromisoformat(comment["created_at"]) < cutoff
    except (KeyError, ValueError, TypeError):
        return True


def revise_event(comment: dict[str, str]) -> dict[str, Any]:
    """keyword arguments for `emit_event` or `Event`."""
    return {
        "event": REVISE_EVENT,
        "resource": {
            "prefect.resource.id": f"autofix.comment.{comment['uri'].rsplit('/', 1)[-1]}",
            "prefect.resource.name": comment["pull"].rsplit("/", 1)[-1],
        },
        "payload": comment,
    }


async def load_handled() -> list[str]:
    stored = await Variable.aget(HANDLED_VAR)
    return [str(u) for u in stored] if isinstance(stored, list) else []


async def mark_handled(uris: list[str]) -> None:
    """append to the handled list, re-reading it so a concurrent writer's
    entries survive (the bridge and the reconcile flow both write it)."""
    handled = await load_handled()
    merged = [*handled, *(u for u in uris if u not in handled)]
    await Variable.aset(HANDLED_VAR, merged[-HANDLED_KEEP:], overwrite=True)
