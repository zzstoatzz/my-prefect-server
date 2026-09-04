"""turn the operator's comments on gardener's pulls into autofix-revise runs.

rung three of the autofix ladder (docs/autofix.md). the operator
reviews a gardener-authored pull on tangled and leaves a comment; this flow
sees the comment record on the stream.waow.tech firehose and starts an
autofix-revise run for it.

two paths, the same shape phi's review loop settled on: the PDS is the
authority and the stream is the fast path. every run does one listRecords
against the operator's PDS (reconcile — catches anything, regardless of
cursor state) and also drains /subscribe from the saved time_us cursor for
low latency. dedupe is by comment uri (a Variable), so overlap between the
two paths and across runs is harmless.

wantedDids scopes the subscription to the operator, and reconcile reads only
the operator's repo: only their comments can trigger a revision, so gardener
replying to itself is structurally impossible.
"""

import asyncio
import json
import time
from typing import Any

import httpx
from mps.tangled import (
    DID as OPERATOR_DID,
    FEED_COMMENT_NSID,
    LEGACY_COMMENT_NSID,
    comment_subject,
    comment_text,
    resolve_pds,
)
from prefect import flow
from prefect.deployments.flow_runs import arun_deployment
from prefect.events import emit_event
from prefect.variables import Variable

from flows.autofix import PULL_PREFIX

STREAM_URL = "wss://stream.waow.tech/subscribe"

CURSOR_VAR = "autofix_pull_comment_cursor"
HANDLED_VAR = "autofix_handled_comments"
HANDLED_KEEP = 300
IDLE_SECONDS = 8
MAX_WALL_SECONDS = 120
RECONCILE_LIMIT = 25
# saved cursors resume exactly; a fresh cursor overlaps the previous window
# by this much rather than trusting idle detection during sparse replay
CURSOR_OVERLAP_US = 30_000_000


def relevant_comment(event: dict[str, Any]) -> dict[str, str] | None:
    """reduce a jetstream event to an actionable comment, or None."""
    commit = event.get("commit") or {}
    if commit.get("operation") != "create":
        return None
    if commit.get("collection") not in (FEED_COMMENT_NSID, LEGACY_COMMENT_NSID):
        return None
    record = commit.get("record") or {}
    subject = comment_subject(record)
    if not subject.startswith(PULL_PREFIX):
        return None
    return {
        "uri": f"at://{event.get('did')}/{commit['collection']}/{commit.get('rkey')}",
        "pull": subject,
        "text": comment_text(record),
        "created_at": record.get("createdAt", ""),
    }


async def drain(cursor: int | None) -> tuple[list[dict[str, str]], int | None]:
    """read the stream from `cursor` until the tail goes idle.

    returns (comments, new_cursor). new_cursor is the last event's time_us —
    all events advance it, not just matches, so quiet windows still move
    forward. None means nothing arrived and the cursor should not move.
    """
    import websockets

    params = (
        f"?wantedDids={OPERATOR_DID}"
        f"&wantedCollections={FEED_COMMENT_NSID}"
        f"&wantedCollections={LEGACY_COMMENT_NSID}"
    )
    if cursor:
        params += f"&cursor={cursor}"

    comments: list[dict[str, str]] = []
    connect_us = time.time_ns() // 1000
    last_time_us: int | None = None
    deadline = asyncio.get_event_loop().time() + MAX_WALL_SECONDS

    async with websockets.connect(STREAM_URL + params, open_timeout=15) as ws:
        while asyncio.get_event_loop().time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=IDLE_SECONDS)
            except TimeoutError:
                break
            event = json.loads(raw)
            if time_us := event.get("time_us"):
                last_time_us = time_us
            if match := relevant_comment(event):
                comments.append(match)
    # matching events are sparse, so idle says nothing about replay progress;
    # the cursor still advances to connect time (minus overlap) because the
    # reconcile path, not the stream, is what guarantees delivery
    return comments, max(last_time_us or 0, connect_us - CURSOR_OVERLAP_US)


def reconcile() -> list[dict[str, str]]:
    """the operator's newest comments straight from their PDS (the authority)."""
    resp = httpx.get(
        f"{resolve_pds(OPERATOR_DID)}/xrpc/com.atproto.repo.listRecords",
        params={
            "repo": OPERATOR_DID,
            "collection": FEED_COMMENT_NSID,
            "limit": RECONCILE_LIMIT,
        },
        timeout=20,
    )
    resp.raise_for_status()
    out = []
    for rec in resp.json().get("records", []):
        value = rec.get("value", {})
        subject = comment_subject(value)
        if subject.startswith(PULL_PREFIX):
            out.append(
                {
                    "uri": rec["uri"],
                    "pull": subject,
                    "text": comment_text(value),
                    "created_at": value.get("createdAt", ""),
                }
            )
    return out


@flow(name="watch-tangled-pulls", log_prints=True, timeout_seconds=300)
async def watch_tangled_pulls() -> int:
    stored_cursor = await Variable.aget(CURSOR_VAR)
    stored_handled = await Variable.aget(HANDLED_VAR)
    handled = [str(u) for u in stored_handled] if isinstance(stored_handled, list) else []

    try:
        streamed, new_cursor = await drain(
            int(stored_cursor) if isinstance(stored_cursor, int | str) and stored_cursor else None
        )
    except Exception as exc:
        print(f"stream drain failed ({exc!r}); reconcile still runs")
        streamed, new_cursor = [], None
    comments = {c["uri"]: c for c in [*streamed, *reconcile()]}
    new = [c for c in comments.values() if c["uri"] not in handled]
    print(f"{len(streamed)} streamed + reconcile -> {len(comments)} comment(s), {len(new)} new")

    for comment in new:
        emit_event(
            event="autofix.review-comment",
            resource={
                "prefect.resource.id": f"autofix.comment.{comment['uri'].rsplit('/', 1)[-1]}",
                "prefect.resource.name": comment["pull"].rsplit("/", 1)[-1],
            },
            payload=comment,
        )
        run = await arun_deployment(
            "autofix-revise/autofix-revise",
            parameters={"pull": comment["pull"], "comment_uri": comment["uri"]},
            timeout=0,
        )
        print(f"revise run {run.id} for comment {comment['uri']}")

    if new:
        await Variable.aset(
            HANDLED_VAR,
            [*handled, *(c["uri"] for c in new)][-HANDLED_KEEP:],
            overwrite=True,
        )
    if new_cursor:
        await Variable.aset(CURSOR_VAR, str(new_cursor), overwrite=True)
    return len(new)


if __name__ == "__main__":
    asyncio.run(watch_tangled_pulls())
