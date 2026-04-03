"""
Synthesize per-user relationship summaries from phi's observations + interactions.
Extract observations from liked posts and write to TurboPuffer.

Reads from dbt mart (int_phi_user_profiles) and staging models, sends to LLM,
writes summaries back to TurboPuffer where phi can consume them at conversation time.

Triggers on transform completion (parallel with brief).
"""

import hashlib
import os
import shutil
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import duckdb
import httpx
import turbopuffer
from openai import OpenAI
from pydantic import BaseModel, Field
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
        inputs: dict[str, Any],
        flow_parameters: dict[str, Any],
        **kwargs: Any,
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
def load_user_profiles(snap_path: str) -> list[dict[str, Any]]:
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
def resolve_bsky_profile(handle: str) -> dict[str, str] | None:
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


def _format_stats(profile: dict[str, Any]) -> str:
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
    bsky_profile: dict[str, str] | None = None,
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
        inputs: dict[str, Any],
        flow_parameters: dict[str, Any],
        **kwargs: Any,
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


# --- likes observation extraction ---

LIKES_SYSTEM_PROMPT = """\
you extract observations about a bluesky user from context nate provided by liking their posts.
you're given what phi already knows (if anything), the user's profile, their posts that nate liked,
and any publications they have.

produce 1-3 atomic observations. each should be a concrete fact (what they work on, what they write
about, a specific project, a notable take). include 1-3 lowercase tags.

for each observation, specify an action:
- ADD: genuinely new fact not covered by existing knowledge
- UPDATE: existing knowledge is stale or incomplete — provide the merged version
- NOOP: this is already known — skip

if you can't say anything meaningful beyond what's already known, return empty.
use lowercase. be concrete, not vague."""


class LikesObservation(BaseModel):
    author_handle: str
    content: str = Field(description="one atomic fact about this person")
    tags: list[str] = Field(description="1-3 lowercase tags")
    action: str = Field(description="ADD, UPDATE, or NOOP")
    supersedes_content: str | None = Field(
        default=None,
        description="for UPDATE: the old observation content this replaces",
    )


class LikesExtractionResult(BaseModel):
    observations: list[LikesObservation] = []


class ByLikedPostsHash(CachePolicy):
    """Cache likes extraction by author handle + liked posts content hash."""

    def compute_key(
        self,
        task_ctx: TaskRunContext,
        inputs: dict[str, Any],
        flow_parameters: dict[str, Any],
        **kwargs: Any,
    ) -> str | None:
        handle = inputs.get("handle")
        liked_posts_text = inputs.get("liked_posts_text")
        if not handle or not liked_posts_text:
            return None
        h = hashlib.md5(liked_posts_text.encode()).hexdigest()[:12]
        return f"likes-obs/{handle}/{h}"


@task
def load_recent_liked_posts(snap_path: str) -> dict[str, list[dict[str, str]]]:
    """Load liked posts from last 7 days, grouped by author handle."""
    db = duckdb.connect(snap_path, read_only=True)
    try:
        rows = db.execute("""
            SELECT author_handle, author_did, text, created_at,
                   liked_at, embed_type, embed_text
            FROM raw_liked_posts
            WHERE liked_at >= (now() - INTERVAL '7 days')::VARCHAR
              AND author_handle != ''
            ORDER BY liked_at DESC
        """).fetchall()
    except duckdb.CatalogException:
        db.close()
        return {}
    db.close()

    columns = ["author_handle", "author_did", "text", "created_at",
               "liked_at", "embed_type", "embed_text"]
    by_author: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        post = dict(zip(columns, row))
        by_author[post["author_handle"]].append(post)
    return dict(by_author)


@task
def query_existing_knowledge(tpuf_key: str, handle: str) -> str:
    """Query TurboPuffer for existing observations about this author."""
    client = turbopuffer.Turbopuffer(api_key=tpuf_key, region="gcp-us-central1")
    ns_name = f"phi-users-{clean_handle(handle)}"
    ns = client.namespace(ns_name)

    try:
        resp = ns.query(
            rank_by=("created_at", "desc"),
            top_k=10,
            filters={"kind": ["Eq", "observation"]},
            include_attributes=["content", "tags", "created_at"],
        )
        if not resp.rows:
            return "no prior knowledge"
        lines = []
        for row in resp.rows:
            tags = getattr(row, "tags", []) or []
            tag_str = f" [{', '.join(tags)}]" if tags else ""
            lines.append(f"- {row.content}{tag_str}")
        return "\n".join(lines)
    except Exception:
        return "no prior knowledge"


@task
def search_publications(handle: str) -> str:
    """Check pub-search for long-form writing by this author."""
    try:
        resp = httpx.get(
            "https://leaflet-search-backend.fly.dev/api/search",
            params={"author": handle, "limit": 5},
            timeout=10,
        )
        if resp.status_code != 200:
            return ""
        results = resp.json().get("results", [])
        if not results:
            return ""
        lines = []
        for r in results:
            title = r.get("title", "")
            url = r.get("url", "")
            lines.append(f"- {title} ({url})" if url else f"- {title}")
        return "publications:\n" + "\n".join(lines)
    except Exception:
        return ""


def _format_liked_posts(posts: list[dict[str, str]]) -> str:
    """Format liked posts as text for LLM input."""
    lines = []
    for p in posts:
        text = p.get("text", "").strip()
        embed = ""
        if p.get("embed_type") and p.get("embed_text"):
            embed = f" [{p['embed_type']}: {p['embed_text'][:200]}]"
        if text or embed:
            lines.append(f"- {text}{embed}")
    return "\n".join(lines)


@task(
    cache_policy=ByLikedPostsHash(),
    cache_expiration=timedelta(hours=4),
    persist_result=True,
    result_serializer="json",
)
async def extract_likes_observations(
    handle: str,
    liked_posts_text: str,
    existing_knowledge: str,
    bsky_profile: dict[str, str] | None,
    publications: str,
    api_key: str,
) -> list[dict[str, Any]]:
    """LLM extraction of observations from liked posts. Cached by posts hash."""
    model = AnthropicModel("claude-haiku-4-5", provider=AnthropicProvider(api_key=api_key))
    agent = Agent(
        model,
        system_prompt=LIKES_SYSTEM_PROMPT,
        output_type=LikesExtractionResult,
        name="likes-observer",
    )

    profile_section = f"handle: @{handle}\n"
    if bsky_profile:
        if bsky_profile.get("display_name"):
            profile_section += f"display name: {bsky_profile['display_name']}\n"
        if bsky_profile.get("bio"):
            profile_section += f"bio: {bsky_profile['bio']}\n"

    prompt = (
        f"author profile:\n{profile_section}\n"
        f"existing knowledge about @{handle}:\n{existing_knowledge}\n\n"
        f"posts nate liked by this author:\n{liked_posts_text}\n\n"
        f"{publications}"
    )
    result = await agent.run(prompt)
    return [obs.model_dump() for obs in result.output.observations]


def _observation_id(handle: str, content: str) -> str:
    """Deterministic ID for a likes-derived observation."""
    return hashlib.sha256(f"user-{handle}-observation-{content}".encode()).hexdigest()[:16]


USER_NAMESPACE_SCHEMA = {
    "kind": {"type": "string", "filterable": True},
    "content": {"type": "string", "full_text_search": True},
    "tags": {"type": "[]string", "filterable": True},
    "created_at": {"type": "string"},
}


@task
def write_likes_observations_to_turbopuffer(
    tpuf_key: str,
    openai_key: str,
    observations: list[dict[str, Any]],
):
    """Embed and write likes-derived observations to TurboPuffer user namespaces."""
    logger = get_run_logger()
    openai_client = OpenAI(api_key=openai_key)
    client = turbopuffer.Turbopuffer(api_key=tpuf_key, region="gcp-us-central1")

    added = 0
    updated = 0

    for obs in observations:
        action = obs.get("action", "NOOP").upper()
        if action == "NOOP":
            continue

        handle = obs["author_handle"]
        content = obs["content"]
        tags = obs.get("tags", [])
        ns_name = f"phi-users-{clean_handle(handle)}"
        ns = client.namespace(ns_name)

        embedding = openai_client.embeddings.create(
            model="text-embedding-3-small", input=content,
        ).data[0].embedding

        if action == "UPDATE" and obs.get("supersedes_content"):
            # find and delete the old observation by semantic similarity
            try:
                resp = ns.query(
                    rank_by=("vector", "ANN", embedding),
                    top_k=3,
                    filters={"kind": ["Eq", "observation"]},
                    include_attributes=["content"],
                )
                if resp.rows:
                    old_content = obs["supersedes_content"].lower()
                    for row in resp.rows:
                        if old_content in row.content.lower() or row.content.lower() in old_content:
                            ns.write(deletes=[row.id], distance_metric="cosine_distance")
                            break
            except Exception:
                pass  # proceed with upsert even if delete fails

        obs_id = _observation_id(handle, content)
        ns.write(
            upsert_rows=[{
                "id": obs_id,
                "vector": embedding,
                "kind": "observation",
                "content": content,
                "tags": tags,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }],
            distance_metric="cosine_distance",
            schema=USER_NAMESPACE_SCHEMA,
        )

        if action == "ADD":
            added += 1
        elif action == "UPDATE":
            updated += 1

    logger.info(f"likes observations: {added} added, {updated} updated, "
                f"{sum(1 for o in observations if o.get('action', '').upper() == 'NOOP')} skipped")


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

    # --- phase 2: extract observations from liked posts ---
    liked_by_author = load_recent_liked_posts(snap_path)
    if liked_by_author:
        # limit to top 15 most-liked unique authors
        top_authors = sorted(
            liked_by_author.items(), key=lambda kv: len(kv[1]), reverse=True,
        )[:15]
        logger.info(f"extracting observations from {len(top_authors)} liked authors")

        all_observations: list[dict[str, Any]] = []
        for handle, posts in top_authors:
            liked_posts_text = _format_liked_posts(posts)
            if not liked_posts_text.strip():
                continue

            bsky_profile = resolve_bsky_profile(handle)
            existing = query_existing_knowledge(tpuf_key, handle)
            pubs = search_publications(handle)

            obs_dicts = await extract_likes_observations(
                handle, liked_posts_text, existing, bsky_profile, pubs, anthropic_key,
            )
            all_observations.extend(obs_dicts)

        actionable = [o for o in all_observations if o.get("action", "").upper() != "NOOP"]
        if actionable:
            write_likes_observations_to_turbopuffer(tpuf_key, openai_key, actionable)
        logger.info(f"extracted {len(all_observations)} observations from liked posts "
                    f"({len(actionable)} actionable)")
    else:
        logger.info("no recent liked posts to extract observations from")


if __name__ == "__main__":
    import asyncio

    asyncio.run(compact())
