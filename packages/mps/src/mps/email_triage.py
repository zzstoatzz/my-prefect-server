"""Daily email triage: pick what deserves a human, ask, record the answer.

Pure pieces of the ``email-triage`` flow, kept free of Prefect so they test
without a server: candidate selection from Jev's categories and confidence,
the models luna fills, and the Discord renderings.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EmailAction = Literal["reply", "act", "read", "ignore"]
Decision = Literal["archive", "reply", "todo", "ignore"]

SHORTLIST_MAX = 8
CANDIDATE_MAX = 60
CONFIDENT = 0.6
DISCORD_BUDGET = 1900


class Candidate(BaseModel):
    """One inbox message with Jev's verdict, as read from DuckDB."""

    message_id: str
    sender_name: str
    sender_address: str
    subject: str
    snippet: str
    received_at: str
    category: str
    confidence: float | None


class ShortlistItem(BaseModel):
    index: int = Field(description="0-based index of the email in the candidate list")
    reason: str = Field(
        description="why this needs the owner today, one clause under 120 characters"
    )
    suggested: EmailAction = Field(
        description="reply: needs a written answer; act: needs a non-email action; "
        "read: worth reading, no action; ignore: surfaced only because it looked personal"
    )


class Shortlist(BaseModel):
    """What luna hands back after reading the candidates."""

    summary: str = Field(description="one plain sentence about today's inbox, no hype")
    items: list[ShortlistItem] = Field(description="at most 8, most pressing first")


class TriageReply(BaseModel):
    """The owner's free-form answer, typed into the paused run."""

    instructions: str = Field(
        description="what to do about the shortlist, in your own words; 'nothing' is fine"
    )


class TriageAction(BaseModel):
    index: int = Field(description="0-based index into the shortlist")
    action: Decision
    note: str = Field(description="the owner's intent for this item, in one clause")


class TriageActions(BaseModel):
    """luna's reading of the owner's reply against the shortlist."""

    actions: list[TriageAction]
    summary: str = Field(description="one sentence confirming what was decided")


def select_candidates(rows: list[Candidate]) -> list[Candidate]:
    """Keep what a person might need to see.

    Personal and work mail always qualifies. Anything Jev was unsure about
    qualifies too, whatever the label, so a misfiled invite or invoice still
    reaches the shortlist. Confident notifications and promotions are dropped.
    Newest first, capped so the prompt stays small.
    """
    kept = [
        r
        for r in rows
        if r.category in ("personal", "work") or r.confidence is None or r.confidence < CONFIDENT
    ]
    kept.sort(key=lambda r: r.received_at, reverse=True)
    return kept[:CANDIDATE_MAX]


def render_candidates(candidates: list[Candidate]) -> str:
    lines = []
    for i, c in enumerate(candidates):
        conf = "?" if c.confidence is None else f"{c.confidence:.2f}"
        sender = c.sender_name or c.sender_address
        lines.append(
            f"[{i}] {c.received_at[:16]} | {sender} <{c.sender_address}> | {c.category} ({conf})\n"
            f"    subject: {c.subject}\n"
            f"    {c.snippet[:300]}"
        )
    return "\n".join(lines)


def trim_shortlist(shortlist: Shortlist, n_candidates: int) -> Shortlist:
    """Drop out-of-range indexes and anything past the cap, keeping order."""
    seen: set[int] = set()
    items: list[ShortlistItem] = []
    for item in shortlist.items:
        if 0 <= item.index < n_candidates and item.index not in seen:
            seen.add(item.index)
            items.append(item)
        if len(items) == SHORTLIST_MAX:
            break
    return Shortlist(summary=shortlist.summary, items=items)


def render_shortlist(
    shortlist: Shortlist, candidates: list[Candidate], total: int, run_url: str
) -> str:
    """The Discord post: summary, numbered items, how to answer. Under 2000 chars."""
    head = f"**email triage: {len(shortlist.items)} of {total} need you**\n\n{shortlist.summary}\n"
    tail = (
        f'\nanswer in your own words: `just triage-reply "..."`, or resume the run: {run_url}\n'
        "no answer within 12 hours closes it as unanswered."
    )
    lines = []
    for n, item in enumerate(shortlist.items, 1):
        c = candidates[item.index]
        sender = c.sender_name or c.sender_address
        lines.append(f"{n}. **{sender}**: {c.subject}\n   {item.reason} · _{item.suggested}_")
    body = head + "\n" + "\n".join(lines) + "\n" + tail
    while len(body) > DISCORD_BUDGET and lines:
        lines.pop()
        body = (
            head
            + "\n"
            + "\n".join(lines)
            + f"\n…and {len(shortlist.items) - len(lines)} more\n"
            + tail
        )
    return body


def render_outcome(
    actions: TriageActions, shortlist: Shortlist, candidates: list[Candidate]
) -> str:
    lines = [f"**email triage: decided**\n\n{actions.summary}\n"]
    for a in actions.actions:
        if 0 <= a.index < len(shortlist.items):
            c = candidates[shortlist.items[a.index].index]
            lines.append(
                f"- _{a.action}_ **{c.sender_name or c.sender_address}**: {c.subject}\n  {a.note}"
            )
    return "\n".join(lines)[:DISCORD_BUDGET]


SHORTLIST_INSTRUCTIONS = """\
you read a solo software developer's inbox once a day and pick the few emails
that need their attention today. you are given candidates that a classifier
already narrowed (personal and work mail, plus anything it was unsure about),
each with a 0-based index, sender, Jev's category and confidence, subject and
a snippet.

pick at most 8. a person writing to them directly beats any automated mail.
an invoice, a calendar invitation, a security alert, or a deadline counts as
work that may need action. newsletters, receipts, login links and marketing
do not, even when the classifier was unsure. most days two or three items is
right; zero is fine, and then the summary says so plainly.

reasons are one clause, under 120 characters, specific to the email.
"""

ACTIONS_INSTRUCTIONS = """\
the owner has read the shortlist and answered in their own words. map their
answer onto the shortlist items, by 0-based shortlist index. every item gets
exactly one decision: archive (done, nothing to do), reply (they will or want
to answer it), todo (an action outside email), or ignore (they said nothing
about it, or said to leave it). do not invent intent: an item they did not
mention is ignore unless they gave a blanket instruction that covers it.
the note is their intent for that item, in one clause, in their voice.
"""
