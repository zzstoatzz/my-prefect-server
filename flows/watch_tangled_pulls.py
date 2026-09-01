"""turn the operator's comments on gardener's pulls into autofix-revise runs.

rung three of the autofix ladder (docs/autofix-design.md). the operator
reviews a gardener-authored pull on tangled and leaves a comment; this flow
sees the comment record on the stream.waow.tech firehose and starts an
autofix-revise run for it.

the consumer is deliberately not a long-lived process: each run connects to
/subscribe with the saved time_us cursor, drains until the tail goes idle,
and saves the new cursor. stream.waow.tech replays from the cursor on
connect, so a cron of short-lived runs sees every event a daemon would,
without a daemon to babysit. dedupe is by comment rkey (a Variable), so a
re-read window is harmless — and autofix-revise re-lists the pull's comments
from the PDS anyway, which is the reconcile path if the stream drops.

wantedDids scopes the subscription to the operator: only their comments can
trigger a revision, so gardener replying to itself is structurally impossible.
"""

import asyncio
import json
from typing import Any

from mps.tangled import (
    DID as OPERATOR_DID,
)
from mps.tangled import (
    FEED_COMMENT_NSID,
    LEGACY_COMMENT_NSID,
    comment_subject,
    comment_text,
)
from prefect import flow
from prefect.deployments import run_deployment
from prefect.events import emit_event
from prefect.variables import Variable

from flows.autofix import PULL_PREFIX

STREAM_URL = "wss://stream.waow.tech/subscribe"

CURSOR_VAR = "autofix_pull_comment_cursor"
HANDLED_VAR = "autofix_handled_comments"
HANDLED_KEEP = 300
IDLE_SECONDS = 8
MAX_WALL_SECONDS = 120


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
    return comments, last_time_us


@flow(name="watch-tangled-pulls", log_prints=True, timeout_seconds=300)
async def watch_tangled_pulls() -> int:
    cursor = await Variable.aget(CURSOR_VAR, default=None)
    handled: list[str] = await Variable.aget(HANDLED_VAR, default=[])

    comments, last_time_us = await drain(int(cursor) if cursor else None)
    new = [c for c in comments if c["uri"] not in handled]
    print(f"stream window: {len(comments)} comment(s), {len(new)} new")

    for comment in new:
        emit_event(
            event="autofix.review-comment",
            resource={
                "prefect.resource.id": f"autofix.comment.{comment['uri'].rsplit('/', 1)[-1]}",
                "prefect.resource.name": comment["pull"].rsplit("/", 1)[-1],
            },
            payload=comment,
        )
        run = await run_deployment(
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
    if last_time_us:
        await Variable.aset(CURSOR_VAR, str(last_time_us), overwrite=True)
    return len(new)


if __name__ == "__main__":
    asyncio.run(watch_tangled_pulls())
