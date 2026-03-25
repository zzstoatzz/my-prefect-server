"""
Synthesize per-user relationship summaries from phi's observations + interactions.

Reads from dbt mart (int_phi_user_profiles) and staging models, sends to LLM,
writes summaries back to TurboPuffer where phi can consume them at conversation time.

Triggers on transform completion (parallel with brief).
"""

import hashlib
import os
import shutil
from datetime import datetime, timedelta, timezone

import duckdb
import httpx
import turbopuffer
from openai import OpenAI
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from prefect import flow, get_run_logger, task
from prefect.blocks.system import Secret
from prefect.cache_policies import CachePolicy
from prefect.context import TaskRunContext

from mps.phi import clean_handle

SYSTEM_PROMPT = """\
you synthesize relationship summaries for a bluesky bot named phi.
given observations and recent interactions with a user, produce a dense
paragraph that captures: who this person is, what they care about right
now, the tone of the relationship, and any notable patterns.

write as notes to phi's future self. use lowercase. be honest about
uncertainty. if the relationship is thin, say so — a thin summary beats
a fabricated one. include concrete details (projects, interests, topics)
not just vibes.

IMPORTANT: use ONLY facts present in the provided data. the user's
bluesky profile (handle, display name, bio) is included — use that for
their name and identity. never guess or infer names.
"""


class ByObservationsHash(CachePolicy):
    """Cache compact result by handle + observations content hash."""

    def compute_key(
        self,
        task_ctx: TaskRunContext,
        inputs: dict,
        flow_parameters: dict,
        **kwargs,
    ) -> str | None:
        handle = inputs.get("handle")
        observations_text = inputs.get("observations_text")
        if not handle or not observations_text:
            return None
        h = hashlib.md5(observations_text.encode()).hexdigest()[:12]
        return f"compact/{handle}/{h}"


@task
def snapshot_db(db_path: str) -> str:
    """Snapshot DuckDB once to avoid exclusive flock (same pattern as brief)."""
    snap = "/tmp/compact_analytics_snapshot.duckdb"
    shutil.copy2(db_path, snap)
    return snap


@task
def load_user_profiles(snap_path: str) -> list[dict]:
    """Read per-user profiles from the dbt enrichment model."""
    db = duckdb.connect(snap_path, read_only=True)
    rows = db.execute(
        "SELECT handle, observation_count, interaction_count, "
        "first_seen, last_interaction, top_tags, recency_score "
        "FROM int_phi_user_profiles ORDER BY recency_score DESC"
    ).fetchall()
    db.close()

    columns = [
        "handle", "observation_count", "interaction_count",
        "first_seen", "last_interaction", "top_tags", "recency_score",
    ]
    return [dict(zip(columns, row)) for row in rows]


@task
def load_user_observations(snap_path: str, handle: str) -> str:
    """Read observations for a specific user, formatted as text."""
    db = duckdb.connect(snap_path, read_only=True)
    rows = db.execute(
        "SELECT DISTINCT ON (observation_id) content, tags, created_at "
        "FROM raw_phi_observations "
        "WHERE handle = ? ORDER BY observation_id, fetched_at DESC",
        [handle],
    ).fetchall()
    db.close()

    lines = []
    for content, tags, created_at in rows:
        tag_str = f" [{', '.join(tags)}]" if tags else ""
        lines.append(f"- {content}{tag_str} ({created_at})")
    return "\n".join(lines)


@task
def load_user_interactions(snap_path: str, handle: str) -> str:
    """Read interactions for a specific user, formatted as text."""
    db = duckdb.connect(snap_path, read_only=True)
    rows = db.execute(
        "SELECT DISTINCT ON (interaction_id) content, created_at "
        "FROM raw_phi_interactions "
        "WHERE handle = ? ORDER BY interaction_id, fetched_at DESC "
        "LIMIT 20",
        [handle],
    ).fetchall()
    db.close()

    lines = []
    for content, created_at in rows:
        lines.append(f"[{created_at}]\n{content}")
    return "\n\n".join(lines)


@task
def resolve_bsky_profile(handle: str) -> dict | None:
    """Fetch display name and bio from the public Bluesky API."""
    try:
        resp = httpx.get(
            "https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile",
            params={"actor": handle},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "handle": data.get("handle", handle),
            "display_name": data.get("displayName", ""),
            "bio": data.get("description", ""),
        }
    except Exception:
        return None


def _format_stats(profile: dict) -> str:
    tags = ", ".join(profile.get("top_tags") or [])
    return (
        f"observations: {profile['observation_count']}, "
        f"interactions: {profile['interaction_count']}, "
        f"first seen: {profile['first_seen']}, "
        f"last interaction: {profile.get('last_interaction') or 'never'}, "
        f"top tags: [{tags}], "
        f"recency: {profile['recency_score']:.2f}"
    )


@task(
    cache_policy=ByObservationsHash(),
    cache_expiration=timedelta(hours=4),
    persist_result=True,
    result_serializer="json",
)
async def synthesize_summary(
    handle: str,
    stats_text: str,
    observations_text: str,
    interactions_text: str,
    api_key: str,
    bsky_profile: dict | None = None,
) -> str:
    """LLM synthesis of a relationship summary. Cached by observations hash."""
    model = AnthropicModel("claude-haiku-4-5", provider=AnthropicProvider(api_key=api_key))
    agent = Agent(model, system_prompt=SYSTEM_PROMPT, name="phi-compactor")

    profile_section = f"handle: @{handle}\n"
    if bsky_profile:
        if bsky_profile.get("display_name"):
            profile_section += f"display name: {bsky_profile['display_name']}\n"
        if bsky_profile.get("bio"):
            profile_section += f"bio: {bsky_profile['bio']}\n"

    prompt = (
        f"user profile:\n{profile_section}\n"
        f"stats: {stats_text}\n\n"
        f"observations:\n{observations_text}\n\n"
        f"recent interactions:\n{interactions_text}"
    )
    result = await agent.run(prompt)
    return result.output


def _summary_id(handle: str) -> str:
    """Stable, deterministic ID for a user's relationship summary."""
    return f"summary-{clean_handle(handle)}"


class BySummaryContent(CachePolicy):
    """Cache write by handle + summary text hash. Skips embed+upsert when unchanged."""

    def compute_key(
        self,
        task_ctx: TaskRunContext,
        inputs: dict,
        flow_parameters: dict,
        **kwargs,
    ) -> str | None:
        handle = inputs.get("handle")
        summary = inputs.get("summary")
        if not handle or not summary:
            return None
        h = hashlib.md5(summary.encode()).hexdigest()[:12]
        return f"compact-write/{handle}/{h}"


@task(
    cache_policy=BySummaryContent(),
    cache_expiration=timedelta(hours=4),
    persist_result=True,
)
def write_summary_to_turbopuffer(
    tpuf_key: str,
    openai_key: str,
    handle: str,
    summary: str,
):
    """Embed summary and upsert to the user's TurboPuffer namespace as kind=summary."""
    openai_client = OpenAI(api_key=openai_key)
    embedding = openai_client.embeddings.create(
        model="text-embedding-3-small", input=summary,
    ).data[0].embedding

    client = turbopuffer.Turbopuffer(api_key=tpuf_key, region="gcp-us-central1")
    ns_name = f"phi-users-{clean_handle(handle)}"
    ns = client.namespace(ns_name)

    ns.write(
        upsert_rows=[
            {
                "id": _summary_id(handle),
                "vector": embedding,
                "kind": "summary",
                "content": summary,
                "tags": [],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
        distance_metric="cosine_distance",
        schema={
            "kind": {"type": "string", "filterable": True},
            "content": {"type": "string", "full_text_search": True},
            "tags": {"type": "[]string", "filterable": True},
            "created_at": {"type": "string"},
        },
    )


@flow(name="compact", log_prints=True)
async def compact():
    """Synthesize per-user relationship summaries from phi's memory."""
    logger = get_run_logger()
    db_path = os.environ.get(
        "ANALYTICS_DB_PATH",
        os.environ.get("PREFECT_LOCAL_STORAGE_PATH", "/tmp") + "/analytics.duckdb",
    )
    tpuf_key = (await Secret.load("turbopuffer-api-key")).get()
    openai_key = (await Secret.load("openai-api-key")).get()
    anthropic_key = (await Secret.load("anthropic-api-key")).get()

    snap_path = snapshot_db(db_path)

    profiles = load_user_profiles(snap_path)
    logger.info(f"found {len(profiles)} users above threshold")

    for profile in profiles:
        handle = profile["handle"]
        bsky_profile = resolve_bsky_profile(handle)
        obs_text = load_user_observations(snap_path, handle)
        ix_text = load_user_interactions(snap_path, handle)
        stats_text = _format_stats(profile)

        summary = await synthesize_summary(
            handle, stats_text, obs_text, ix_text, anthropic_key,
            bsky_profile=bsky_profile,
        )
        write_summary_to_turbopuffer(tpuf_key, openai_key, handle, summary)
        logger.info(f"@{handle}: compacted {profile['observation_count']} observations")


if __name__ == "__main__":
    import asyncio

    asyncio.run(compact())
