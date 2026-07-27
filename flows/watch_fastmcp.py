"""Turn fastmcp activity into events on the hub's bus.

This flow does not notify anybody. It polls the repo-scoped notifications
endpoint and emits one `github.<reason>` event per thread that moved, so
automations can react to *patterns* — three comments on one issue in an hour,
a review request, an unusually quiet day — rather than to every notification.
The thing that actually reaches a human is a brief composed downstream.

Why `/repos/{owner}/{repo}/notifications` rather than the events firehose:
`reason` is the signal-vs-noise dial (`review_requested` and `mention` mean
someone is blocked on you; `subscribed` is ambient), and conditional requests
that 304 do not count against the rate limit, so polling every couple of
minutes is free. GitHub also returns X-Poll-Interval telling us its floor.

The watermark is advanced only after emitting, because emit_event is
fire-and-forget through a bounded queue that drops when full — advancing first
would silently lose a window.

Note for automations: payload keys are dot-free on purpose. The template
resolver splits paths on ".", so a label keyed "github.repo" is unreachable as
{{ event.resource.github.repo }}.
"""

import datetime
from typing import Any

import httpx
from prefect import flow, get_run_logger, task
from prefect.blocks.system import Secret
from prefect.events import emit_event
from prefect.variables import Variable

from mps.github import gh_headers

GITHUB_API = "https://api.github.com"
REPO = "PrefectHQ/fastmcp"

# a notification's reason, mapped to how much of your attention it deserves.
# `direct` means a person is waiting on you; `ambient` is repo activity you
# subscribed to. Anything unlisted is dropped rather than guessed at.
DIRECT_REASONS = {"review_requested", "mention", "team_mention", "assign"}
AMBIENT_REASONS = {"subscribed", "comment", "author", "state_change"}

# bots generate real notifications that are never worth a human's attention
BOT_AUTHORS = {"dependabot[bot]", "renovate[bot]", "github-actions[bot]", "pre-commit-ci[bot]"}

WATERMARK = "fastmcp_notifications_last_modified"


@task(retries=2, retry_delay_seconds=10)
def fetch_notifications(token: str, last_modified: str | None) -> tuple[list[dict], str | None, int]:
    """Threads that moved since `last_modified`, plus the new watermark.

    Returns ([], watermark, poll_interval) on a 304 — the common case, and the
    reason this is cheap enough to run every couple of minutes.
    """
    logger = get_run_logger()
    headers = gh_headers(token)
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    with httpx.Client(headers=headers, timeout=30.0) as client:
        resp = client.get(
            f"{GITHUB_API}/repos/{REPO}/notifications",
            params={"all": "false", "per_page": 50},
        )

    poll_interval = int(resp.headers.get("X-Poll-Interval", 60))

    if resp.status_code == 304:
        logger.info("304 — nothing new since %s", last_modified)
        return [], last_modified, poll_interval

    resp.raise_for_status()
    return resp.json(), resp.headers.get("Last-Modified", last_modified), poll_interval


def _thread_to_event(thread: dict) -> dict[str, Any] | None:
    """One notification thread reduced to what an automation can act on."""
    reason = thread.get("reason")
    if reason not in DIRECT_REASONS and reason not in AMBIENT_REASONS:
        return None

    subject = thread.get("subject") or {}
    subject_type = subject.get("type")
    if subject_type not in ("Issue", "PullRequest", "Discussion", "Release"):
        return None

    # the API subject.url is the REST url; derive the number and a human link
    api_url = subject.get("url") or ""
    number = None
    if api_url:
        tail = api_url.rstrip("/").rsplit("/", 1)[-1]
        if tail.isdigit():
            number = int(tail)

    path = {"PullRequest": "pull", "Issue": "issues", "Discussion": "discussions"}.get(
        subject_type, "issues"
    )
    html_url = f"https://github.com/{REPO}/{path}/{number}" if number else f"https://github.com/{REPO}"

    return {
        "thread_id": thread.get("id"),
        "reason": reason,
        "tier": "direct" if reason in DIRECT_REASONS else "ambient",
        "title": subject.get("title") or "",
        "kind": subject_type,
        "number": number,
        "url": html_url,
        "repo": REPO,
        "updated_at": thread.get("updated_at") or "",
    }


@task
def emit_thread_events(threads: list[dict]) -> int:
    """Emit one `github.<reason>` event per actionable thread."""
    logger = get_run_logger()
    emitted = 0

    for thread in threads:
        payload = _thread_to_event(thread)
        if payload is None:
            continue

        emit_event(
            event=f"github.{payload['reason']}",
            resource={
                "prefect.resource.id": f"github.thread.{payload['thread_id']}",
                "prefect.resource.name": payload["title"][:200] or "untitled",
                # dot-free so `{{ event.resource.githubtier }}`-style lookups
                # are not needed; automations match on these with globs
                "githubrepo": payload["repo"],
                "githubtier": payload["tier"],
                "githubkind": payload["kind"],
            },
            payload=payload,
        )
        emitted += 1

    logger.info("emitted %d/%d threads as events", emitted, len(threads))
    return emitted


@flow(name="watch-fastmcp", log_prints=True, timeout_seconds=300)
def watch_fastmcp(reset_watermark: bool = False) -> dict[str, Any]:
    logger = get_run_logger()
    token = Secret.load("github-token").get()

    last_modified = None if reset_watermark else Variable.get(WATERMARK, default=None)
    threads, watermark, poll_interval = fetch_notifications(token, last_modified)

    if not threads:
        return {"emitted": 0, "poll_interval": poll_interval}

    actionable = [
        t
        for t in threads
        if (t.get("subject") or {}).get("latest_comment_url") is not None
        or t.get("reason") in DIRECT_REASONS
    ]
    logger.info("%d threads moved (%d actionable)", len(threads), len(actionable))

    emitted = emit_thread_events(threads)

    # only now — emit_event drops silently on a full queue, so advancing the
    # watermark first would lose a window with no trace
    if watermark:
        Variable.set(WATERMARK, watermark, overwrite=True)

    return {
        "emitted": emitted,
        "threads": len(threads),
        "poll_interval": poll_interval,
        "watermark": watermark,
    }


if __name__ == "__main__":
    print(watch_fastmcp(reset_watermark=True))
