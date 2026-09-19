"""
Daily email triage: shortlist what needs nate, ask him in Discord, record
what he decides.

Runs after the morning ingest -> classify chain. Reads the last day and a
half of inbox mail with Jev's category and confidence, keeps personal and
work mail plus anything Jev was unsure about, and has luna pick at most
eight items. The shortlist goes to Discord through the `email shortlist ->
discord` automation with a link to this run, then the run pauses for typed
input. nate answers in his own words (`just triage-reply "..."` or the run
page); luna maps the answer onto the shortlist as one decision per item,
which is persisted to raw_email_triage and confirmed in Discord.

Nothing touches the mailbox yet: decisions are recorded, not executed.
"""

import os
import shutil
import tempfile
from typing import Literal

from mps.blocks import secret_sync
from mps.db import recent_classified_emails, write_email_triage
from mps.email_triage import (
    ACTIONS_INSTRUCTIONS,
    SHORTLIST_INSTRUCTIONS,
    Candidate,
    Shortlist,
    TriageActions,
    TriageReply,
    render_candidates,
    render_outcome,
    render_shortlist,
    select_candidates,
    trim_shortlist,
)
from mps.spend import record_pydantic_ai_result
from prefect import flow, get_run_logger, task
from prefect.events import emit_event
from prefect.exceptions import FlowPauseTimeout
from prefect.flow_runs import pause_flow_run
from prefect.runtime import flow_run
from prefect.states import Completed
from pydantic import BaseModel

MODEL = "gpt-5.6-luna"
UI_RUN_URL = "https://prefect-server.waow.tech/runs/flow-run/{id}"
WINDOW_HOURS = 36
REPLY_TIMEOUT_SECONDS = 12 * 60 * 60


def _db_path() -> str:
    return os.environ.get(
        "ANALYTICS_DB_PATH",
        os.environ.get("PREFECT_LOCAL_STORAGE_PATH", "/tmp") + "/analytics.duckdb",
    )


@task
def load_candidates() -> list[Candidate]:
    """Snapshot the analytics db, read the window, apply the keep rules."""
    with tempfile.TemporaryDirectory(prefix="email-triage-") as tmp:
        snapshot = os.path.join(tmp, "analytics.duckdb")
        shutil.copyfile(_db_path(), snapshot)
        rows = recent_classified_emails(snapshot, hours=WINDOW_HOURS)
    return select_candidates(rows)


def _luna[T: BaseModel](
    api_key: str, output_type: type[T], instructions: str, prompt: str, task_name: str
) -> T:
    from pydantic_ai import Agent
    from pydantic_ai.models.openai import OpenAIResponsesModel
    from pydantic_ai.providers.openai import OpenAIProvider

    agent = Agent[None, T](
        OpenAIResponsesModel(MODEL, provider=OpenAIProvider(api_key=api_key)),
        output_type=output_type,
        instructions=instructions,
        name=task_name,
        retries=2,
    )
    result = agent.run_sync(prompt)
    record_pydantic_ai_result(task_name=task_name, model=MODEL, provider="openai", result=result)
    return result.output


@task(retries=2, retry_delay_seconds=[5, 15])
def shortlist_with_luna(candidates: list[Candidate], api_key: str) -> Shortlist:
    raw = _luna(
        api_key,
        Shortlist,
        SHORTLIST_INSTRUCTIONS,
        "candidates:\n\n" + render_candidates(candidates),
        "email_shortlist",
    )
    return trim_shortlist(raw, len(candidates))


@task(retries=2, retry_delay_seconds=[5, 15])
def read_reply_with_luna(
    reply: str, shortlist: Shortlist, candidates: list[Candidate], api_key: str
) -> TriageActions:
    lines = []
    for i, item in enumerate(shortlist.items):
        c = candidates[item.index]
        lines.append(f"[{i}] {c.sender_name or c.sender_address}: {c.subject} ({item.reason})")
    prompt = "shortlist:\n" + "\n".join(lines) + f"\n\nthe owner's answer:\n{reply}"
    return _luna(api_key, TriageActions, ACTIONS_INSTRUCTIONS, prompt, "email_triage_actions")


@task
def persist_decisions(
    actions: TriageActions,
    shortlist: Shortlist,
    candidates: list[Candidate],
    run_id: str,
    reply: str,
) -> int:
    decisions = [
        (candidates[shortlist.items[a.index].index].message_id, str(a.action), a.note)
        for a in actions.actions
        if 0 <= a.index < len(shortlist.items)
    ]
    return write_email_triage(decisions, run_id, reply, _db_path())


def _post(event: str, body: str, **payload: object) -> None:
    emit_event(
        event=event,
        resource={
            "prefect.resource.id": "hub.email.triage",
            "prefect.resource.name": "email",
            "hubtopic": "email",
        },
        payload={"body": body, **payload},
    )


@flow(name="email-triage", log_prints=True, timeout_seconds=REPLY_TIMEOUT_SECONDS + 1800)
def email_triage(post: Literal["discord", "log"] = "discord"):
    logger = get_run_logger()
    run_id = str(flow_run.id)

    candidates = load_candidates()
    if not candidates:
        logger.info("nothing personal, work, or uncertain in the last %d hours", WINDOW_HOURS)
        return Completed(name="Quiet", message="no candidates")

    api_key = secret_sync("openai-api-key")
    shortlist = shortlist_with_luna(candidates, api_key)
    message = render_shortlist(shortlist, candidates, len(candidates), UI_RUN_URL.format(id=run_id))
    logger.info(
        "shortlist: %d of %d candidates\n%s", len(shortlist.items), len(candidates), message
    )
    if not shortlist.items:
        return Completed(name="Quiet", message=shortlist.summary)
    if post == "discord":
        _post(
            "hub.email.shortlist", message, items=len(shortlist.items), candidates=len(candidates)
        )

    try:
        reply = pause_flow_run(
            wait_for_input=TriageReply, timeout=REPLY_TIMEOUT_SECONDS, poll_interval=30
        )
    except FlowPauseTimeout:
        return Completed(name="Unanswered", message="no reply within the window")

    actions = read_reply_with_luna(reply.instructions, shortlist, candidates, api_key)
    total = persist_decisions(actions, shortlist, candidates, run_id, reply.instructions)
    outcome = render_outcome(actions, shortlist, candidates)
    logger.info("recorded %d decisions (%d total)\n%s", len(actions.actions), total, outcome)
    if post == "discord":
        _post("hub.email.triage.done", outcome, decisions=len(actions.actions))
    return {
        "candidates": len(candidates),
        "shortlist": len(shortlist.items),
        "decisions": len(actions.actions),
    }


if __name__ == "__main__":
    email_triage(post="log")
