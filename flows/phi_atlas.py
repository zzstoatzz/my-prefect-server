"""phi-atlas — daily map of phi's mental landscape.

Enumerates every "object of phi's attention" across:
  - TurboPuffer (observations, summaries, interactions, episodic memory)
  - phi's PDS (goals, cosmik cards, blog docs, posts)
  - per-handle centroids over engaged users

Embeds the content via openai (text-embedding-3-small, cached by content hash),
reduces to 2D via UMAP, clusters at two granularities via HDBSCAN, labels the
clusters via haiku, computes deterministic `layer` + `promotion_status`
lifecycle metadata per point, and writes the result as a blob on phi's PDS
under `io.zzstoatzz.phi.atlas/self`.

Distinct from `flows/atlas.py`, which is the pub-search publication atlas.

Required env (injected by deployment job_variables.env, sourced from Secret
blocks at deploy time):
  - TURBOPUFFER_API_KEY  (block: turbopuffer-api-key)
  - OPENAI_API_KEY       (block: openai-api-key)
  - ANTHROPIC_API_KEY    (block: anthropic-api-key)
  - PHI_BSKY_HANDLE      (block: atproto-handle)
  - PHI_BSKY_PASSWORD    (block: atproto-password)
"""

import asyncio
import collections
import gc
import gzip
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import turbopuffer
from openai import OpenAI
from prefect import flow, get_run_logger, task
from prefect.blocks.system import Secret
from prefect.cache_policies import NONE
from prefect.exceptions import MissingContextError
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from mps.atproto import create_bsky_session
from mps.observability import configure_logfire
from mps.phi import clean_handle, restore_handle
from mps.spend import record_openai_embedding_response, record_pydantic_ai_result

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

PHI_DID = "did:plc:65sucjiel52gefhcdcypynsr"
PDS_BASE = "https://bsky.social"  # entryway routes to phi's actual PDS
USER_NS_PREFIX = "phi-users-"
EPISODIC_NS = "phi-episodic"

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536
EMBED_BATCH_SIZE = 128

# UMAP / HDBSCAN parameters — see docs/phi-atlas.md "open questions" for tuning rationale
UMAP_MIN_NEIGHBORS = 5
UMAP_MAX_NEIGHBORS = 30
COARSE_GROUPS = 8  # coarse groups agglomerated from fine centroids; see assign_clusters
HDBSCAN_FINE_MIN_CLUSTER = 5
NEIGHBOR_K = 5  # k for nearest-neighbor field per point

# kind → layer (per the plan's lifecycle taxonomy)
KIND_TO_LAYER: dict[str, str] = {
    "observation": "private-working",
    "summary": "private-working",
    "interaction": "private-working",
    "episodic": "private-working",
    "goal": "durable-intent",
    "note": "public-knowledge",
    "url": "public-knowledge",
    "post": "public-output",
    "blog": "public-output",
    "handle-engaged": "private-working",  # the centroid lives in working space
}


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


class AtlasPoint(BaseModel):
    """One point in phi's atlas."""

    id: str
    kind: str
    label: str
    # filled by reduce / cluster / lifecycle phases
    x: float = 0.0
    y: float = 0.0
    layer: str = ""
    promotion_status: str = "raw"
    cluster_coarse: int = -1
    cluster_fine: int = -1
    neighbor_ids: list[str] = Field(default_factory=list)
    refs: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    created_at: str = ""
    # intermediate state; pydantic v2 PrivateAttrs — excluded by model_dump
    _content: str = ""
    _vector: list[float] | None = None


class AtlasCluster(BaseModel):
    """One cluster summary."""

    id: int
    x: float
    y: float
    count: int
    label: str
    kind_counts: dict[str, int] = Field(default_factory=dict)
    parent_coarse: int | None = None  # only set on fine clusters


class Atlas(BaseModel):
    """The full artifact."""

    generated_at: str
    embedding_model: str
    reducer: str
    clusterer: str
    point_count: int
    clusters_coarse: list[AtlasCluster] = Field(default_factory=list)
    clusters_fine: list[AtlasCluster] = Field(default_factory=list)
    points: list[AtlasPoint] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# atproto helpers (mirror flows/curate.py)
# ---------------------------------------------------------------------------


def _list_records(did: str, collection: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"repo": did, "collection": collection, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        try:
            resp = httpx.get(
                f"{PDS_BASE}/xrpc/com.atproto.repo.listRecords",
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            # collection doesn't exist on this repo → empty list
            if e.response.status_code in (400, 404):
                return records
            raise
        data = resp.json()
        records.extend(data.get("records", []))
        cursor = data.get("cursor")
        if not cursor:
            break
    return records


# ---------------------------------------------------------------------------
# label extraction per kind (no LLM — just pull a useful snippet)
# ---------------------------------------------------------------------------


def _label_from_record(kind: str, value: dict[str, Any]) -> str:
    if kind == "goal":
        return (value.get("title") or value.get("description") or "")[:200]
    if kind == "post":
        return (value.get("text") or "")[:200]
    if kind == "blog":
        return (value.get("title") or "")[:200]
    if kind == "note":
        content = value.get("content") or {}
        return (content.get("text") or "")[:200]
    if kind == "url":
        content = value.get("content") or {}
        url = content.get("url") or ""
        title = (content.get("metadata") or {}).get("title") or ""
        return f"{title} — {url}" if title else url
    return ""


def _embed_text_for_record(kind: str, value: dict[str, Any]) -> str:
    """Text used to compute the embedding — usually richer than the label."""
    if kind == "goal":
        title = value.get("title") or ""
        desc = value.get("description") or ""
        return f"{title}\n{desc}".strip()
    if kind == "post":
        return value.get("text") or ""
    if kind == "blog":
        title = value.get("title") or ""
        body = (value.get("content") or "")[:1500]
        return f"{title}\n\n{body}".strip()
    if kind == "note":
        content = value.get("content") or {}
        return content.get("text") or ""
    if kind == "url":
        content = value.get("content") or {}
        url = content.get("url") or ""
        meta = content.get("metadata") or {}
        title = meta.get("title") or ""
        desc = meta.get("description") or ""
        return f"{title}\n{desc}\n{url}".strip()
    return ""


# ---------------------------------------------------------------------------
# Phase A — enumerate + fetch raw points (no embeddings yet)
# ---------------------------------------------------------------------------


@task
def fetch_tpuf_points(tpuf_key: str) -> list[AtlasPoint]:
    """Pull observations, summaries, interactions from every phi-users-* namespace
    plus the phi-episodic namespace, with their existing embeddings.

    TurboPuffer stores the same `text-embedding-3-small` vector we'd compute
    via openai anyway — reusing it saves embedding cost + cold-start latency.
    Include "vector" in include_attributes to get it back on each row.
    """
    logger = get_run_logger()
    client = turbopuffer.Turbopuffer(api_key=tpuf_key, region="gcp-us-central1")
    points: list[AtlasPoint] = []

    # per-user namespaces
    page = client.namespaces(prefix=USER_NS_PREFIX)
    ns_ids = [ns.id for ns in page.namespaces]
    logger.info(f"found {len(ns_ids)} phi-users-* namespaces")

    kind_map = {
        "observation": "observation",
        "summary": "summary",
        "interaction": "interaction",
    }

    attrs_with_vector = ["content", "tags", "created_at", "vector"]

    for ns_id in ns_ids:
        handle = restore_handle(ns_id)
        ns = client.namespace(ns_id)
        for tpuf_kind, point_kind in kind_map.items():
            try:
                resp = ns.query(
                    rank_by=("vector", "ANN", [0.5] * EMBEDDING_DIM),
                    top_k=500,
                    filters={"kind": ["Eq", tpuf_kind]},
                    include_attributes=attrs_with_vector,
                )
            except Exception as e:
                if "not found" not in str(e).lower():
                    logger.warning(f"query {ns_id}/{tpuf_kind} failed: {e}")
                continue
            for row in resp.rows or []:
                content = getattr(row, "content", "") or ""
                if not content:
                    continue
                pid = f"{point_kind}-{ns_id}-{row.id}"
                point = AtlasPoint(
                    id=pid,
                    kind=point_kind,
                    label=content[:200],
                    tags=getattr(row, "tags", []) or [],
                    created_at=getattr(row, "created_at", "") or "",
                    refs={
                        "handle": handle,
                        "tpuf_namespace": ns_id,
                        "tpuf_id": str(row.id),
                    },
                )
                point._content = content
                vec = getattr(row, "vector", None)
                if vec:
                    point._vector = list(vec)
                points.append(point)

    # phi-episodic
    try:
        ep_ns = client.namespace(EPISODIC_NS)
        resp = ep_ns.query(
            rank_by=("vector", "ANN", [0.5] * EMBEDDING_DIM),
            top_k=500,
            include_attributes=attrs_with_vector,
        )
        for row in resp.rows or []:
            content = getattr(row, "content", "") or ""
            if not content:
                continue
            point = AtlasPoint(
                id=f"episodic-{row.id}",
                kind="episodic",
                label=content[:200],
                tags=getattr(row, "tags", []) or [],
                created_at=getattr(row, "created_at", "") or "",
                refs={"tpuf_namespace": EPISODIC_NS, "tpuf_id": str(row.id)},
            )
            point._content = content
            vec = getattr(row, "vector", None)
            if vec:
                point._vector = list(vec)
            points.append(point)
    except Exception as e:
        if "not found" not in str(e).lower():
            logger.warning(f"episodic query failed: {e}")

    n_with_vec = sum(1 for p in points if p._vector is not None)
    logger.info(
        f"fetched {len(points)} points from turbopuffer ({n_with_vec} with vectors reused)"
    )
    return points


@task
def fetch_pds_points() -> list[AtlasPoint]:
    """Enumerate phi's PDS records: goals, cosmik cards, blog docs, and her own
    posts. Embeddings computed later.
    """
    logger = get_run_logger()
    points: list[AtlasPoint] = []

    # (collection, point_kind_resolver) tuples. resolver returns kind given record value.
    pds_kinds: list[tuple[str, Any]] = [
        ("io.zzstoatzz.phi.goal", lambda _v: "goal"),
        ("app.greengale.document", lambda _v: "blog"),
        ("app.bsky.feed.post", lambda _v: "post"),
        (
            "network.cosmik.card",
            lambda v: (
                "note"
                if v.get("type") == "NOTE"
                else ("url" if v.get("type") == "URL" else None)
            ),
        ),
    ]

    for collection, kind_fn in pds_kinds:
        try:
            records = _list_records(PHI_DID, collection)
        except Exception as e:
            logger.warning(f"list {collection} failed: {e}")
            continue
        for r in records:
            uri = r.get("uri", "")
            value = r.get("value") or {}
            kind = kind_fn(value)
            if not kind:
                continue
            label = _label_from_record(kind, value)
            embed_text = _embed_text_for_record(kind, value)
            if not embed_text.strip():
                continue
            pid = f"{kind}-{uri.rsplit('/', 1)[-1]}"
            point = AtlasPoint(
                id=pid,
                kind=kind,
                label=label,
                created_at=value.get("createdAt", "")
                or value.get("publishedAt", "")
                or "",
                refs={"at_uri": uri, "cid": r.get("cid", ""), "collection": collection},
            )
            point._content = embed_text
            points.append(point)

    logger.info(f"fetched {len(points)} points from PDS")
    return points


@task
def fetch_cosmik_connections() -> list[dict[str, Any]]:
    """Pull the cosmik connection graph. Used to mark `promotion_status=connected`
    on points whose AT URIs appear as source/target.
    """
    try:
        return _list_records(PHI_DID, "network.cosmik.connection")
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Phase B — embed everything via openai with per-content-hash cache
# ---------------------------------------------------------------------------


def _embed_cache_path() -> Path:
    base = Path(
        os.environ.get("PREFECT_LOCAL_STORAGE_PATH")
        or os.environ.get("ANALYTICS_DB_PATH", "/tmp").rsplit("/", 1)[0]
        or "/tmp"
    )
    p = base / "phi_atlas_embed_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _cached_embedding(content: str) -> list[float] | None:
    path = _embed_cache_path() / f"{_content_hash(content)}.json"
    if path.exists():
        try:
            data = json.loads(path.read_text())
            return data if isinstance(data, list) else None
        except Exception:
            return None
    return None


def _store_embedding(content: str, vector: list[float]) -> None:
    path = _embed_cache_path() / f"{_content_hash(content)}.json"
    path.write_text(json.dumps(vector))


@task
def embed_points(points: list[AtlasPoint], openai_key: str) -> list[AtlasPoint]:
    """Compute embeddings for points that don't already have one.

    Points fetched from turbopuffer arrive with their stored vector reused.
    PDS-sourced points (goals, cards, posts, blogs) have no embedding yet;
    those go to openai. A per-content-hash on-disk cache covers re-runs.
    """
    logger = get_run_logger()
    if not points:
        return points

    reused = 0
    pending: list[tuple[int, str]] = []  # (index, content)
    for i, p in enumerate(points):
        if p._vector is not None:
            reused += 1
            continue
        v = _cached_embedding(p._content)
        if v is not None:
            p._vector = v
        else:
            pending.append((i, p._content))

    logger.info(
        f"embeddings: {reused} reused from tpuf, "
        f"{len(points) - reused - len(pending)} from local cache, "
        f"{len(pending)} new openai calls needed"
    )
    if not pending:
        return points

    client = OpenAI(api_key=openai_key)
    for batch_start in range(0, len(pending), EMBED_BATCH_SIZE):
        batch = pending[batch_start : batch_start + EMBED_BATCH_SIZE]
        inputs = [c for _, c in batch]
        resp = client.embeddings.create(model=EMBEDDING_MODEL, input=inputs)
        record_openai_embedding_response(
            task_name="embed_points",
            model=EMBEDDING_MODEL,
            response=resp,
            item_count=len(inputs),
        )
        for (idx, content), data in zip(batch, resp.data):
            vec = list(data.embedding)
            points[idx]._vector = vec
            _store_embedding(content, vec)

    return points


@task
def compute_handle_centroids(points: list[AtlasPoint]) -> list[AtlasPoint]:
    """Add one `handle-engaged` point per handle, positioned at the centroid of
    its observation embeddings. Centroid points piggyback on the same UMAP pass.
    """
    obs_by_handle: dict[str, list[list[float]]] = {}
    for p in points:
        if p.kind != "observation":
            continue
        handle = p.refs.get("handle")
        if not handle or p._vector is None:
            continue
        obs_by_handle.setdefault(handle, []).append(p._vector)

    centroids: list[AtlasPoint] = []
    for handle, vecs in obs_by_handle.items():
        arr = np.array(vecs, dtype=np.float32)
        centroid = arr.mean(axis=0).tolist()
        cp = AtlasPoint(
            id=f"handle-{clean_handle(handle)}",
            kind="handle-engaged",
            label=f"@{handle}",
            refs={"handle": handle, "observation_count": len(vecs)},
        )
        cp._content = f"engaged handle: @{handle}"
        cp._vector = centroid
        centroids.append(cp)

    get_run_logger().info(f"computed {len(centroids)} handle centroids")
    return points + centroids


# ---------------------------------------------------------------------------
# Phase C — reduce to 2D, cluster, label
# ---------------------------------------------------------------------------


@task
def reduce_to_2d(points: list[AtlasPoint]) -> list[AtlasPoint]:
    """UMAP all embedded points down to (x, y). Mutates points in place."""
    import umap

    logger = get_run_logger()
    vectors = [p._vector for p in points if p._vector is not None]
    if not vectors:
        return points

    n = len(vectors)
    # n_neighbors scaling per docs/phi-atlas.md open question 5
    n_neighbors = int(max(UMAP_MIN_NEIGHBORS, min(UMAP_MAX_NEIGHBORS, np.sqrt(n))))
    logger.info(f"UMAP: n={n}, n_neighbors={n_neighbors}")

    reducer = umap.UMAP(
        n_components=2,
        n_neighbors=n_neighbors,
        min_dist=0.1,
        metric="cosine",
        random_state=42,
    )
    coords = reducer.fit_transform(np.array(vectors, dtype=np.float32))

    # write coords back, skipping points without vectors
    idx = 0
    for p in points:
        if p._vector is None:
            continue
        p.x = float(coords[idx, 0])
        p.y = float(coords[idx, 1])
        idx += 1
    return points


def assign_clusters(coords: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Fine labels from HDBSCAN, coarse groups agglomerated from fine centroids.

    Coarse used to be its own HDBSCAN pass over the same 2D coords, which
    collapsed as the atlas grew: by 2026-07-27 it returned two clusters, one
    holding 93.5% of 4,416 points under the label "audit trails and memory".
    That is not a tuning problem — at min_cluster_size 20, 40, 80 and 150 the
    answer is two, because UMAP puts almost everything in one dense blob and
    HDBSCAN's excess-of-mass selection then takes the root of the hierarchy.

    Clustering the fine centroids instead gives a balanced split (largest group
    ~22% rather than 93.5%) and a fixed number of groups, so the top level of
    the map stays legible as the atlas grows. It also makes the hierarchy real:
    every fine cluster now belongs to exactly one coarse group, where before
    the two passes were independent and `parent_coarse` was a majority vote
    over members that could disagree.

    Points HDBSCAN calls noise (~38%) keep a coarse group — nearest centroid —
    because the previous coarse pass assigned every point somewhere and the
    docket's anchor lookup depends on that.
    """
    import hdbscan
    from sklearn.cluster import AgglomerativeClustering

    fine = hdbscan.HDBSCAN(min_cluster_size=HDBSCAN_FINE_MIN_CLUSTER).fit(coords).labels_

    fine_ids = sorted({int(lbl) for lbl in fine if lbl != -1})
    if not fine_ids:
        return fine, np.zeros(len(coords), dtype=int)

    centroids = np.array([coords[fine == fid].mean(axis=0) for fid in fine_ids])
    k = min(COARSE_GROUPS, len(fine_ids))
    if k < 2:
        return fine, np.zeros(len(coords), dtype=int)

    groups = AgglomerativeClustering(n_clusters=k, linkage="ward").fit(centroids).labels_
    fine_to_coarse = {fid: int(g) for fid, g in zip(fine_ids, groups)}
    group_centroids = np.array(
        [centroids[groups == g].mean(axis=0) for g in range(k)]
    )

    coarse = np.empty(len(coords), dtype=int)
    for i, lbl in enumerate(fine):
        if lbl != -1:
            coarse[i] = fine_to_coarse[int(lbl)]
        else:
            coarse[i] = int(((group_centroids - coords[i]) ** 2).sum(axis=1).argmin())
    return fine, coarse


@task
def cluster_points(points: list[AtlasPoint]) -> list[AtlasPoint]:
    """Fine clusters via HDBSCAN on the 2D coords; coarse groups above them."""
    logger = get_run_logger()
    placed = [p for p in points if p._vector is not None]
    if not placed:
        return points

    coords = np.array([[p.x, p.y] for p in placed], dtype=np.float32)
    fine, coarse = assign_clusters(coords)

    for p, c, f in zip(placed, coarse, fine):
        p.cluster_coarse = int(c)
        p.cluster_fine = int(f)

    n_coarse = len(set(coarse.tolist()))
    n_fine = len(set(fine.tolist())) - (1 if -1 in fine else 0)
    largest = max(collections.Counter(coarse.tolist()).values()) / len(coarse)
    logger.info(
        f"clusters: {n_coarse} coarse (largest {largest:.1%} of points), {n_fine} fine"
    )
    return points


# cluster labeling — one LLM call per cluster

CLUSTER_LABEL_PROMPT = """you are labeling a cluster of phi's mental atlas points.
phi is a bluesky bot that reads, writes, observes, and curates atproto records.
each cluster groups together points that landed near each other in a semantic 2D map.

below are representative snippets from one cluster. respond with a short label
(2-5 lowercase words) that names the theme. concrete > abstract. examples:
"atproto relays", "memory architecture", "craft and ai", "rebuild-atlas debugging".
avoid generic labels like "thoughts" or "topics". reply with only the label.

cluster contents:
{contents}
"""


async def _label_cluster(snippets: list[str], agent: Agent[None, str]) -> str:
    sample = "\n".join(f"- {s[:160]}" for s in snippets[:12])
    try:
        result = await agent.run(CLUSTER_LABEL_PROMPT.format(contents=sample))
        record_pydantic_ai_result(
            task_name="label_cluster",
            model="claude-haiku-4-5",
            result=result,
        )
        return result.output.strip().lower()
    except Exception:
        return ""


@task(cache_policy=NONE)
async def label_clusters(
    points: list[AtlasPoint], anthropic_key: str
) -> tuple[dict[int, str], dict[int, str]]:
    """Generate human-readable labels for each cluster at both granularities.
    Returns (coarse_labels, fine_labels) keyed by cluster id.
    """
    logger = get_run_logger()
    model = AnthropicModel(
        "claude-haiku-4-5", provider=AnthropicProvider(api_key=anthropic_key)
    )
    agent: Agent[None, str] = Agent(
        model,
        system_prompt="you label semantic clusters with short, concrete themes.",
        name="phi-atlas-labeler",
        # ~110 haiku calls per atlas run (one per cluster) within a few
        # minutes; identical one-line system prompt. Caching is marginal
        # in absolute dollars (tiny prompt) but free to enable. 5m TTL
        # covers the burst.
        model_settings={"anthropic_cache_instructions": "5m"},
    )

    coarse_buckets: dict[int, list[str]] = {}
    fine_buckets: dict[int, list[str]] = {}
    for p in points:
        if p.cluster_coarse >= 0:
            coarse_buckets.setdefault(p.cluster_coarse, []).append(
                p._content or p.label
            )
        if p.cluster_fine >= 0:
            fine_buckets.setdefault(p.cluster_fine, []).append(p._content or p.label)

    coarse_labels: dict[int, str] = {
        cid: await _label_cluster(snips, agent) for cid, snips in coarse_buckets.items()
    }
    fine_labels: dict[int, str] = {
        cid: await _label_cluster(snips, agent) for cid, snips in fine_buckets.items()
    }

    logger.info(
        f"labeled {len(coarse_labels)} coarse + {len(fine_labels)} fine clusters"
    )
    return coarse_labels, fine_labels


# ---------------------------------------------------------------------------
# Phase D — lifecycle metadata and neighbors
# ---------------------------------------------------------------------------


@task
def compute_lifecycle_metadata(
    points: list[AtlasPoint],
    cosmik_connections: list[dict[str, Any]],
) -> list[AtlasPoint]:
    """Assign `layer` from kind, `promotion_status` from cluster composition
    + cosmik connection records. All deterministic, no LLM.

    promotion_status rules (in priority order, first match wins):
      - `connected`: this point's at_uri appears as source or target of a
        network.cosmik.connection record.
      - `promoted`: a private-working point shares its fine cluster with a
        public-knowledge or public-output point.
      - `summarized`: a private-working point shares a fine cluster with a
        `summary` point (already a synthesis).
      - `raw`: anything else (default).
    """
    # build the connection adjacency from cosmik
    connected_uris: set[str] = set()
    for conn in cosmik_connections:
        v = conn.get("value") or {}
        src = v.get("source") or ""
        tgt = v.get("target") or ""
        if src:
            connected_uris.add(src)
        if tgt:
            connected_uris.add(tgt)

    # group points by fine cluster. HDBSCAN labels noise points with -1 —
    # those are NOT in any real cluster, just "unclustered" together. We must
    # not treat -1 as a cluster for composition lookups, or every noise point
    # would inherit composition from every other noise point.
    by_fine: dict[int, list[AtlasPoint]] = {}
    for p in points:
        if p.cluster_fine < 0:
            continue
        by_fine.setdefault(p.cluster_fine, []).append(p)

    # compute per-cluster composition flags
    cluster_has_public: dict[int, bool] = {}
    cluster_has_summary: dict[int, bool] = {}
    for cid, members in by_fine.items():
        cluster_has_public[cid] = any(
            KIND_TO_LAYER.get(m.kind) in ("public-knowledge", "public-output")
            for m in members
        )
        cluster_has_summary[cid] = any(m.kind == "summary" for m in members)

    # assign layer + promotion_status
    for p in points:
        p.layer = KIND_TO_LAYER.get(p.kind, "")

        at_uri = p.refs.get("at_uri", "")
        if at_uri and at_uri in connected_uris:
            p.promotion_status = "connected"
            continue

        # public layers don't have a "promotion" — they ARE the promotion
        if p.layer in ("public-knowledge", "public-output", "durable-intent"):
            p.promotion_status = "promoted"
            continue

        # active-attention is a separate lifecycle stage; treat as "summarized"
        # since it represents phi actively chewing on something
        if p.layer == "active-attention":
            p.promotion_status = "summarized"
            continue

        # private-working: noise points (cluster -1) have no composition to
        # inherit from. Fall through to raw — the "no nearby public anchor"
        # signal, which is what raw means.
        if p.cluster_fine < 0:
            p.promotion_status = "raw"
        elif cluster_has_public.get(p.cluster_fine):
            p.promotion_status = "promoted"
        elif cluster_has_summary.get(p.cluster_fine):
            p.promotion_status = "summarized"
        else:
            p.promotion_status = "raw"

    return points


@task
def compute_neighbor_ids(points: list[AtlasPoint]) -> list[AtlasPoint]:
    """For each point, list the k=NEIGHBOR_K nearest others in 2D space."""
    from scipy.spatial import KDTree

    placed = [p for p in points if p._vector is not None]
    if len(placed) <= 1:
        return points

    coords = np.array([[p.x, p.y] for p in placed], dtype=np.float32)
    tree = KDTree(coords)
    k = min(NEIGHBOR_K + 1, len(placed))  # +1 because the point's own nearest is itself
    _, idxs = tree.query(coords, k=k)

    for i, p in enumerate(placed):
        neighbors: list[str] = []
        for j in idxs[i]:
            if j == i:
                continue
            neighbors.append(placed[int(j)].id)
            if len(neighbors) >= NEIGHBOR_K:
                break
        p.neighbor_ids = neighbors
    return points


# ---------------------------------------------------------------------------
# Phase E — assemble + upload + archive
# ---------------------------------------------------------------------------


def _cluster_summaries(
    points: list[AtlasPoint],
    cluster_attr: str,
    labels: dict[int, str],
    parent_attr: str | None = None,
) -> list[AtlasCluster]:
    """Roll up per-cluster centroids + counts + label."""
    buckets: dict[int, list[AtlasPoint]] = {}
    for p in points:
        cid = getattr(p, cluster_attr)
        if cid < 0:
            continue
        buckets.setdefault(cid, []).append(p)

    clusters: list[AtlasCluster] = []
    for cid, members in buckets.items():
        xs = np.array([m.x for m in members])
        ys = np.array([m.y for m in members])
        kc: dict[str, int] = {}
        for m in members:
            kc[m.kind] = kc.get(m.kind, 0) + 1
        parent = None
        if parent_attr:
            # take the mode of the parent cluster id among members
            counts: dict[int, int] = {}
            for m in members:
                pc = getattr(m, parent_attr)
                if pc < 0:
                    continue
                counts[pc] = counts.get(pc, 0) + 1
            if counts:
                parent = max(counts.items(), key=lambda kv: kv[1])[0]
        clusters.append(
            AtlasCluster(
                id=cid,
                x=float(xs.mean()),
                y=float(ys.mean()),
                count=len(members),
                label=labels.get(cid, ""),
                kind_counts=kc,
                parent_coarse=parent,
            )
        )
    return clusters


def _assemble_atlas(
    points: list[AtlasPoint],
    coarse_labels: dict[int, str],
    fine_labels: dict[int, str],
    point_count: int,
) -> Atlas:
    clusters_coarse = _cluster_summaries(points, "cluster_coarse", coarse_labels)
    clusters_fine = _cluster_summaries(
        points, "cluster_fine", fine_labels, parent_attr="cluster_coarse"
    )
    return Atlas(
        generated_at=datetime.now(UTC).isoformat(),
        embedding_model=EMBEDDING_MODEL,
        reducer="umap",
        clusterer="hdbscan",
        point_count=point_count,
        clusters_coarse=clusters_coarse,
        clusters_fine=clusters_fine,
        points=points,
    )


def _embedded_point_count(points: list[AtlasPoint]) -> int:
    return sum(1 for p in points if p._vector is not None)


def _drop_intermediate_payloads(points: list[AtlasPoint]) -> None:
    """Release vector/content payloads before JSON upload/archive."""
    for point in points:
        point._vector = None
        point._content = ""
    gc.collect()


def _atlas_to_json(atlas: Atlas) -> str:
    """Serialize atlas to JSON. Private fields on AtlasPoint (_content,
    _vector) are pydantic v2 PrivateAttrs and excluded by model_dump."""
    return json.dumps(atlas.model_dump(), indent=None, separators=(",", ":"))


@task
def upload_atlas_to_pds(
    atlas_bytes: bytes, point_count: int, handle: str, password: str
) -> dict[str, Any]:
    """Upload the atlas JSON as a blob, then putRecord pointing at it.

    Per the PDS JSON-blob bug (carried as project memory): upload with
    Content-Type application/octet-stream, NOT application/json. The bsky
    atproto-pds serializes the ReadStream object instead of the file
    contents when the stored mime is JSON. Consumers parse as JSON on read
    regardless of the stored mime type.
    """
    logger = get_run_logger()
    session = create_bsky_session(handle, password)
    headers = {"Authorization": f"Bearer {session['accessJwt']}"}

    # upload blob
    blob_resp = httpx.post(
        f"{PDS_BASE}/xrpc/com.atproto.repo.uploadBlob",
        headers={**headers, "Content-Type": "application/octet-stream"},
        content=atlas_bytes,
        timeout=60,
    )
    blob_resp.raise_for_status()
    blob_ref = blob_resp.json()["blob"]
    blob_cid = blob_ref.get("ref", {}).get("$link", "?")
    logger.info(f"uploaded atlas blob: {len(atlas_bytes)} bytes, cid={blob_cid}")

    # putRecord: io.zzstoatzz.phi.atlas/self
    record = {
        "generatedAt": datetime.now(UTC).isoformat(),
        "pointCount": point_count,
        "blob": blob_ref,
    }
    put_resp = httpx.post(
        f"{PDS_BASE}/xrpc/com.atproto.repo.putRecord",
        headers={**headers, "Content-Type": "application/json"},
        json={
            "repo": session["did"],
            "collection": "io.zzstoatzz.phi.atlas",
            "rkey": "self",
            "record": record,
        },
        timeout=15,
    )
    put_resp.raise_for_status()
    result = put_resp.json()
    logger.info(f"wrote io.zzstoatzz.phi.atlas/self: uri={result.get('uri')}")
    return result


@task
def archive_atlas_json(atlas_json: str, generated_at: str, point_count: int) -> str:
    """Archive the atlas JSON without loading DuckDB in this memory-hot pod."""
    archive_dir = (
        Path(os.environ.get("PREFECT_LOCAL_STORAGE_PATH", "/tmp")) / "phi-atlas-runs"
    )
    archive_dir.mkdir(parents=True, exist_ok=True)
    safe_generated_at = (
        generated_at.replace(":", "")
        .replace("+", "")
        .replace(".", "-")
        .replace("T", "_")
    )
    path = archive_dir / f"{safe_generated_at}_{point_count}.json.gz"
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp_path, "wt", encoding="utf-8") as handle:
        handle.write(atlas_json)
    os.replace(tmp_path, path)
    try:
        get_run_logger().info(f"archived atlas JSON to {path}")
    except MissingContextError:
        pass
    return str(path)


# ---------------------------------------------------------------------------
# main flow
# ---------------------------------------------------------------------------


@flow(name="phi-atlas", log_prints=True, timeout_seconds=3600)
async def phi_atlas(dry_run: bool = False) -> dict[str, int]:
    """Daily map of phi's mental landscape.

    Set dry_run=True to compute and print a summary without writing to PDS
    or DuckDB. Useful for local validation.
    """
    configure_logfire("prefect-flow-phi-atlas")
    logger = get_run_logger()

    tpuf_key = (await Secret.load("turbopuffer-api-key")).get()
    openai_key = (await Secret.load("openai-api-key")).get()
    anthropic_key = (await Secret.load("anthropic-api-key")).get()
    phi_handle = (await Secret.load("atproto-handle")).get()
    phi_password = (await Secret.load("atproto-password")).get()

    # phase A — gather raw points + the cosmik connection graph
    tpuf_points = fetch_tpuf_points.fn(tpuf_key)
    pds_points = fetch_pds_points.fn()
    connections = fetch_cosmik_connections.fn()
    points = tpuf_points + pds_points
    logger.info(
        f"total points: {len(points)} (tpuf={len(tpuf_points)}, pds={len(pds_points)}); "
        f"cosmik connections: {len(connections)}"
    )

    # phase B — embed + add handle centroids
    points = embed_points.fn(points, openai_key)
    points = compute_handle_centroids.fn(points)
    logger.info(f"points with vectors: {_embedded_point_count(points)}")

    # phase C — reduce + cluster + label
    points = reduce_to_2d.fn(points)
    points = cluster_points.fn(points)
    coarse_labels, fine_labels = await label_clusters.fn(points, anthropic_key)

    # phase D — lifecycle + neighbors
    points = compute_lifecycle_metadata.fn(points, connections)
    points = compute_neighbor_ids.fn(points)

    # phase E — assemble + upload
    point_count = _embedded_point_count(points)
    _drop_intermediate_payloads(points)
    atlas = _assemble_atlas(points, coarse_labels, fine_labels, point_count)
    atlas_json = _atlas_to_json(atlas)
    logger.info(
        f"atlas: {atlas.point_count} points, "
        f"{len(atlas.clusters_coarse)} coarse / {len(atlas.clusters_fine)} fine clusters, "
        f"{len(atlas_json.encode('utf-8')) / 1024:.0f} KB"
    )

    # promotion_status distribution — useful for at-a-glance health
    status_counts: dict[str, int] = {}
    for p in atlas.points:
        status_counts[p.promotion_status] = status_counts.get(p.promotion_status, 0) + 1
    logger.info(f"promotion_status distribution: {status_counts}")

    if dry_run:
        logger.info("dry-run: skipping PDS upload and DuckDB archive")
        return {"point_count": atlas.point_count, "dry_run": 1}

    atlas_bytes = atlas_json.encode("utf-8")
    upload_atlas_to_pds(atlas_bytes, atlas.point_count, phi_handle, phi_password)
    del atlas_bytes
    archive_atlas_json(atlas_json, atlas.generated_at, atlas.point_count)

    return {"point_count": atlas.point_count, "dry_run": 0}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(phi_atlas(dry_run=args.dry_run))
