"""Write the hub briefing: an LLM reads the scored action items and produces briefing.json."""

import hashlib
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
from mps.briefing import Briefing
from mps.spend import record_pydantic_ai_result
from prefect import flow, get_run_logger, task
from prefect.artifacts import create_markdown_artifact
from prefect.blocks.system import Secret
from prefect.cache_policies import CachePolicy
from prefect.context import TaskRunContext
from pydantic_ai import Agent
from pydantic_ai.durable_exec.prefect import PrefectAgent, TaskConfig
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider


@dataclass
class ByItemsContent(CachePolicy):
    """Cache briefing by content hash of items + system prompt."""

    def compute_key(
        self,
        task_ctx: TaskRunContext,
        inputs: dict[str, Any],
        flow_parameters: dict[str, Any],
        **kwargs: Any,
    ) -> str | None:
        items_text = inputs.get("items_text")
        if items_text is None:
            return None
        h = hashlib.md5((SYSTEM_PROMPT + items_text).encode()).hexdigest()[:12]
        return f"briefing/{h}"


SYSTEM_PROMPT = """\
you are a dashboard curator for a solo developer's issue tracker.
given scored items from github and tangled.org, produce a briefing
with exactly 4 sections. group by theme or status, not by source.
keep each section to 4-6 items. be selective, not exhaustive.

be honest and proportionate. most days are normal — say so.
don't manufacture urgency. reserve "critical", "urgent", "immediate"
for genuinely exceptional situations. use red accent sparingly.
lead with the most useful observation, not the most alarming one.
"""


def make_agent(api_key: str) -> PrefectAgent[Briefing]:
    """Build agent after API key is available (provider validates key at init)."""
    model = AnthropicModel("claude-haiku-4-5", provider=AnthropicProvider(api_key=api_key))
    agent = Agent(
        model,
        output_type=Briefing,
        system_prompt=SYSTEM_PROMPT,
        name="hub-curator",
        # cache the constant SYSTEM_PROMPT — input cache reads are 0.1× input
        # price; net win whenever a flow run does ≥2 agent.run() calls or two
        # runs land within the 5m TTL window.
        model_settings={"anthropic_cache_instructions": "5m"},
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
        source, repo, ident, kind, title, _url, author, labels, score, updated = r
        item_id = f"{source}:{repo}#{ident}"
        label_str = ", ".join(labels) if labels else ""
        lines.append(
            f"- [{item_id}] {kind}: {title} "
            f"(repo={repo}, author={author}, score={score:.2f}, "
            f"updated={updated}, labels=[{label_str}])"
        )
    return "\n".join(lines)


@task(
    cache_policy=ByItemsContent(),
    cache_expiration=timedelta(hours=4),
    persist_result=True,
    result_serializer="json",
)
async def generate_briefing(items_text: str, api_key: str) -> Briefing:
    """Call the LLM to curate items into a briefing. Cached by items content hash."""
    prefect_agent = make_agent(api_key)
    result = await prefect_agent.run(f"curate these items:\n\n{items_text}")
    record_pydantic_ai_result(
        task_name="generate_briefing",
        model="claude-haiku-4-5",
        result=result,
        metadata={"item_count": 0 if not items_text.strip() else items_text.count(chr(10)) + 1},
    )
    return result.output


@task
def write_briefing(briefing: Briefing, path: str):
    Path(path).write_text(briefing.model_dump_json(indent=2))


@task
def publish_briefing_artifact(briefing: Briefing) -> None:
    """Render the briefing on the flow run page, so 'what did that run
    produce?' doesn't require ssh to the analytics box."""
    lines = [f"# {briefing.title}", "", briefing.headline, ""]
    for section in briefing.sections:
        lines.append(f"## {section.title}")
        lines.append(section.summary)
        for item in section.items:
            marker = "**" if item.highlight else ""
            lines.append(f"- {marker}{item.item_id}{marker} — {item.note}")
        lines.append("")
    create_markdown_artifact(
        key="briefing",
        markdown="\n".join(lines),
        description="the generated hub briefing",
    )


@flow(name="brief", log_prints=True, timeout_seconds=900)
async def brief():
    logger = get_run_logger()
    db_path = os.environ.get(
        "ANALYTICS_DB_PATH",
        os.environ.get("PREFECT_LOCAL_STORAGE_PATH", "/tmp") + "/analytics.duckdb",
    )
    briefing_path = os.environ.get(
        "BRIEFING_PATH",
        str(Path(db_path).parent / "briefing.json"),
    )

    api_key = (await Secret.load("anthropic-api-key")).get()

    items_text = load_items(db_path)
    item_count = 0 if not items_text.strip() else items_text.count(chr(10)) + 1
    logger.info(f"loaded {item_count} items for curation")

    briefing = await generate_briefing(items_text, api_key)
    briefing.generated_at = datetime.now(UTC).isoformat()

    write_briefing(briefing, briefing_path)
    publish_briefing_artifact(briefing)
    logger.info(f"wrote briefing: {briefing.headline} ({briefing_path})")


if __name__ == "__main__":
    import asyncio

    asyncio.run(brief())
