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
from datetime import UTC, datetime, timedelta
from typing import Any

import duckdb
import httpx
import turbopuffer
from mps.blocks import secret
from mps.phi import clean_handle, patch as patch_row, row as make_row, row_strings, row_text
from mps.spend import record_openai_embedding_response, record_pydantic_ai_result
from openai import OpenAI
from prefect import flow, get_run_logger, task
from prefect.cache_policies import CachePolicy
from prefect.context import TaskRunContext
from prefect.tasks import exponential_backoff
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
from pydantic_ai.providers.anthropic import AnthropicProvider
from turbopuffer.types import AttributeSchemaConfigParam
from turbopuffer.types.custom import Filter

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

default to they/them pronouns unless the person has stated their own
pronouns in the data you've been given (bio, posts, observations).
never assume gender from a name or handle.
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
        return f"compact/haiku-5-5/{handle}/{h}"


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
        "handle",
        "observation_count",
        "interaction_count",
        "first_seen",
        "last_interaction",
        "top_tags",
        "recency_score",
    ]
    return [dict(zip(columns, row, strict=True)) for row in rows]


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
    retries=3,
    retry_delay_seconds=exponential_backoff(backoff_factor=15),
    retry_jitter_factor=1,
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
    model = AnthropicModel("claude-haiku-5-5", provider=AnthropicProvider(api_key=api_key))
    # compact iterates over top authors; the SYSTEM_PROMPT is constant across
    # the per-user loop, so caching it once and reusing on every call is the
    # biggest single lever for this flow.
    agent = Agent[None, str](
        model,
        system_prompt=SYSTEM_PROMPT,
        name="phi-compactor",
        model_settings=AnthropicModelSettings(
            anthropic_cache_instructions="5m", anthropic_effort="low"
        ),
    )

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
    record_pydantic_ai_result(
        task_name="synthesize_summary",
        model="claude-haiku-5-5",
        result=result,
        metadata={"handle": handle},
    )
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
    embedding_response = openai_client.embeddings.create(
        model="text-embedding-3-small",
        input=summary,
    )
    record_openai_embedding_response(
        task_name="write_summary_to_turbopuffer",
        model="text-embedding-3-small",
        response=embedding_response,
        item_count=1,
        metadata={"handle": handle},
    )
    embedding = embedding_response.data[0].embedding

    client = turbopuffer.Turbopuffer(api_key=tpuf_key, region="gcp-us-central1")
    ns_name = f"phi-users-{clean_handle(handle)}"
    ns = client.namespace(ns_name)

    summary_row = make_row(
        _summary_id(handle),
        embedding,
        kind="summary",
        content=summary,
        tags=[],
        created_at=datetime.now(UTC).isoformat(),
    )
    ns.write(
        upsert_rows=[summary_row],
        distance_metric="cosine_distance",
        schema={
            "kind": {"type": "string", "filterable": True},
            "content": {"type": "string", "full_text_search": True},
            "tags": {"type": "[]string", "filterable": True},
            "created_at": {"type": "string"},
            "updated_at": {"type": "string"},
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
- ADD: a fact the existing knowledge does not already state, including one that corrects it
- NOOP: this is already known — skip

if you can't say anything meaningful beyond what's already known, return empty.
use lowercase. be concrete, not vague.

default to they/them pronouns unless the person has stated their own
pronouns in the data you've been given (bio, posts, prior observations).
never assume gender from a name or handle."""


class LikesObservation(BaseModel):
    author_handle: str
    content: str = Field(description="one atomic fact about this person")
    tags: list[str] = Field(description="1-3 lowercase tags")
    action: str = Field(description="ADD or NOOP")
    # populated post-extraction by the orchestrator from the liked-post URIs
    # that fed the LLM call. the model itself doesn't see URIs (only text).
    source_uris: list[str] = Field(default_factory=list)


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
        return f"likes-obs/haiku-5-5/{handle}/{h}"


@task
def load_recent_liked_posts(snap_path: str) -> dict[str, list[dict[str, str]]]:
    """Load liked posts from last 7 days, grouped by author handle."""
    db = duckdb.connect(snap_path, read_only=True)
    try:
        rows = db.execute("""
            SELECT subject_uri, author_handle, author_did, text, created_at,
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

    columns = [
        "subject_uri",
        "author_handle",
        "author_did",
        "text",
        "created_at",
        "liked_at",
        "embed_type",
        "embed_text",
    ]
    by_author: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        post = dict(zip(columns, row, strict=True))
        by_author[post["author_handle"]].append(post)
    return dict(by_author)


ACTIVE_OBSERVATIONS: Filter = (
    "And",
    [("kind", "Eq", "observation"), ("status", "NotEq", "superseded")],
)


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
            filters=ACTIVE_OBSERVATIONS,
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
    retries=3,
    retry_delay_seconds=exponential_backoff(backoff_factor=15),
    retry_jitter_factor=1,
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
    model = AnthropicModel("claude-haiku-5-5", provider=AnthropicProvider(api_key=api_key))
    agent = Agent[None, LikesExtractionResult](
        model,
        system_prompt=LIKES_SYSTEM_PROMPT,
        output_type=LikesExtractionResult,
        name="likes-observer",
        model_settings=AnthropicModelSettings(
            anthropic_cache_instructions="5m", anthropic_effort="low"
        ),
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
    record_pydantic_ai_result(
        task_name="extract_likes_observations",
        model="claude-haiku-5-5",
        result=result,
        metadata={"handle": handle},
    )
    return [obs.model_dump() for obs in result.output.observations]


def _observation_id(handle: str, content: str) -> str:
    """Deterministic ID for a likes-derived observation."""
    return hashlib.sha256(f"user-{handle}-observation-{content}".encode()).hexdigest()[:16]


USER_NAMESPACE_SCHEMA: dict[str, str | AttributeSchemaConfigParam] = {
    "kind": {"type": "string", "filterable": True},
    "status": {"type": "string", "filterable": True},  # active | superseded
    "content": {"type": "string", "full_text_search": True},
    "tags": {"type": "[]string", "filterable": True},
    "supersedes": {"type": "string"},  # id of observation this replaces
    "source_uris": {"type": "[]string"},  # AT-URIs backing the observation
    "created_at": {"type": "string"},
    "updated_at": {"type": "string"},
}


# Same contract as the bot's observation-reconciler
# (bot/src/bot/memory/extraction.py): both writers share phi-users-* rows, so a
# likes-derived fact must supersede its neighbours the way an extracted one does.
RECONCILIATION_SYSTEM_PROMPT = """\
You reconcile a NEW observation against up to three EXISTING observations from memory, numbered nearest first.

Decide one action, and list in `targets` the numbers of the existing observations it applies to:
- ADD: the new observation contains genuinely different information from every existing one. keep them all. no targets.
- UPDATE: the new observation refines, corrects, or supersedes the targets. return merged content and tags; that one row replaces every target.
- DELETE: the targets are wrong, outdated, or fully redundant given the new one. the new one will be stored separately.
- NOOP: the new observation adds nothing beyond the target. discard it.

Judge each existing observation on its own. One that is merely about the same topic is not a target; only target what the new observation actually restates, refines, or contradicts. If the new observation contradicts or replaces more than one, target all of them.
Two observations about different things of the same kind (two projects, two tools, two places, two events) are both true: that is ADD, even when the wording is close. A target is replaced, so everything in it that is still true must survive in the merged content.
Write merged content the way the observations are written: lowercase, one fact, no longer than it needs to be.
When in doubt between ADD and NOOP, prefer NOOP. memory should be lean."""


class Reconciliation(BaseModel):
    action: str = Field(description="one of: ADD, UPDATE, DELETE, NOOP")
    targets: list[int] = Field(
        default_factory=list,
        description="numbers of the EXISTING observations the action applies to. empty for ADD.",
    )
    new_content: str | None = Field(
        default=None, description="merged content when action is UPDATE"
    )
    new_tags: list[str] | None = Field(
        default=None, description="merged tags when action is UPDATE"
    )
    reason: str = Field(default="", description="brief explanation")


def reconciliation_prompt(existing: list[dict[str, Any]], content: str, tags: list[str]) -> str:
    listed = "\n\n".join(
        f"EXISTING {n}: {row['content']}\nEXISTING {n} tags: {row['tags']}"
        for n, row in enumerate(existing, start=1)
    )
    return f"{listed}\n\nNEW observation: {content}\nNEW tags: {tags}"


def plan_observation_write(
    decision: Reconciliation,
    existing: list[dict[str, Any]],
    content: str,
    tags: list[str],
    source_uris: list[str],
) -> tuple[dict[str, Any] | None, list[str]]:
    """The observation to write, if any, and the ids it supersedes."""
    action = decision.action.upper()
    new = {"content": content, "tags": tags, "source_uris": source_uris, "supersedes": ""}
    if action == "NOOP":
        return None, []
    if action not in ("UPDATE", "DELETE"):
        return new, []
    targets = [
        existing[n - 1] for n in dict.fromkeys(decision.targets) if 1 <= n <= len(existing)
    ] or existing[:1]
    new["supersedes"] = targets[0]["id"]
    if action == "UPDATE":
        new["content"] = decision.new_content or content
        new["tags"] = decision.new_tags or tags
        new["source_uris"] = list(
            dict.fromkeys([uri for t in targets for uri in t["source_uris"]] + source_uris)
        )
    return new, [t["id"] for t in targets]


@task(
    retries=3,
    retry_delay_seconds=exponential_backoff(backoff_factor=15),
    retry_jitter_factor=1,
)
async def reconcile_likes_observation(
    handle: str,
    existing: list[dict[str, Any]],
    content: str,
    tags: list[str],
    api_key: str,
) -> Reconciliation:
    model = AnthropicModel("claude-haiku-5-5", provider=AnthropicProvider(api_key=api_key))
    agent = Agent[None, Reconciliation](
        model,
        system_prompt=RECONCILIATION_SYSTEM_PROMPT,
        output_type=Reconciliation,
        name="likes-reconciler",
        model_settings=AnthropicModelSettings(
            anthropic_cache_instructions="5m", anthropic_effort="low"
        ),
    )
    result = await agent.run(reconciliation_prompt(existing, content, tags))
    record_pydantic_ai_result(
        task_name="reconcile_likes_observation",
        model="claude-haiku-5-5",
        result=result,
        metadata={"handle": handle},
    )
    return result.output


@task
async def write_likes_observations_to_turbopuffer(
    tpuf_key: str,
    openai_key: str,
    anthropic_key: str,
    observations: list[dict[str, Any]],
):
    """Reconcile likes-derived observations against their neighbours and write them."""
    logger = get_run_logger()
    openai_client = OpenAI(api_key=openai_key)
    client = turbopuffer.Turbopuffer(api_key=tpuf_key, region="gcp-us-central1")

    def embed(text: str, handle: str) -> list[float]:
        response = openai_client.embeddings.create(model="text-embedding-3-small", input=text)
        record_openai_embedding_response(
            task_name="write_likes_observations_to_turbopuffer",
            model="text-embedding-3-small",
            response=response,
            item_count=1,
            metadata={"handle": handle},
        )
        return response.data[0].embedding

    counts: dict[str, int] = defaultdict(int)
    superseded = 0
    for obs in observations:
        if obs.get("action", "NOOP").upper() == "NOOP":
            continue

        handle = obs["author_handle"]
        content = obs["content"]
        tags = obs.get("tags", [])
        ns = client.namespace(f"phi-users-{clean_handle(handle)}")
        embedding = embed(content, handle)

        existing: list[dict[str, Any]] = []
        try:
            resp = ns.query(
                rank_by=("vector", "ANN", embedding),
                top_k=3,
                filters=ACTIVE_OBSERVATIONS,
                include_attributes=True,
            )
            existing = [
                {
                    "id": row.id,
                    "content": row_text(row, "content"),
                    "tags": row_strings(row, "tags"),
                    "source_uris": row_strings(row, "source_uris"),
                }
                for row in resp.rows or []
            ]
        except turbopuffer.NotFoundError:
            pass

        decision = (
            await reconcile_likes_observation(handle, existing, content, tags, anthropic_key)
            if existing
            else Reconciliation(action="ADD")
        )
        new, superseded_ids = plan_observation_write(
            decision, existing, content, tags, list(obs.get("source_uris") or [])
        )
        counts[decision.action.upper()] += 1
        if new is None:
            continue

        if new["content"] != content:
            embedding = embed(new["content"], handle)
        now = datetime.now(UTC).isoformat()
        new_id = _observation_id(handle, new["content"])
        ns.write(
            upsert_rows=[
                make_row(
                    new_id,
                    embedding,
                    kind="observation",
                    status="active",
                    content=new["content"],
                    tags=new["tags"],
                    supersedes=new["supersedes"],
                    source_uris=new["source_uris"],
                    created_at=now,
                    updated_at=now,
                )
            ],
            distance_metric="cosine_distance",
            schema=USER_NAMESPACE_SCHEMA,
        )
        stale = [i for i in superseded_ids if i != new_id]
        if stale:
            ns.write(patch_rows=[patch_row(i, status="superseded") for i in stale])
            superseded += len(stale)

    logger.info(f"likes observations: {dict(counts)}, {superseded} rows superseded")


@flow(name="phi-memory-synthesis", log_prints=True, timeout_seconds=1800)
async def compact():
    """Synthesize per-user relationship summaries from phi's memory."""
    logger = get_run_logger()
    db_path = os.environ.get(
        "ANALYTICS_DB_PATH",
        os.environ.get("PREFECT_LOCAL_STORAGE_PATH", "/tmp") + "/analytics.duckdb",
    )
    tpuf_key = await secret("turbopuffer-api-key")
    openai_key = await secret("openai-api-key")
    anthropic_key = await secret("anthropic-api-key")

    snap_path = snapshot_db(db_path)

    profiles = load_user_profiles(snap_path)
    logger.info(f"found {len(profiles)} users above threshold")

    summaries_written = 0
    sample_handles: list[str] = []
    for profile in profiles:
        handle = profile["handle"]
        bsky_profile = resolve_bsky_profile(handle)
        obs_text = load_user_observations(snap_path, handle)
        ix_text = load_user_interactions(snap_path, handle)
        stats_text = _format_stats(profile)

        summary = await synthesize_summary(
            handle,
            stats_text,
            obs_text,
            ix_text,
            anthropic_key,
            bsky_profile=bsky_profile,
        )
        write_summary_to_turbopuffer(tpuf_key, openai_key, handle, summary)
        summaries_written += 1
        if len(sample_handles) < 5:
            sample_handles.append(f"@{handle}:{profile['observation_count']}")
    if sample_handles:
        logger.info(
            f"compacted {summaries_written} user summaries; sample={', '.join(sample_handles)}"
        )

    # --- phase 2: extract observations from liked posts ---
    liked_by_author = load_recent_liked_posts(snap_path)
    all_observations: list[dict[str, Any]] = []
    actionable: list[dict[str, Any]] = []
    if liked_by_author:
        # limit to top 15 most-liked unique authors
        top_authors = sorted(
            liked_by_author.items(),
            key=lambda kv: len(kv[1]),
            reverse=True,
        )[:15]
        logger.info(f"extracting observations from {len(top_authors)} liked authors")

        for handle, posts in top_authors:
            liked_posts_text = _format_liked_posts(posts)
            if not liked_posts_text.strip():
                continue

            bsky_profile = resolve_bsky_profile(handle)
            existing = query_existing_knowledge(tpuf_key, handle)
            pubs = search_publications(handle)

            obs_dicts = await extract_likes_observations(
                handle,
                liked_posts_text,
                existing,
                bsky_profile,
                pubs,
                anthropic_key,
            )

            # attach the URIs of the liked posts that fed this batch to every
            # observation. coarse — the LLM doesn't tell us which post produced
            # which observation, but always-true: each extracted claim was
            # justified by something in this batch. dedup, preserve order.
            batch_uris = list(
                dict.fromkeys(p.get("subject_uri", "") for p in posts if p.get("subject_uri"))
            )
            for obs in obs_dicts:
                if not obs.get("source_uris") and batch_uris:
                    obs["source_uris"] = batch_uris

            all_observations.extend(obs_dicts)

        actionable = [o for o in all_observations if o.get("action", "").upper() != "NOOP"]
        if actionable:
            await write_likes_observations_to_turbopuffer(
                tpuf_key, openai_key, anthropic_key, actionable
            )
        logger.info(
            f"extracted {len(all_observations)} observations from liked posts "
            f"({len(actionable)} actionable)"
        )
    else:
        logger.info("no recent liked posts to extract observations from")


if __name__ == "__main__":
    import asyncio

    asyncio.run(compact())
