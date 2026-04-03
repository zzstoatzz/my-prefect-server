"""
Morning flow — tag maintenance.

Daily at 0 13 * * * (8am CT), 1h before phi's reflection at 14:00 UTC.
NOT triggered by transform — runs on its own cron.

Phases 1-3: mechanical tag maintenance (dedup, relationships, storage).
Curation is handled by a separate flow (curate.py) triggered on completion.
"""

import hashlib
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import turbopuffer
from openai import OpenAI
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from prefect import flow, task
from prefect.blocks.system import Secret
from prefect.cache_policies import CachePolicy
from prefect.context import TaskRunContext
from prefect.variables import Variable

from mps.phi import (
    TagCluster,
    TagMerge,
    TagRelationship,
)

TAG_REL_NAMESPACE = "phi-tag-relationships"
TAG_REL_SCHEMA = {
    "tag_a": {"type": "string", "filterable": True},
    "tag_b": {"type": "string", "filterable": True},
    "relationship_type": {"type": "string", "filterable": True},
    "confidence": {"type": "float"},
    "evidence": {"type": "string"},
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _rel_id(tag_a: str, tag_b: str) -> str:
    """Deterministic ID for a tag relationship (order-independent)."""
    pair = tuple(sorted([tag_a, tag_b]))
    return f"rel-{pair[0]}-{pair[1]}"


# ---------------------------------------------------------------------------
# cache policies
# ---------------------------------------------------------------------------


class ByTagsHash(CachePolicy):
    """Cache by hash of all tags, scoped per task. Skip LLM if tag set unchanged."""

    def compute_key(
        self,
        task_ctx: TaskRunContext,
        inputs: dict[str, Any],
        flow_parameters: dict[str, Any],
        **kwargs: Any,
    ) -> str | None:
        tags_text = inputs.get("tags_text")
        if not tags_text:
            return None
        h = hashlib.md5(tags_text.encode()).hexdigest()[:12]
        task_key = task_ctx.task.task_key if task_ctx else "unknown"
        return f"morning-{task_key}/{h}"



# ---------------------------------------------------------------------------
# phase 1: tag collection + deduplication
# ---------------------------------------------------------------------------


@task
def collect_all_tags(tpuf_key: str) -> dict[str, Any]:
    """Read all tags from user namespaces + episodic, with co-occurrence data."""
    client = turbopuffer.Turbopuffer(api_key=tpuf_key, region="gcp-us-central1")

    tag_info: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "users": set(), "samples": [], "episodic_count": 0}
    )
    cooccurrences: dict[tuple[str, str], int] = defaultdict(int)
    user_tag_sets: dict[str, set[str]] = defaultdict(set)

    # scan user namespaces
    page = client.namespaces(prefix="phi-users-")
    for ns_summary in page.namespaces:
        handle = ns_summary.id.removeprefix("phi-users-").replace("_", ".")
        ns = client.namespace(ns_summary.id)
        try:
            response = ns.query(
                rank_by=("vector", "ANN", [0.5] * 1536),
                top_k=200,
                filters={"kind": ["Eq", "observation"]},
                include_attributes=["content", "tags"],
            )
            if response.rows:
                for row in response.rows:
                    row_tags = sorted(getattr(row, "tags", []) or [])
                    for tag in row_tags:
                        info = tag_info[tag]
                        info["count"] += 1
                        info["users"].add(handle)
                        user_tag_sets[handle].add(tag)
                        if len(info["samples"]) < 3:
                            info["samples"].append(row.content[:200])
                    # co-occurrence within same observation
                    for i, t1 in enumerate(row_tags):
                        for t2 in row_tags[i + 1 :]:
                            cooccurrences[(t1, t2)] += 1
        except Exception:
            pass

    # scan episodic namespace
    try:
        ns = client.namespace("phi-episodic")
        response = ns.query(
            rank_by=("vector", "ANN", [0.5] * 1536),
            top_k=200,
            include_attributes=["content", "tags"],
        )
        if response.rows:
            for row in response.rows:
                row_tags = sorted(getattr(row, "tags", []) or [])
                for tag in row_tags:
                    info = tag_info[tag]
                    info["episodic_count"] += 1
                    if len(info["samples"]) < 3:
                        info["samples"].append(row.content[:200])
                for i, t1 in enumerate(row_tags):
                    for t2 in row_tags[i + 1 :]:
                        cooccurrences[(t1, t2)] += 1
    except Exception:
        pass

    # serialize for prefect (sets -> lists, tuple keys -> string keys)
    return {
        "tag_info": {
            tag: {**info, "users": list(info["users"])}
            for tag, info in tag_info.items()
        },
        "cooccurrences": {
            f"{t1}|{t2}": count for (t1, t2), count in cooccurrences.items()
        },
        "user_tag_sets": {h: list(tags) for h, tags in user_tag_sets.items()},
    }


@task
def embed_tags(openai_key: str, tags: list[str]) -> dict[str, list[float]]:
    """Batch-embed all tag names with text-embedding-3-small."""
    if not tags:
        return {}
    client = OpenAI(api_key=openai_key)
    response = client.embeddings.create(model="text-embedding-3-small", input=tags)
    return {tags[i]: response.data[i].embedding for i in range(len(tags))}


class MergeProposal(BaseModel):
    merges: list[TagMerge] = Field(default_factory=list)


@task(
    cache_policy=ByTagsHash(),
    cache_expiration=timedelta(hours=4),
    persist_result=True,
    result_serializer="json",
)
async def identify_tag_merges(
    tags_text: str,
    tag_info: dict[str, dict],
    tag_embeddings: dict[str, list[float]],
    api_key: str,
    model_name: str = "claude-sonnet-4-6",
) -> list[dict[str, Any]]:
    """Give the LLM the full tag inventory and let it propose consolidations."""
    tag_lines = []
    for tag in sorted(tag_info.keys()):
        info = tag_info[tag]
        users = info.get("users", [])
        count = info.get("count", 0)
        episodic = info.get("episodic_count", 0)
        sample = (info.get("samples") or [""])[0][:120]
        tag_lines.append(
            f"  {tag}  (obs={count}, episodic={episodic}, users={len(users)})"
            f"\n    sample: {sample}"
        )

    inventory = "\n".join(tag_lines)

    model = AnthropicModel(
        model_name, provider=AnthropicProvider(api_key=api_key)
    )
    agent = Agent(
        model,
        system_prompt=(
            "you are consolidating the tag vocabulary for phi's memory graph.\n\n"
            "you will receive the full inventory of tags with usage counts and sample "
            "observations. your job:\n\n"
            "1. MERGE tags that are the same concept with different surface forms.\n"
            "   examples: 'attestation' / 'self-attestation' → canonical: 'attestation'\n"
            "   'ai_systems' / 'bot' / 'system-improvement' → canonical: 'ai-systems'\n\n"
            "2. mark tags that are RELATED but distinct — these should link, not merge.\n"
            "   example: 'epistemology' / 'social-epistemology' → related, not merged\n\n"
            "rules:\n"
            "- prefer lowercase, hyphenated canonical forms\n"
            "- group transitive merges into one entry\n"
            "- put related (but not merged) tags in the 'related' field\n"
            "- only merge when you're confident they're the same concept\n"
            "- it's fine to return zero merges if the tags are already clean\n"
            "- look for underscored vs hyphenated variants, singular/plural, "
            "abbreviations, and overlapping concepts"
        ),
        output_type=MergeProposal,
        name="tag-merger",
    )

    result = await agent.run(f"full tag inventory ({len(tag_info)} tags):\n{inventory}")
    return [m.model_dump() for m in result.output.merges]


@task
def apply_tag_merges(
    tpuf_key: str,
    merges: list[dict[str, Any]],
) -> int:
    """Rewrite tags in turbopuffer observations to use canonical forms."""
    if not merges:
        return 0

    alias_map: dict[str, str] = {}
    for merge in merges:
        for alias in merge["aliases"]:
            alias_map[alias] = merge["canonical"]

    client = turbopuffer.Turbopuffer(api_key=tpuf_key, region="gcp-us-central1")
    updated = 0

    ns_ids: list[str] = []
    page = client.namespaces(prefix="phi-users-")
    ns_ids.extend(ns.id for ns in page.namespaces)
    ns_ids.append("phi-episodic")

    for ns_id in ns_ids:
        ns = client.namespace(ns_id)
        is_user_ns = ns_id.startswith("phi-users-")

        try:
            kwargs: dict[str, Any] = {
                "rank_by": ("vector", "ANN", [0.5] * 1536),
                "top_k": 200,
                "include_attributes": ["content", "tags", "created_at", "vector"],
            }
            if is_user_ns:
                kwargs["filters"] = {"kind": ["Eq", "observation"]}
                kwargs["include_attributes"].append("kind")
            else:
                kwargs["include_attributes"].append("source")
            response = ns.query(**kwargs)
        except Exception:
            continue

        if not response.rows:
            continue

        rows_to_upsert = []
        for row in response.rows:
            old_tags = list(getattr(row, "tags", []) or [])
            new_tags = [alias_map.get(t, t) for t in old_tags]
            seen: set[str] = set()
            deduped = []
            for t in new_tags:
                if t not in seen:
                    seen.add(t)
                    deduped.append(t)

            if deduped != old_tags:
                vec = getattr(row, "vector", None)
                if not vec:
                    continue
                row_data: dict[str, Any] = {
                    "id": row.id,
                    "vector": vec,
                    "content": row.content,
                    "tags": deduped,
                    "created_at": getattr(
                        row, "created_at", datetime.now(timezone.utc).isoformat()
                    ),
                }
                if is_user_ns:
                    row_data["kind"] = "observation"
                else:
                    row_data["source"] = getattr(row, "source", "tool")
                rows_to_upsert.append(row_data)

        if rows_to_upsert:
            schema: dict[str, Any] = {
                "content": {"type": "string", "full_text_search": True},
                "tags": {"type": "[]string", "filterable": True},
                "created_at": {"type": "string"},
            }
            if is_user_ns:
                schema["kind"] = {"type": "string", "filterable": True}
            else:
                schema["source"] = {"type": "string", "filterable": True}

            ns.write(
                upsert_rows=rows_to_upsert,
                distance_metric="cosine_distance",
                schema=schema,
            )
            updated += len(rows_to_upsert)

    return updated


# ---------------------------------------------------------------------------
# phase 2: tag relationship discovery
# ---------------------------------------------------------------------------


class ClusterProposal(BaseModel):
    clusters: list[TagCluster] = Field(default_factory=list)


@task(
    cache_policy=ByTagsHash(),
    cache_expiration=timedelta(hours=4),
    persist_result=True,
    result_serializer="json",
)
async def discover_tag_relationships(
    tags_text: str,
    tag_info: dict[str, dict],
    tag_embeddings: dict[str, list[float]],
    cooccurrences: dict[str, int],
    user_tag_sets: dict[str, list[str]],
    merged_aliases: set[str],
    api_key: str,
    model_name: str = "claude-sonnet-4-6",
) -> list[dict[str, Any]]:
    """Ask the LLM to identify thematic clusters, then derive pairwise edges."""
    tags = [t for t in sorted(tag_info.keys()) if t not in merged_aliases]

    cooccur_hints: dict[str, list[str]] = defaultdict(list)
    for pair_key, count in cooccurrences.items():
        t1, t2 = pair_key.split("|", 1)
        if t1 in merged_aliases or t2 in merged_aliases:
            continue
        if count >= 2:
            cooccur_hints[t1].append(f"{t2} ({count}x)")
            cooccur_hints[t2].append(f"{t1} ({count}x)")

    tag_lines = []
    for tag in tags:
        info = tag_info.get(tag, {})
        count = info.get("count", 0)
        episodic = info.get("episodic_count", 0)
        n_users = len(info.get("users", []))
        sample = (info.get("samples") or [""])[0][:120]
        cooccur_str = ""
        if tag in cooccur_hints:
            cooccur_str = f"\n    co-occurs with: {', '.join(cooccur_hints[tag][:5])}"
        tag_lines.append(
            f"  {tag}  (obs={count}, episodic={episodic}, users={n_users})"
            f"\n    sample: {sample}{cooccur_str}"
        )

    inventory = "\n".join(tag_lines)

    model = AnthropicModel(
        model_name, provider=AnthropicProvider(api_key=api_key)
    )
    agent = Agent(
        model,
        system_prompt=(
            "you are organizing phi's memory tags into thematic clusters.\n"
            "phi is a bluesky bot that remembers conversations and builds knowledge.\n\n"
            "you will receive the full tag inventory with usage counts, sample observations, "
            "and co-occurrence data. your job: identify thematic clusters — groups of tags "
            "that belong to the same area of phi's knowledge or experience.\n\n"
            "examples of good clusters:\n"
            "- 'phi's epistemic concerns': epistemology, memory, confabulation, self-attestation\n"
            "- 'technical interests': programming, ai-systems, infrastructure\n"
            "- 'social dynamics': community, trust, collaboration\n\n"
            "rules:\n"
            "- a tag can appear in multiple clusters (topics overlap)\n"
            "- clusters should have 2-8 tags — small enough to be coherent\n"
            "- assign cohesion 0.0-1.0: how tightly the tags relate to the cluster theme\n"
            "- name each cluster concisely (2-4 words)\n"
            "- describe what ties the tags together\n"
            "- not every tag needs a cluster — singletons with no thematic neighbors are fine to skip\n"
            "- use the sample observations and co-occurrence data to inform your groupings"
        ),
        output_type=ClusterProposal,
        name="tag-clusterer",
    )

    result = await agent.run(
        f"full tag inventory ({len(tags)} tags after merges):\n{inventory}"
    )

    relationships: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for cluster in result.output.clusters:
        members = [t for t in cluster.tags if t in set(tags)]
        for i, t1 in enumerate(members):
            for t2 in members[i + 1 :]:
                pair = tuple(sorted([t1, t2]))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                relationships.append(
                    TagRelationship(
                        tag_a=pair[0],
                        tag_b=pair[1],
                        relationship_type="related",
                        confidence=cluster.cohesion,
                        evidence=f"[{cluster.name}] {cluster.description}",
                    ).model_dump()
                )

    return relationships


# ---------------------------------------------------------------------------
# phase 3: store relationships in turbopuffer
# ---------------------------------------------------------------------------


@task
def store_tag_relationships(
    tpuf_key: str,
    openai_key: str,
    relationships: list[dict[str, Any]],
) -> int:
    """Write tag relationships to phi-tag-relationships namespace."""
    if not relationships:
        return 0

    client = turbopuffer.Turbopuffer(api_key=tpuf_key, region="gcp-us-central1")
    openai_client = OpenAI(api_key=openai_key)
    ns = client.namespace(TAG_REL_NAMESPACE)

    texts = [f"{r['tag_a']} — {r['tag_b']}: {r['evidence']}" for r in relationships]
    embeddings = openai_client.embeddings.create(
        model="text-embedding-3-small", input=texts
    )

    rows = []
    for i, rel in enumerate(relationships):
        rows.append(
            {
                "id": _rel_id(rel["tag_a"], rel["tag_b"]),
                "vector": embeddings.data[i].embedding,
                "tag_a": rel["tag_a"],
                "tag_b": rel["tag_b"],
                "relationship_type": rel["relationship_type"],
                "confidence": rel["confidence"],
                "evidence": rel["evidence"],
            }
        )

    ns.write(
        upsert_rows=rows,
        distance_metric="cosine_distance",
        schema=TAG_REL_SCHEMA,
    )
    return len(rows)


# ---------------------------------------------------------------------------
# main flow
# ---------------------------------------------------------------------------


@flow(name="morning", log_prints=True)
async def morning():
    """Morning flow: tag maintenance.

    Runs daily at 8am CT (13:00 UTC). Phases 1-3 clean up the tag graph.
    Curation is handled by a separate flow triggered on completion.
    """
    tpuf_key = (await Secret.load("turbopuffer-api-key")).get()
    openai_key = (await Secret.load("openai-api-key")).get()
    anthropic_key = (await Secret.load("anthropic-api-key")).get()
    model_name = await Variable.get("morning-model", default="claude-sonnet-4-6")
    print(f"using model: {model_name}")

    # --- phase 1: collect and deduplicate tags ---
    tag_data = collect_all_tags(tpuf_key)
    tag_info: dict[str, dict] = tag_data["tag_info"]
    cooccurrences: dict[str, int] = tag_data["cooccurrences"]
    user_tag_sets: dict[str, list[str]] = tag_data["user_tag_sets"]

    tags = sorted(tag_info.keys())
    if not tags:
        print("no tags found, nothing to do")
        return

    print(f"collected {len(tags)} unique tags across all namespaces")
    tag_embeddings = embed_tags(openai_key, tags)

    tags_text = "\n".join(tags)
    merge_dicts = await identify_tag_merges(
        tags_text, tag_info, tag_embeddings, anthropic_key, model_name=model_name
    )

    merged_aliases: set[str] = set()
    if merge_dicts:
        for m in merge_dicts:
            merged_aliases.update(m["aliases"])
        updated = apply_tag_merges(tpuf_key, merge_dicts)
        print(
            f"phase 1: merged {len(merged_aliases)} aliases into "
            f"{len(merge_dicts)} canonical tags, updated {updated} observations"
        )
    else:
        print("phase 1: no tag merges needed")

    # --- phase 2: discover relationships ---
    rel_dicts = await discover_tag_relationships(
        tags_text,
        tag_info,
        tag_embeddings,
        cooccurrences,
        user_tag_sets,
        merged_aliases,
        anthropic_key,
        model_name=model_name,
    )
    print(f"phase 2: discovered {len(rel_dicts)} tag relationships")

    # --- phase 3: store in turbopuffer ---
    if rel_dicts:
        stored = store_tag_relationships(tpuf_key, openai_key, rel_dicts)
        print(f"phase 3: stored {stored} relationships in {TAG_REL_NAMESPACE}")
    else:
        print("phase 3: no relationships to store")


if __name__ == "__main__":
    import asyncio

    asyncio.run(morning())
