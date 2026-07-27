"""Compose what happened in fastmcp into something worth reading.

Triggered by an automation watching `github.*` volume, not by a schedule — the
point is to fire when something is *happening*, then look at the whole window
rather than at the one event that crossed the threshold. So this flow takes no
event payload; it reads the window back off the bus itself.

Output goes back onto the bus as `hub.brief.ready`, and a second automation
renders `{{ payload.brief }}` into Discord through the existing block. That
keeps the LLM's output in the events table, reuses the delivery path already in
production, and means this flow needs no notification credentials of its own.

The rendered brief is hard-capped: Discord rejects message content over 2000
characters, and the zig sender prepends a bold subject, so a brief that ignores
this would fail at the last hop with a 400.
"""

import datetime
import os
from typing import Any

import httpx
from prefect import flow, get_run_logger, task
from prefect.blocks.system import Secret
from prefect.events import emit_event
from prefect.variables import Variable
from pydantic import BaseModel, Field

MODEL = "claude-haiku-4-5"

# thread_id -> updated_at of everything already briefed. A volume trigger fires
# whenever the window is busy, so without this the same four pull requests get
# re-briefed every time it trips. Keyed on updated_at, not just the id, so a
# thread that genuinely moves again can come back.
BRIEFED = "fastmcp_briefed_threads"
BRIEFED_MAX = 400

# discord's content limit is 2000; the sender wraps body in "**subject**\n\n"
# and we leave room for the footer line
BRIEF_CHAR_BUDGET = 1600

BRIEF_PROMPT = """\
you summarize activity in the fastmcp repository for its maintainer, who works
on it full time and already knows the codebase.

you are given recent notification threads (pull requests, issues, discussions).
almost all of them are ambient subscription noise. your job is to find the few
that a maintainer would want to know about right now, and to say why in a
clause — not to summarize everything.

rank by what would change how they spend the next hour:
- something broken, regressed, or blocking a release
- an outside contributor waiting on a maintainer
- a decision being asked for
- an issue that looks like a real bug report rather than a question

skip: routine docs edits, dependency bumps, your own merged work, anything
purely mechanical. it is correct to return very few items, or none.

write headlines in lowercase, plain, specific. no marketing tone, no "notably",
no "it's worth mentioning". name the actual thing.

headlines are read at a glance on a phone, so keep them short — under about
nine words. put the detail in `why`, as one clause, no trailing period.

`number` is the issue or PR number the url points at. `severity` is:
  broken   — main is red, a release is blocked, something is down
  bug      — a real defect users will hit
  waiting  — someone needs a maintainer to act
  decision — a question or design call is open
"""


class BriefItem(BaseModel):
    headline: str = Field(max_length=90, description="what happened, specific and lowercase")
    why: str = Field(max_length=120, description="one clause on why it deserves attention")
    url: str
    number: int = Field(description="the issue or PR number this is about")
    severity: str = Field(
        description="one of: broken, waiting, decision, bug",
        pattern="^(broken|waiting|decision|bug)$",
    )


class Brief(BaseModel):
    items: list[BriefItem] = Field(description="ranked, most important first; may be empty")
    considered: int = Field(description="how many threads you looked at")


def _api_url() -> str:
    return os.environ["PREFECT_API_URL"].rstrip("/")


def _auth() -> httpx.BasicAuth | None:
    raw = os.environ.get("PREFECT_API_AUTH_STRING")
    if not raw:
        return None
    user, sep, password = raw.partition(":")
    if not sep:
        raise RuntimeError("PREFECT_API_AUTH_STRING must be user:password")
    return httpx.BasicAuth(user, password)


@task(retries=2, retry_delay_seconds=5)
def recent_github_events(hours: int) -> list[dict[str, Any]]:
    """The `github.*` window, one entry per thread (latest wins)."""
    logger = get_run_logger()
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)

    with httpx.Client(auth=_auth(), timeout=30.0) as client:
        resp = client.post(
            f"{_api_url()}/events/filter",
            json={
                "filter": {
                    "event": {"prefix": ["github."]},
                    "occurred": {"since": since.isoformat()},
                },
                "limit": 200,
            },
        )
        resp.raise_for_status()

    by_thread: dict[str, dict] = {}
    for event in resp.json().get("events", []):
        payload = event.get("payload") or {}
        thread_id = payload.get("thread_id")
        if not thread_id:
            continue
        prior = by_thread.get(thread_id)
        if prior is None or (payload.get("updated_at") or "") >= (prior.get("updated_at") or ""):
            by_thread[thread_id] = payload

    threads = sorted(by_thread.values(), key=lambda p: p.get("updated_at") or "")
    logger.info("window=%dh events collapsed to %d threads", hours, len(threads))
    return threads


@task
def compose(threads: list[dict[str, Any]], api_key: str) -> Brief:
    from pydantic_ai import Agent
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.providers.anthropic import AnthropicProvider

    from mps.spend import record_pydantic_ai_result

    lines = [
        f"- [{t.get('kind')} #{t.get('number')}] {t.get('title')!r} "
        f"(reason={t.get('reason')}, updated={t.get('updated_at')}) {t.get('url')}"
        for t in threads
    ]

    agent = Agent(
        AnthropicModel(MODEL, provider=AnthropicProvider(api_key=api_key)),
        output_type=Brief,
        system_prompt=BRIEF_PROMPT,
        name="fastmcp-brief",
        retries=2,
        model_settings={"anthropic_cache_instructions": "5m"},
    )
    result = agent.run_sync("recent fastmcp threads:\n\n" + "\n".join(lines))
    record_pydantic_ai_result(
        task_name="fastmcp_brief",
        model=MODEL,
        result=result,
        metadata={"thread_count": len(threads)},
    )
    return result.output


# a bare URL makes discord render a full link-preview card per item, which is
# what turned the first brief into a wall. A masked link — [text](url) — is not
# unfurled at all, so the whole brief stays one compact block.
SEVERITY_MARK = {
    "broken": "🔴",
    "bug": "🟠",
    "waiting": "🟡",
    "decision": "🔵",
}


def render(brief: Brief, window_hours: int) -> str:
    """Ranked items as compact markdown, sized to fit Discord."""
    if not brief.items:
        return ""

    out: list[str] = []
    used = 0
    shown = 0
    for item in brief.items:
        mark = SEVERITY_MARK.get(item.severity, "⚪")
        # headline carries the link so no bare url appears anywhere
        block = f"{mark} **[{item.headline}]({item.url})** `#{item.number}`\n-# {item.why}"
        if used + len(block) + 2 > BRIEF_CHAR_BUDGET:
            break
        out.append(block)
        used += len(block) + 2
        shown += 1

    dropped = len(brief.items) - shown
    tail = f"-# {shown} of {brief.considered} threads · last {window_hours}h"
    if dropped:
        tail += f" · {dropped} more"
    return "\n\n".join(out) + "\n\n" + tail


@flow(name="fastmcp-brief", log_prints=True, timeout_seconds=600)
def fastmcp_brief(window_hours: int = 6, ignore_briefed: bool = False) -> dict[str, Any]:
    logger = get_run_logger()

    threads = recent_github_events(window_hours)
    if not threads:
        logger.info("no github activity in the window — nothing to brief")
        return {"items": 0, "threads": 0}

    briefed: dict[str, str] = {} if ignore_briefed else (Variable.get(BRIEFED, default={}) or {})
    fresh = [
        t
        for t in threads
        if briefed.get(str(t.get("thread_id"))) != (t.get("updated_at") or "")
    ]
    if not fresh:
        logger.info("all %d threads already briefed", len(threads))
        return {"items": 0, "threads": len(threads), "fresh": 0}
    logger.info("%d of %d threads are new since the last brief", len(fresh), len(threads))
    threads = fresh

    brief = compose(threads, Secret.load("anthropic-api-key").get())
    body = render(brief, window_hours)

    # mark everything we looked at, including what the model discarded —
    # otherwise discarded threads are reconsidered on every single trip
    briefed.update({str(t.get("thread_id")): (t.get("updated_at") or "") for t in threads})
    if len(briefed) > BRIEFED_MAX:
        briefed = dict(sorted(briefed.items(), key=lambda kv: kv[1])[-BRIEFED_MAX:])
    if not ignore_briefed:
        Variable.set(BRIEFED, briefed, overwrite=True)

    if not body:
        # a brief with nothing in it is a correct outcome; emitting it anyway
        # would put an empty message in discord
        logger.info("looked at %d threads, none worth surfacing", len(threads))
        return {"items": 0, "threads": len(threads)}

    emit_event(
        event="hub.brief.ready",
        resource={
            "prefect.resource.id": "hub.brief.fastmcp",
            "prefect.resource.name": "fastmcp",
            "hubtopic": "fastmcp",
        },
        payload={"brief": body, "items": len(brief.items), "threads": len(threads)},
    )
    logger.info("emitted brief with %d items (%d chars)", len(brief.items), len(body))
    return {"items": len(brief.items), "threads": len(threads), "chars": len(body)}


if __name__ == "__main__":
    print(fastmcp_brief())
