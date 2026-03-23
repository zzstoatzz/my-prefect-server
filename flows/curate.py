import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import duckdb
from pydantic_ai import Agent
from pydantic_ai.durable_exec.prefect import PrefectAgent, TaskConfig
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from prefect import flow, task, get_run_logger
from prefect.blocks.system import Secret

from mps.briefing import Briefing

SYSTEM_PROMPT = """\
you are a dashboard curator for a software developer's issue tracker.
given a list of scored items from github and tangled.org, produce a briefing
with exactly 4 themed sections. group by actionability, not by source.

the layout is a 2x2 grid, so always produce exactly 4 sections.
keep each section to 4-6 items max — be selective, not exhaustive.

section titles should be lowercase, short, action-oriented:
"needs review", "going stale", "quick wins", "watching"

each item note should be ~10 words of useful context.
the headline should be a single sentence summary.

## visual styling

each section has accent and priority fields to control presentation.

accent colors — pick the one that matches the section's mood:
- red: urgent, blocked, overdue items
- amber: warnings, going stale, needs attention soon
- emerald: positive signals, quick wins, ready to merge
- sky: informational, watching, low-urgency tracking
- violet: features, enhancements, new ideas

priority — all sections should use "normal" for the 2x2 grid layout.

do not set highlight on any items.
"""

def make_agent(api_key: str) -> PrefectAgent[Briefing]:
    """Build agent after API key is available (provider validates key at init)."""
    model = AnthropicModel("claude-haiku-4-5", provider=AnthropicProvider(api_key=api_key))
    agent = Agent(
        model,
        output_type=Briefing,
        system_prompt=SYSTEM_PROMPT,
        name="hub-curator",
    )
    return PrefectAgent(
        agent,
        model_task_config=TaskConfig(
            retries=2,
            retry_delay_seconds=[2.0, 5.0],
        ),
    )


@task
def load_items(db_path: str) -> str:
    """Read scored items from hub_action_items, format as text for the LLM."""
    # snapshot to bypass exclusive flock (same pattern as hub frontend)
    snap = "/tmp/curate_analytics_snapshot.duckdb"
    shutil.copy2(db_path, snap)
    db = duckdb.connect(snap, read_only=True)
    rows = db.execute(
        "SELECT source, repo, identifier, kind, title, url, "
        "author, labels, importance_score, updated "
        "FROM hub_action_items ORDER BY importance_score DESC LIMIT 200"
    ).fetchall()
    db.close()

    lines = []
    for r in rows:
        source, repo, ident, kind, title, url, author, labels, score, updated = r
        item_id = f"{source}:{repo}#{ident}"
        label_str = ", ".join(labels) if labels else ""
        lines.append(
            f"- [{item_id}] {kind}: {title} "
            f"(repo={repo}, author={author}, score={score:.2f}, "
            f"updated={updated}, labels=[{label_str}])"
        )
    return "\n".join(lines)


@task
def write_briefing(briefing: Briefing, path: str):
    Path(path).write_text(briefing.model_dump_json(indent=2))


@flow(name="curate", log_prints=True)
async def curate():
    logger = get_run_logger()
    db_path = os.environ.get(
        "ANALYTICS_DB_PATH",
        os.environ.get("PREFECT_LOCAL_STORAGE_PATH", "/tmp") + "/analytics.duckdb",
    )
    briefing_path = os.environ.get(
        "BRIEFING_PATH",
        str(Path(db_path).parent / "briefing.json"),
    )

    # load API key from Prefect Secret, build agent
    api_key = (await Secret.load("anthropic-api-key")).get()
    prefect_agent = make_agent(api_key)

    items_text = load_items(db_path)
    logger.info(f"loaded {items_text.count(chr(10)) + 1} items for curation")

    now = datetime.now(timezone.utc).isoformat()

    result = await prefect_agent.run(
        f"curate these items (current time: {now}):\n\n{items_text}"
    )
    briefing = result.output
    briefing.generated_at = now

    write_briefing(briefing, briefing_path)
    logger.info(f"wrote briefing: {briefing.headline}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(curate())
