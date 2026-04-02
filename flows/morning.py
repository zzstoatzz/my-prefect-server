"""
Morning flow — tag maintenance + agentic semble curation.

Daily at 0 13 * * * (8am CT), 1h before phi's reflection at 14:00 UTC.
NOT triggered by transform — runs on its own cron.

Phases 1-3: mechanical tag maintenance (dedup, relationships, storage).
Phase 4: agentic curation — assembles phi's knowledge state, lets an LLM
decide what (if anything) deserves promotion to semble, then executes.
"""

import hashlib
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

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
from prefect.variables import Variable

from mps.phi import (
    CardPlan,
    CurationPlan,
    TagCluster,
    TagMerge,
    TagRelationship,
)

PHI_DID = "did:plc:65sucjiel52gefhcdcypynsr"
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


def _create_bsky_session(handle: str, password: str) -> dict[str, Any]:
    """Authenticate with bsky and return session (accessJwt, did)."""
    resp = httpx.post(
        "https://bsky.social/xrpc/com.atproto.server.createSession",
        json={"identifier": handle, "password": password},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _list_cosmik_records(did: str, collection: str) -> list[dict[str, Any]]:
    """Paginate through all records of a given cosmik collection type."""
    records: list[dict[str, Any]] = []
    cursor = None
    while True:
        params: dict[str, Any] = {
            "repo": did,
            "collection": collection,
            "limit": 100,
        }
        if cursor:
            params["cursor"] = cursor
        resp = httpx.get(
            "https://bsky.social/xrpc/com.atproto.repo.listRecords",
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        records.extend(data.get("records", []))
        cursor = data.get("cursor")
        if not cursor:
            break
    return records


def _list_cosmik_cards(did: str) -> list[dict[str, Any]]:
    return _list_cosmik_records(did, "network.cosmik.card")


def _list_cosmik_connections(did: str) -> list[dict[str, Any]]:
    return _list_cosmik_records(did, "network.cosmik.connection")


def _list_cosmik_collections(did: str) -> list[dict[str, Any]]:
    return _list_cosmik_records(did, "network.cosmik.collection")


def _list_cosmik_collection_links(did: str) -> list[dict[str, Any]]:
    return _list_cosmik_records(did, "network.cosmik.collectionLink")


def _create_pds_record(
    session: dict[str, Any], collection: str, record: dict[str, Any]
) -> dict[str, Any]:
    """Create a record on PDS via XRPC. Returns {uri, cid}."""
    resp = httpx.post(
        "https://bsky.social/xrpc/com.atproto.repo.createRecord",
        headers={"Authorization": f"Bearer {session['accessJwt']}"},
        json={
            "repo": session["did"],
            "collection": collection,
            "record": record,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


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


class ByCurationStateHash(CachePolicy):
    """Cache by hash of the full curation context. Skip LLM if state unchanged."""

    def compute_key(
        self,
        task_ctx: TaskRunContext,
        inputs: dict[str, Any],
        flow_parameters: dict[str, Any],
        **kwargs: Any,
    ) -> str | None:
        context = inputs.get("context")
        if not context:
            return None
        h = hashlib.md5(context.encode()).hexdigest()[:12]
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
# phase 4: agentic curation
# ---------------------------------------------------------------------------


@task
def collect_recent_observations(tpuf_key: str, top_k: int = 40) -> list[dict[str, Any]]:
    """Gather the most recent observations across all user namespaces."""
    client = turbopuffer.Turbopuffer(api_key=tpuf_key, region="gcp-us-central1")
    all_obs: list[dict[str, Any]] = []

    page = client.namespaces(prefix="phi-users-")
    for ns_summary in page.namespaces:
        handle = ns_summary.id.removeprefix("phi-users-").replace("_", ".")
        ns = client.namespace(ns_summary.id)
        try:
            response = ns.query(
                rank_by=("created_at", "desc"),
                top_k=top_k,
                filters={"kind": ["Eq", "observation"]},
                include_attributes=["content", "tags", "created_at"],
            )
            for row in response.rows or []:
                all_obs.append({
                    "handle": handle,
                    "content": row.content[:400],
                    "tags": list(getattr(row, "tags", []) or []),
                    "created_at": getattr(row, "created_at", ""),
                })
        except Exception:
            pass

    # sort by created_at desc, take top_k overall
    all_obs.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return all_obs[:top_k]


@task
def collect_recent_episodic(tpuf_key: str, top_k: int = 20) -> list[dict[str, Any]]:
    """Gather the most recent episodic memories."""
    client = turbopuffer.Turbopuffer(api_key=tpuf_key, region="gcp-us-central1")
    try:
        ns = client.namespace("phi-episodic")
        response = ns.query(
            rank_by=("created_at", "desc"),
            top_k=top_k,
            include_attributes=["content", "tags", "created_at", "source"],
        )
        return [
            {
                "content": row.content[:400],
                "tags": list(getattr(row, "tags", []) or []),
                "created_at": getattr(row, "created_at", ""),
                "source": getattr(row, "source", ""),
            }
            for row in (response.rows or [])
        ]
    except Exception:
        return []


@task
def assemble_curation_context(
    tag_info: dict[str, dict],
    tag_relationships: list[dict[str, Any]],
    existing_cards: list[dict[str, Any]],
    existing_connections: list[dict[str, Any]],
    existing_collections: list[dict[str, Any]],
    existing_collection_links: list[dict[str, Any]],
    recent_observations: list[dict[str, Any]],
    recent_episodic: list[dict[str, Any]],
) -> str:
    """Format all state into a single context string for the curation LLM."""
    sections: list[str] = []

    # existing cards
    card_lines = []
    for card in existing_cards:
        uri = card.get("uri", "")
        val = card.get("value", {})
        ctype = val.get("type", "?")
        if ctype == "NOTE":
            text = val.get("content", {}).get("text", "")[:200]
            card_lines.append(f"  [{ctype}] {uri}\n    {text}")
        elif ctype == "URL":
            content = val.get("content", {})
            url = content.get("url", "")
            meta = content.get("metadata", {})
            desc = meta.get("description", "") or meta.get("title", "")
            card_lines.append(f"  [{ctype}] {uri}\n    {url} — {desc[:150]}")
        else:
            card_lines.append(f"  [{ctype}] {uri}")
    sections.append(
        f"## existing cards ({len(existing_cards)})\n" + "\n".join(card_lines)
        if card_lines else "## existing cards (0)\nnone"
    )

    # existing connections
    conn_lines = []
    for conn in existing_connections:
        val = conn.get("value", {})
        src = val.get("source", "")
        tgt = val.get("target", "")
        note = val.get("note", "")[:100]
        conn_lines.append(f"  {src} → {tgt}  ({note})")
    sections.append(
        f"## existing connections ({len(existing_connections)})\n" + "\n".join(conn_lines)
        if conn_lines else "## existing connections (0)\nnone"
    )

    # existing collections
    coll_lines = []
    # build link index: collection uri -> list of card uris
    coll_card_map: dict[str, list[str]] = defaultdict(list)
    for link in existing_collection_links:
        val = link.get("value", {})
        coll_uri = val.get("collection", {}).get("uri", "")
        card_uri = val.get("card", {}).get("uri", "")
        if coll_uri and card_uri:
            coll_card_map[coll_uri].append(card_uri)
    for coll in existing_collections:
        uri = coll.get("uri", "")
        val = coll.get("value", {})
        name = val.get("name", "")
        desc = val.get("description", "")[:100]
        cards_in = coll_card_map.get(uri, [])
        coll_lines.append(f"  {uri} — {name} ({len(cards_in)} cards): {desc}")
    sections.append(
        f"## existing collections ({len(existing_collections)})\n" + "\n".join(coll_lines)
        if coll_lines else "## existing collections (0)\nnone"
    )

    # tag inventory (post-merge, with counts)
    tag_lines = []
    for tag in sorted(tag_info.keys()):
        info = tag_info[tag]
        count = info.get("count", 0)
        episodic = info.get("episodic_count", 0)
        n_users = len(info.get("users", []))
        tag_lines.append(f"  {tag} (obs={count}, episodic={episodic}, users={n_users})")
    sections.append(
        f"## tag inventory ({len(tag_info)} tags)\n" + "\n".join(tag_lines)
    )

    # tag relationships (clusters/edges)
    if tag_relationships:
        rel_lines = [
            f"  {r['tag_a']} — {r['tag_b']} ({r['confidence']:.2f}): {r['evidence'][:80]}"
            for r in tag_relationships[:30]
        ]
        sections.append(
            f"## tag relationships ({len(tag_relationships)} edges, showing top 30)\n"
            + "\n".join(rel_lines)
        )

    # recent observations
    if recent_observations:
        obs_lines = []
        for obs in recent_observations[:20]:
            tags_str = ", ".join(obs.get("tags", [])[:5])
            obs_lines.append(
                f"  [{obs.get('created_at', '?')[:10]}] @{obs['handle']}: "
                f"{obs['content'][:150]}  tags: [{tags_str}]"
            )
        sections.append(
            f"## recent observations ({len(recent_observations)}, showing top 20)\n"
            + "\n".join(obs_lines)
        )

    # recent episodic memories
    if recent_episodic:
        ep_lines = []
        for ep in recent_episodic[:10]:
            tags_str = ", ".join(ep.get("tags", [])[:5])
            ep_lines.append(
                f"  [{ep.get('created_at', '?')[:10]}] {ep['content'][:150]}  tags: [{tags_str}]"
            )
        sections.append(
            f"## recent episodic memories ({len(recent_episodic)}, showing top 10)\n"
            + "\n".join(ep_lines)
        )

    return "\n\n".join(sections)


@task(
    cache_policy=ByCurationStateHash(),
    cache_expiration=timedelta(hours=20),
    persist_result=True,
    result_serializer="json",
)
async def plan_curation(
    context: str,
    api_key: str,
    model_name: str = "claude-sonnet-4-6",
) -> dict[str, Any]:
    """Single LLM call: decide what (if anything) to promote to semble."""
    model = AnthropicModel(
        model_name, provider=AnthropicProvider(api_key=api_key)
    )
    agent = Agent(
        model,
        system_prompt=(
            "you are phi's curation engine. phi is a bluesky bot that builds knowledge "
            "through conversations. you decide what knowledge gets promoted to semble — "
            "phi's curated public knowledge layer (cosmik cards, connections, collections).\n\n"
            "## what semble is\n"
            "semble is the pristine, curated output. it holds hard-fought knowledge — "
            "insights earned through multiple interactions, not surface co-occurrence. "
            "most days nothing should change.\n\n"
            "## card types\n"
            "- NOTE: original insight or synthesis. use when phi has genuinely learned "
            "something that deserves to be crystallized.\n"
            "- URL: a resource phi has encountered and finds valuable. only promote URLs "
            "that appear multiple times across different conversations.\n\n"
            "## connections\n"
            "link two cards when there's a meaningful relationship. use descriptive "
            "connection_type: 'builds-on', 'contrasts', 'related', 'example-of'.\n\n"
            "## collections\n"
            "group cards into named themes. only create a collection when there are 3+ "
            "related cards. you can add cards to existing collections.\n\n"
            "## rules\n"
            "- set should_curate=false unless there is genuinely new knowledge worth promoting\n"
            "- never duplicate existing cards — check the existing cards list carefully\n"
            "- a card should represent a non-obvious insight, not a factual restatement\n"
            "- look for patterns across multiple observations/interactions, not single data points\n"
            "- use ref_key (e.g. 'card-1') on new cards so connections/collections can reference them\n"
            "- for connections to existing cards, use their at:// URI\n"
            "- for collections, you can reference existing_collection_uri to add to an existing one\n"
            "- quality over quantity — one excellent card beats three mediocre ones\n"
            "- you can also suggest connections between existing cards that aren't yet connected"
        ),
        output_type=CurationPlan,
        name="curation-planner",
    )

    result = await agent.run(
        f"here is phi's current knowledge state. decide what, if anything, "
        f"should be promoted to semble today.\n\n{context}"
    )
    return result.output.model_dump()


@task
def execute_curation_plan(
    session: dict[str, Any],
    plan: dict[str, Any],
    existing_cards: list[dict[str, Any]],
    existing_connections: list[dict[str, Any]],
) -> dict[str, int]:
    """Execute the curation plan: create cards, then connections + collections."""
    logger = get_run_logger()

    if not plan.get("should_curate"):
        logger.info(f"no curation needed: {plan.get('reasoning', '')}")
        return {"cards": 0, "connections": 0, "collections": 0}

    logger.info(f"executing curation: {plan.get('reasoning', '')}")

    # index existing card content/URLs for dedup
    existing_note_texts: set[str] = set()
    existing_urls: set[str] = set()
    for card in existing_cards:
        val = card.get("value", {})
        if val.get("type") == "NOTE":
            existing_note_texts.add(val.get("content", {}).get("text", "")[:200])
        elif val.get("type") == "URL":
            existing_urls.add(val.get("content", {}).get("url", ""))

    # index existing connections for dedup
    existing_conn_pairs: set[tuple[str, str]] = set()
    for conn in existing_connections:
        val = conn.get("value", {})
        existing_conn_pairs.add((val.get("source", ""), val.get("target", "")))

    # phase 1: create cards, build ref_key -> {uri, cid} map
    ref_map: dict[str, dict[str, str]] = {}
    cards_created = 0

    for card_plan in plan.get("cards", []):
        card_type = card_plan.get("card_type", "NOTE")
        ref_key = card_plan.get("ref_key", "")

        if card_type == "NOTE":
            text = card_plan.get("content", "")
            if text[:200] in existing_note_texts:
                logger.info(f"skipping duplicate NOTE card: {ref_key}")
                continue
            record = {
                "type": "NOTE",
                "content": {
                    "$type": "network.cosmik.card#noteContent",
                    "text": text,
                },
                "createdAt": datetime.now(timezone.utc).isoformat(),
            }
            if card_plan.get("title"):
                record["content"]["title"] = card_plan["title"]

        elif card_type == "URL":
            url = card_plan.get("content", "")
            if url in existing_urls:
                logger.info(f"skipping duplicate URL card: {ref_key}")
                continue
            record = {
                "type": "URL",
                "content": {
                    "$type": "network.cosmik.card#urlContent",
                    "url": url,
                    "metadata": {
                        "$type": "network.cosmik.card#urlMetadata",
                    },
                },
                "createdAt": datetime.now(timezone.utc).isoformat(),
            }
            if card_plan.get("title"):
                record["content"]["metadata"]["title"] = card_plan["title"]
            if card_plan.get("description"):
                record["content"]["metadata"]["description"] = card_plan["description"]
        else:
            logger.warning(f"unknown card type: {card_type}")
            continue

        try:
            result = _create_pds_record(session, "network.cosmik.card", record)
            ref_map[ref_key] = {"uri": result["uri"], "cid": result["cid"]}
            cards_created += 1
            logger.info(f"created {card_type} card: {ref_key} -> {result['uri']}")
        except Exception as e:
            logger.warning(f"failed to create card {ref_key}: {e}")

    # helper to resolve ref_key or at:// URI
    def resolve_ref(ref: str) -> dict[str, str] | None:
        if ref.startswith("at://"):
            # find cid from existing cards
            for card in existing_cards:
                if card.get("uri") == ref:
                    return {"uri": ref, "cid": card.get("cid", "")}
            return {"uri": ref, "cid": ""}
        return ref_map.get(ref)

    # phase 2: create connections
    conns_created = 0
    for conn_plan in plan.get("connections", []):
        source = resolve_ref(conn_plan.get("source", ""))
        target = resolve_ref(conn_plan.get("target", ""))
        if not source or not target:
            logger.warning(
                f"skipping connection — unresolved ref: "
                f"{conn_plan.get('source')} -> {conn_plan.get('target')}"
            )
            continue

        pair = (source["uri"], target["uri"])
        if pair in existing_conn_pairs:
            logger.info(f"skipping duplicate connection: {pair}")
            continue

        record = {
            "source": source["uri"],
            "target": target["uri"],
            "connectionType": conn_plan.get("connection_type", "related"),
        }
        if conn_plan.get("note"):
            record["note"] = conn_plan["note"][:1000]

        try:
            _create_pds_record(session, "network.cosmik.connection", record)
            existing_conn_pairs.add(pair)
            conns_created += 1
            logger.info(f"created connection: {pair}")
        except Exception as e:
            logger.warning(f"failed to create connection: {e}")

    # phase 3: create/update collections
    colls_created = 0
    for coll_plan in plan.get("collections", []):
        card_refs = coll_plan.get("card_refs", [])
        resolved_cards = []
        for ref in card_refs:
            resolved = resolve_ref(ref)
            if resolved:
                resolved_cards.append(resolved)

        if not resolved_cards:
            continue

        existing_uri = coll_plan.get("existing_collection_uri")
        if existing_uri:
            # add cards to existing collection — need its cid
            coll_ref = {"uri": existing_uri, "cid": ""}
        else:
            # create new collection
            record = {
                "name": coll_plan.get("name", "untitled")[:100],
                "accessType": "OPEN",
                "createdAt": datetime.now(timezone.utc).isoformat(),
            }
            if coll_plan.get("description"):
                record["description"] = coll_plan["description"][:500]

            try:
                result = _create_pds_record(
                    session, "network.cosmik.collection", record
                )
                coll_ref = {"uri": result["uri"], "cid": result["cid"]}
                colls_created += 1
                logger.info(f"created collection: {coll_plan.get('name')}")
            except Exception as e:
                logger.warning(f"failed to create collection: {e}")
                continue

        # link cards to collection
        for card_ref in resolved_cards:
            link_record = {
                "collection": {"uri": coll_ref["uri"], "cid": coll_ref["cid"]},
                "card": {"uri": card_ref["uri"], "cid": card_ref["cid"]},
                "addedBy": session["did"],
                "addedAt": datetime.now(timezone.utc).isoformat(),
            }
            try:
                _create_pds_record(
                    session, "network.cosmik.collectionLink", link_record
                )
            except Exception as e:
                logger.warning(f"failed to link card to collection: {e}")

    return {
        "cards": cards_created,
        "connections": conns_created,
        "collections": colls_created,
    }


# ---------------------------------------------------------------------------
# main flow
# ---------------------------------------------------------------------------


@flow(name="morning", log_prints=True)
async def morning():
    """Morning flow: tag maintenance + agentic semble curation.

    Runs daily at 8am CT (13:00 UTC). Phases 1-3 clean up the tag graph,
    phase 4 reasons about what deserves promotion to semble.
    """
    logger = get_run_logger()

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

    # --- phase 4: agentic curation ---
    try:
        bsky_handle = (await Secret.load("atproto-handle")).get()
        bsky_password = (await Secret.load("atproto-password")).get()
    except Exception:
        print("phase 4: skipped — atproto secrets not configured")
        return

    # gather state from both knowledge layers
    existing_cards = _list_cosmik_cards(PHI_DID)
    existing_connections = _list_cosmik_connections(PHI_DID)
    existing_collections = _list_cosmik_collections(PHI_DID)
    existing_collection_links = _list_cosmik_collection_links(PHI_DID)
    recent_obs = collect_recent_observations(tpuf_key)
    recent_ep = collect_recent_episodic(tpuf_key)

    print(
        f"phase 4: state — {len(existing_cards)} cards, "
        f"{len(existing_connections)} connections, "
        f"{len(existing_collections)} collections, "
        f"{len(recent_obs)} recent observations, "
        f"{len(recent_ep)} recent episodic"
    )

    # assemble context for LLM
    context = assemble_curation_context(
        tag_info=tag_info,
        tag_relationships=rel_dicts,
        existing_cards=existing_cards,
        existing_connections=existing_connections,
        existing_collections=existing_collections,
        existing_collection_links=existing_collection_links,
        recent_observations=recent_obs,
        recent_episodic=recent_ep,
    )

    # LLM decides what to curate
    plan = await plan_curation(context, anthropic_key, model_name=model_name)

    if not plan.get("should_curate"):
        print(f"phase 4: no curation needed — {plan.get('reasoning', '')}")
        return

    # execute the plan
    session = _create_bsky_session(bsky_handle, bsky_password)
    result = execute_curation_plan(session, plan, existing_cards, existing_connections)
    print(
        f"phase 4: created {result['cards']} cards, "
        f"{result['connections']} connections, "
        f"{result['collections']} collections"
    )


if __name__ == "__main__":
    import asyncio

    asyncio.run(morning())
