"""
Weave phi's memory graph — deduplicate tags, discover relationships, inject edges,
and selectively promote hard-fought knowledge to cosmik records.

Triggered on transform completion (parallel with compact and brief).
Reads from TurboPuffer directly (no DuckDB needed — tags/observations live in tpuf).
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

from mps.phi import TagMerge, TagRelationship

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


# ---------------------------------------------------------------------------
# cache policies
# ---------------------------------------------------------------------------


class ByTagsHash(CachePolicy):
    """Cache by hash of all tags. Skip LLM if tag set unchanged."""

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
        return f"weave-tags/{h}"


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
) -> list[dict[str, Any]]:
    """Cluster tags by embedding similarity, LLM confirms merges."""
    tags = list(tag_embeddings.keys())

    # find high-similarity pairs (>= 0.85) as merge candidates
    candidates: list[tuple[str, str, float]] = []
    for i, t1 in enumerate(tags):
        for t2 in tags[i + 1 :]:
            sim = cosine_similarity(tag_embeddings[t1], tag_embeddings[t2])
            if sim >= 0.85:
                candidates.append((t1, t2, sim))

    if not candidates:
        return []

    candidates.sort(key=lambda x: -x[2])
    candidates_text = "\n".join(
        f"- \"{t1}\" <-> \"{t2}\" (similarity: {sim:.3f})\n"
        f"  {t1} context: {(tag_info.get(t1) or {}).get('samples', [''])[0][:100]}\n"
        f"  {t2} context: {(tag_info.get(t2) or {}).get('samples', [''])[0][:100]}"
        for t1, t2, sim in candidates[:30]  # cap at 30 pairs
    )

    model = AnthropicModel(
        "claude-haiku-4-5", provider=AnthropicProvider(api_key=api_key)
    )
    agent = Agent(
        model,
        system_prompt=(
            "you review tag merge candidates for a memory graph. for each pair, decide:\n"
            "- MERGE: same concept — pick the canonical form\n"
            "- RELATE: distinct but related — don't merge\n"
            "- SKIP: not meaningfully related despite embedding similarity\n\n"
            "prefer lowercase, hyphenated canonical forms (e.g. 'ai-systems' not 'AI_systems').\n"
            "group transitive merges (if a merges with b and b merges with c, "
            "produce one merge with canonical + all aliases).\n"
            "put RELATE pairs in the 'related' field of the merge they're closest to, "
            "or omit if they don't belong to any merge group."
        ),
        output_type=MergeProposal,
        name="tag-merger",
    )

    result = await agent.run(f"merge candidates:\n{candidates_text}")
    # serialize to dicts for prefect result persistence
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

    # collect all namespaces to scan
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
            # deduplicate preserving order
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


class RelationshipProposal(BaseModel):
    relationships: list[TagRelationship] = Field(default_factory=list)


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
) -> list[dict[str, Any]]:
    """Score and LLM-confirm relationships between non-merged tags."""
    tags = [t for t in tag_embeddings if t not in merged_aliases]

    # score tag pairs by combined signal
    scored: list[tuple[str, str, float, str]] = []  # (a, b, score, reason)
    for i, t1 in enumerate(tags):
        for t2 in tags[i + 1 :]:
            sim = cosine_similarity(tag_embeddings[t1], tag_embeddings[t2])
            if sim < 0.4:
                continue

            # co-occurrence score
            pair_key = "|".join(sorted([t1, t2]))
            cooccur = cooccurrences.get(pair_key, 0)

            # shared users score
            shared_users = sum(
                1
                for tags_list in user_tag_sets.values()
                if t1 in tags_list and t2 in tags_list
            )

            # combine signals
            score = sim * 0.5
            if cooccur > 0:
                score += min(cooccur / 5, 0.3)  # cap at 0.3
            if shared_users > 0:
                score += min(shared_users / 3, 0.2)  # cap at 0.2

            if score >= 0.5:
                reason = f"sim={sim:.2f}, cooccur={cooccur}, shared_users={shared_users}"
                scored.append((t1, t2, score, reason))

    if not scored:
        return []

    scored.sort(key=lambda x: -x[2])
    candidates_text = "\n".join(
        f"- \"{t1}\" <-> \"{t2}\" (score: {score:.2f}, {reason})\n"
        f"  {t1}: {(tag_info.get(t1) or {}).get('samples', [''])[0][:100]}\n"
        f"  {t2}: {(tag_info.get(t2) or {}).get('samples', [''])[0][:100]}"
        for t1, t2, score, reason in scored[:30]
    )

    model = AnthropicModel(
        "claude-haiku-4-5", provider=AnthropicProvider(api_key=api_key)
    )
    agent = Agent(
        model,
        system_prompt=(
            "you review candidate tag relationships for a memory graph belonging to phi, "
            "a bluesky bot that remembers conversations.\n\n"
            "for each pair, decide if there's a genuine conceptual relationship:\n"
            "- RELATED: broadly connected concepts\n"
            "- SUBTOPIC: one is a narrower form of the other\n"
            "- OVERLAPPING: partially shared meaning\n"
            "- SKIP: co-occurrence is coincidental, not conceptual\n\n"
            "assign confidence 0.0-1.0. be honest — surface co-occurrence ≠ real relationship.\n"
            "provide brief evidence for each accepted relationship."
        ),
        output_type=RelationshipProposal,
        name="tag-relator",
    )

    result = await agent.run(f"relationship candidates:\n{candidates_text}")
    return [r.model_dump() for r in result.output.relationships]


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

    # batch embed relationship descriptions for vector field
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
# phase 4: cosmik promotion
# ---------------------------------------------------------------------------


def _create_bsky_session(handle: str, password: str) -> dict[str, Any]:
    """Authenticate with bsky and return session (accessJwt, did)."""
    resp = httpx.post(
        "https://bsky.social/xrpc/com.atproto.server.createSession",
        json={"identifier": handle, "password": password},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _list_cosmik_cards(did: str) -> list[dict[str, Any]]:
    """List all cosmik cards for a DID (public, no auth needed)."""
    cards: list[dict[str, Any]] = []
    cursor = None
    while True:
        params: dict[str, Any] = {
            "repo": did,
            "collection": "network.cosmik.card",
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
        cards.extend(data.get("records", []))
        cursor = data.get("cursor")
        if not cursor:
            break
    return cards


def _list_cosmik_connections(did: str) -> list[dict[str, Any]]:
    """List existing cosmik connections (public, no auth)."""
    conns: list[dict[str, Any]] = []
    cursor = None
    while True:
        params: dict[str, Any] = {
            "repo": did,
            "collection": "network.cosmik.connection",
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
        conns.extend(data.get("records", []))
        cursor = data.get("cursor")
        if not cursor:
            break
    return conns


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


def _match_cards_to_tags(
    cards: list[dict[str, Any]],
    tag_info: dict[str, dict],
    openai_key: str,
) -> dict[str, list[dict[str, Any]]]:
    """Match cosmik cards to tags by content similarity.

    Returns tag -> list of cards that are relevant to that tag.
    """
    if not cards or not tag_info:
        return {}

    openai_client = OpenAI(api_key=openai_key)

    # embed card content
    card_texts = []
    for card in cards:
        val = card.get("value", {})
        if val.get("type") == "NOTE":
            card_texts.append(val.get("content", {}).get("text", "")[:500])
        elif val.get("type") == "URL":
            content = val.get("content", {})
            card_texts.append(
                f"{content.get('title', '')} {content.get('description', '')}".strip()
                or content.get("url", "")
            )
        else:
            card_texts.append("")

    # filter out empty
    valid = [(i, t) for i, t in enumerate(card_texts) if t]
    if not valid:
        return {}

    card_embeddings = openai_client.embeddings.create(
        model="text-embedding-3-small", input=[t for _, t in valid]
    )
    card_vecs = {valid[j][0]: card_embeddings.data[j].embedding for j in range(len(valid))}

    # embed tags
    tags = list(tag_info.keys())
    tag_embeddings = openai_client.embeddings.create(
        model="text-embedding-3-small", input=tags
    )
    tag_vecs = {tags[j]: tag_embeddings.data[j].embedding for j in range(len(tags))}

    # match: for each tag, find cards with similarity >= 0.5
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for tag, tvec in tag_vecs.items():
        for card_idx, cvec in card_vecs.items():
            if cosine_similarity(tvec, cvec) >= 0.5:
                result[tag].append(cards[card_idx])

    return dict(result)


@task
def promote_connections(
    session: dict[str, Any],
    relationships: list[dict[str, Any]],
    tag_cards: dict[str, list[dict[str, Any]]],
    tag_info: dict[str, dict],
    existing_connections: list[dict[str, Any]],
) -> int:
    """Promote high-confidence relationships to cosmik connections."""
    # index existing connections by source+target for idempotency
    existing_pairs: set[tuple[str, str]] = set()
    for conn in existing_connections:
        val = conn.get("value", {})
        existing_pairs.add((val.get("source", ""), val.get("target", "")))

    created = 0
    for rel in relationships:
        if rel["confidence"] < 0.8:
            continue

        tag_a, tag_b = rel["tag_a"], rel["tag_b"]
        cards_a = tag_cards.get(tag_a, [])
        cards_b = tag_cards.get(tag_b, [])

        if not cards_a or not cards_b:
            continue

        # check observation support: >= 3 observations across >= 2 users, or >= 5 episodic
        info_a = tag_info.get(tag_a, {})
        info_b = tag_info.get(tag_b, {})
        total_obs = info_a.get("count", 0) + info_b.get("count", 0)
        total_users = len(
            set(info_a.get("users", [])) | set(info_b.get("users", []))
        )
        total_episodic = info_a.get("episodic_count", 0) + info_b.get(
            "episodic_count", 0
        )

        if not (
            (total_obs >= 3 and total_users >= 2) or total_episodic >= 5
        ):
            continue

        # use first card from each tag as source/target
        source_uri = cards_a[0]["uri"]
        target_uri = cards_b[0]["uri"]

        if (source_uri, target_uri) in existing_pairs:
            continue

        record = {
            "source": source_uri,
            "target": target_uri,
            "connectionType": "related",
            "note": rel["evidence"][:1000],
        }

        try:
            _create_pds_record(session, "network.cosmik.connection", record)
            existing_pairs.add((source_uri, target_uri))
            created += 1
        except Exception:
            pass

    return created


@task
def promote_collections(
    session: dict[str, Any],
    relationships: list[dict[str, Any]],
    tag_cards: dict[str, list[dict[str, Any]]],
) -> int:
    """Promote tag clusters with >= 3 cards to cosmik collections."""
    # build clusters: group tags that are connected by relationships
    adj: dict[str, set[str]] = defaultdict(set)
    for rel in relationships:
        if rel["confidence"] >= 0.7:
            adj[rel["tag_a"]].add(rel["tag_b"])
            adj[rel["tag_b"]].add(rel["tag_a"])

    # find connected components via BFS
    visited: set[str] = set()
    clusters: list[set[str]] = []
    for tag in adj:
        if tag in visited:
            continue
        cluster: set[str] = set()
        queue = [tag]
        while queue:
            t = queue.pop()
            if t in visited:
                continue
            visited.add(t)
            cluster.add(t)
            queue.extend(adj[t] - visited)
        if len(cluster) >= 2:
            clusters.append(cluster)

    created = 0
    for cluster in clusters:
        # collect all unique cards in this cluster
        cluster_cards: list[dict[str, Any]] = []
        seen_uris: set[str] = set()
        for tag in cluster:
            for card in tag_cards.get(tag, []):
                if card["uri"] not in seen_uris:
                    seen_uris.add(card["uri"])
                    cluster_cards.append(card)

        if len(cluster_cards) < 3:
            continue

        # derive collection name from tags
        name = " + ".join(sorted(cluster)[:3])
        if len(cluster) > 3:
            name += f" (+{len(cluster) - 3})"

        record = {
            "name": name[:100],
            "accessType": "OPEN",
            "description": f"phi's notes on: {', '.join(sorted(cluster))}"[:500],
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }

        try:
            coll_result = _create_pds_record(
                session, "network.cosmik.collection", record
            )
            coll_uri = coll_result["uri"]
            coll_cid = coll_result["cid"]

            # link cards to collection
            for card in cluster_cards:
                link_record = {
                    "collection": {"uri": coll_uri, "cid": coll_cid},
                    "card": {"uri": card["uri"], "cid": card["cid"]},
                    "addedBy": session["did"],
                    "addedAt": datetime.now(timezone.utc).isoformat(),
                }
                try:
                    _create_pds_record(
                        session, "network.cosmik.collectionLink", link_record
                    )
                except Exception:
                    pass

            created += 1
        except Exception:
            pass

    return created


# ---------------------------------------------------------------------------
# main flow
# ---------------------------------------------------------------------------


@flow(name="weave", log_prints=True)
async def weave():
    """Weave phi's memory graph: deduplicate tags, discover relationships,
    inject graph edges, and selectively promote to cosmik."""
    logger = get_run_logger()

    tpuf_key = (await Secret.load("turbopuffer-api-key")).get()
    openai_key = (await Secret.load("openai-api-key")).get()
    anthropic_key = (await Secret.load("anthropic-api-key")).get()

    # --- phase 1: collect and deduplicate tags ---
    tag_data = collect_all_tags(tpuf_key)
    tag_info: dict[str, dict] = tag_data["tag_info"]
    cooccurrences: dict[str, int] = tag_data["cooccurrences"]
    user_tag_sets: dict[str, list[str]] = tag_data["user_tag_sets"]

    tags = sorted(tag_info.keys())
    if not tags:
        print("no tags found, nothing to weave")
        return

    print(f"collected {len(tags)} unique tags across all namespaces")
    tag_embeddings = embed_tags(openai_key, tags)

    tags_text = "\n".join(tags)
    merge_dicts = await identify_tag_merges(
        tags_text, tag_info, tag_embeddings, anthropic_key
    )

    # collect all aliases for filtering in phase 2
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
    )
    print(f"phase 2: discovered {len(rel_dicts)} tag relationships")

    # --- phase 3: store in turbopuffer ---
    if rel_dicts:
        stored = store_tag_relationships(tpuf_key, openai_key, rel_dicts)
        print(f"phase 3: stored {stored} relationships in {TAG_REL_NAMESPACE}")
    else:
        print("phase 3: no relationships to store")

    # --- phase 4: cosmik promotion ---
    try:
        bsky_handle = (await Secret.load("atproto-handle")).get()
        bsky_password = (await Secret.load("atproto-password")).get()
    except Exception:
        print("phase 4: skipped — atproto secrets not configured")
        return

    if not rel_dicts:
        print("phase 4: no relationships to promote")
        return

    session = _create_bsky_session(bsky_handle, bsky_password)
    cards = _list_cosmik_cards(PHI_DID)
    existing_conns = _list_cosmik_connections(PHI_DID)
    print(f"phase 4: found {len(cards)} existing cards, {len(existing_conns)} connections")

    if not cards:
        print("phase 4: no cosmik cards exist yet — skipping promotion")
        return

    tag_cards = _match_cards_to_tags(cards, tag_info, openai_key)
    tags_with_cards = [t for t in tag_cards if tag_cards[t]]
    print(f"phase 4: matched cards to {len(tags_with_cards)} tags")

    conn_count = promote_connections(
        session, rel_dicts, tag_cards, tag_info, existing_conns
    )
    coll_count = promote_collections(session, rel_dicts, tag_cards)
    print(
        f"phase 4: promoted {conn_count} connections, {coll_count} collections to cosmik"
    )


if __name__ == "__main__":
    import asyncio

    asyncio.run(weave())
