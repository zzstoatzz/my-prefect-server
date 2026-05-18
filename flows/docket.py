"""phi-docket — daily promotion object.

The primitive is the docket: 5-15 work-item candidates per day, each naming
a piece of promotion pressure with evidence pointers and a suggested action.
Every other surface (DuckDB archive, [DOCKET] prompt block, /api/docket
endpoint, /docket cockpit page) is a projection of this object.

Pipeline:
  fetch_atlas          — read io.zzstoatzz.phi.atlas/self + blob
  extract_pressure     — filter to promotion_status='raw', group by fine cluster,
                         drop noise (-1) and below-density clusters
  synthesize_cluster   — one pydantic-ai call per qualifying cluster, claude-
                         sonnet-4-6, cached by cluster-content hash. The rubric
                         is strict: evidence first, anchors second, suggested
                         action third. Output is a DocketCandidate.
  upload_docket_to_pds — blob (octet-stream) + io.zzstoatzz.phi.docket/self
  archive_to_duckdb    — append-only history

Event-triggered on phi-atlas completion (prefect.yaml). No separate cron — the
docket exactly tracks the atlas it was derived from.
"""

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any

import duckdb
import httpx
from prefect import flow, get_run_logger, task
from prefect.blocks.system import Secret
from prefect.cache_policies import CachePolicy
from prefect.context import TaskRunContext
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

PHI_DID = "did:plc:65sucjiel52gefhcdcypynsr"
PDS_BASE = "https://bsky.social"  # entryway; routes to phi's actual PDS

ATLAS_COLLECTION = "io.zzstoatzz.phi.atlas"
ATLAS_RKEY = "self"
DOCKET_COLLECTION = "io.zzstoatzz.phi.docket"
DOCKET_RKEY = "self"

# Synthesis params
MIN_CLUSTER_DENSITY = 3       # require ≥N raw points in a fine cluster to synthesize
MAX_CANDIDATES = 15
SYNTHESIS_MODEL = "claude-sonnet-4-6"

# Cap on representative points handed to the LLM per cluster (token discipline)
MAX_EVIDENCE_PER_CLUSTER = 8
MAX_ANCHORS_PER_CLUSTER = 6


# ---------------------------------------------------------------------------
# models — the docket object itself
# ---------------------------------------------------------------------------


class EvidenceRef(BaseModel):
    """A pointer into phi's private memory that justifies a candidate.
    Always cites an atlas point id so consumers can drill in via inspect_atlas.
    """

    atlas_point_id: str
    kind: str  # observation | interaction | episodic | summary
    snippet: str = Field(default="", max_length=240)


class AnchorRef(BaseModel):
    """A pointer to an existing public record in the same neighborhood —
    the docket consumer checks these BEFORE proposing duplication.
    """

    at_uri: str
    kind: str  # note | url | post | blog | goal | active-observation
    snippet: str = Field(default="", max_length=240)


class DocketCandidate(BaseModel):
    """One work-item per piece of promotion pressure.

    Read as: 'There is private density here (private_evidence); the public
    state nearby looks like this (existing_public_anchors); the natural shape
    for promoting this would be {suggested_shape}; rationale.'
    """

    id: str  # "cand-{12-char hash of cluster content}"
    title: str = Field(max_length=140)
    rationale: str = Field(max_length=600)
    private_evidence: list[EvidenceRef] = Field(default_factory=list)
    existing_public_anchors: list[AnchorRef] = Field(default_factory=list)
    related_tags: list[str] = Field(default_factory=list)
    # knownValues-style, NOT a closed enum — extend by adding strings, no
    # lexicon-breaking change. See atproto style guide on enum avoidance.
    suggested_shape: str = "no-action"
    atlas_cluster_fine: int = -1
    atlas_cluster_coarse: int = -1


class Docket(BaseModel):
    """The daily promotion object. Canonical state lives in PDS; this is the
    in-process projection."""

    generated_at: str
    atlas_record_cid: str = ""
    atlas_point_count: int = 0
    candidates: list[DocketCandidate] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# atproto I/O (mirrors flows/phi_atlas.py + flows/curate.py patterns)
# ---------------------------------------------------------------------------


def _create_bsky_session(handle: str, password: str) -> dict[str, Any]:
    resp = httpx.post(
        f"{PDS_BASE}/xrpc/com.atproto.server.createSession",
        json={"identifier": handle, "password": password},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _get_record(repo: str, collection: str, rkey: str) -> dict[str, Any] | None:
    """Read a single record from a public PDS via the entryway. Returns
    a plain dict with uri/cid/value, or None if not found."""
    try:
        resp = httpx.get(
            f"{PDS_BASE}/xrpc/com.atproto.repo.getRecord",
            params={"repo": repo, "collection": collection, "rkey": rkey},
            timeout=15,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (400, 404):
            return None
        raise
    return resp.json()


def _get_blob(did: str, cid: str) -> bytes:
    """Fetch a blob by CID via the entryway. follow_redirects=True handles
    the 302 to the actual PDS host (carried project memory)."""
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        resp = client.get(
            f"{PDS_BASE}/xrpc/com.atproto.sync.getBlob",
            params={"did": did, "cid": cid},
        )
        resp.raise_for_status()
        return resp.content


# ---------------------------------------------------------------------------
# Phase A — fetch the atlas
# ---------------------------------------------------------------------------


@task
def fetch_atlas() -> dict[str, Any]:
    """Read the latest atlas off phi's PDS. Returns the parsed atlas dict
    plus the upstream record CID (used for cache invalidation downstream).
    """
    logger = get_run_logger()
    record = _get_record(PHI_DID, ATLAS_COLLECTION, ATLAS_RKEY)
    if record is None:
        raise RuntimeError(
            "no atlas record on PDS yet — phi-atlas flow must run first"
        )
    value = record.get("value") or {}
    blob = value.get("blob") or {}
    # blob ref shape: {"$type": "blob", "ref": {"$link": "bafy..."}, "mimeType": ..., "size": ...}
    blob_cid = ((blob.get("ref") or {}).get("$link")) or blob.get("cid")
    if not blob_cid:
        raise RuntimeError(f"atlas record has no blob ref: {value}")

    atlas_bytes = _get_blob(PHI_DID, blob_cid)
    atlas = json.loads(atlas_bytes)
    atlas["_record_cid"] = record.get("cid", "")
    logger.info(
        f"fetched atlas: {len(atlas.get('points') or [])} points, "
        f"record_cid={record.get('cid', '?')[:12]}..."
    )
    return atlas


# ---------------------------------------------------------------------------
# Phase B — extract the pressure pool, group by fine cluster
# ---------------------------------------------------------------------------


def _cluster_label(atlas: dict[str, Any], cluster_fine: int) -> str:
    """Look up the LLM-derived label for a fine cluster, if any."""
    for c in atlas.get("clusters_fine") or []:
        if c.get("id") == cluster_fine:
            return c.get("label") or ""
    return ""


def _public_anchors_in_coarse(
    atlas: dict[str, Any], cluster_coarse: int, max_anchors: int
) -> list[AnchorRef]:
    """Existing public records in the same coarse cluster (the surrounding
    public state the candidate would land near). Excludes the candidate's
    own fine cluster's raw points."""
    out: list[AnchorRef] = []
    for p in atlas.get("points") or []:
        if p.get("cluster_coarse") != cluster_coarse:
            continue
        layer = p.get("layer") or ""
        if layer not in ("public-knowledge", "public-output", "durable-intent"):
            continue
        refs = p.get("refs") or {}
        at_uri = refs.get("at_uri") or ""
        if not at_uri:
            continue
        out.append(
            AnchorRef(
                at_uri=at_uri,
                kind=p.get("kind") or "",
                snippet=(p.get("label") or "")[:240],
            )
        )
        if len(out) >= max_anchors:
            break
    return out


def _evidence_from_cluster(points: list[dict[str, Any]], max_n: int) -> list[EvidenceRef]:
    """Top-N evidence refs from a cluster, ordered by recency."""
    sorted_pts = sorted(
        points, key=lambda p: p.get("created_at") or "", reverse=True
    )
    return [
        EvidenceRef(
            atlas_point_id=p.get("id") or "",
            kind=p.get("kind") or "",
            snippet=(p.get("label") or "")[:240],
        )
        for p in sorted_pts[:max_n]
    ]


def _extract_pressure_pool_impl(atlas: dict[str, Any]) -> list[dict[str, Any]]:
    """Pure-Python pressure-pool extraction — testable without prefect context.

    See extract_pressure_pool for the wrapped-task docstring.
    """
    points = atlas.get("points") or []

    by_fine: dict[int, list[dict[str, Any]]] = {}
    for p in points:
        if p.get("promotion_status") != "raw":
            continue
        cf = p.get("cluster_fine")
        if cf is None or cf < 0:
            continue
        by_fine.setdefault(cf, []).append(p)

    clusters_out: list[dict[str, Any]] = []
    for cf, cluster_points in by_fine.items():
        if len(cluster_points) < MIN_CLUSTER_DENSITY:
            continue
        # coarse cluster is the mode across members (in practice all members
        # share one coarse cluster, but mode is the safe pick)
        coarse_counts: dict[int, int] = {}
        for p in cluster_points:
            cc = p.get("cluster_coarse")
            if cc is None or cc < 0:
                continue
            coarse_counts[cc] = coarse_counts.get(cc, 0) + 1
        coarse = max(coarse_counts.items(), key=lambda kv: kv[1])[0] if coarse_counts else -1

        # union of tags, deduped, ordered by frequency
        tag_counts: dict[str, int] = {}
        for p in cluster_points:
            for t in p.get("tags") or []:
                tag_counts[t] = tag_counts.get(t, 0) + 1
        tags = [t for t, _ in sorted(tag_counts.items(), key=lambda kv: -kv[1])]

        clusters_out.append(
            {
                "cluster_fine": cf,
                "cluster_coarse": coarse,
                "cluster_label": _cluster_label(atlas, cf),
                "points": cluster_points,
                "tags": tags,
                "anchors": _public_anchors_in_coarse(atlas, coarse, MAX_ANCHORS_PER_CLUSTER),
            }
        )

    # rank clusters by raw density (most pressure first) — capped at MAX_CANDIDATES
    clusters_out.sort(key=lambda c: -len(c["points"]))
    return clusters_out[:MAX_CANDIDATES]


@task
def extract_pressure_pool(atlas: dict[str, Any]) -> list[dict[str, Any]]:
    """Group raw private-working points by fine cluster, drop noise + low
    density. Returns a list of cluster-context dicts ready for synthesis.

    Each dict has:
      cluster_fine: int
      cluster_coarse: int
      cluster_label: str
      points: list[dict]    (the raw points in the cluster, full atlas shape)
      tags: list[str]       (union of tags across points, deduped)
      anchors: list[AnchorRef]  (existing public records in the same coarse cluster)
    """
    logger = get_run_logger()
    clusters_out = _extract_pressure_pool_impl(atlas)
    logger.info(
        f"pressure pool: {len(clusters_out)} qualifying clusters "
        f"(min_density={MIN_CLUSTER_DENSITY}, cap={MAX_CANDIDATES})"
    )
    return clusters_out


# ---------------------------------------------------------------------------
# Phase C — synthesize one candidate per qualifying cluster
# ---------------------------------------------------------------------------


SYNTHESIS_SYSTEM_PROMPT = """\
you synthesize one promotion-candidate per cluster of phi's private memory.

phi is a bluesky bot. her atlas surfaces clusters of private signals
(observations, interactions, episodic notes) that have no public anchor.
each cluster you're given is one such pocket of pressure.

your job: produce one DocketCandidate per call. read each cluster as a
work item, not as a horoscope. the rationale must be specific to THIS
cluster's evidence, not a generic observation about phi's interests.

rubric, in order of importance:
  1. evidence first — what specifically is in this cluster? cite the actual
     content. if you can't ground the candidate in 2-3 specific points,
     return suggested_shape="no-action".
  2. existing anchors second — does the candidate already have public state
     it would build on? if there's an existing card/blog/post that already
     covers this, suggest "connection" (link the private signals to it)
     rather than duplicating it as a new artifact.
  3. suggested action third — pick the most natural surface for what wants
     to come out:
       - card     a single observation worth saving publicly (network.cosmik.card NOTE)
       - url      a URL phi has been pointing at, worth bookmarking publicly
       - connection  link existing public records (e.g. a note ↔ a blog)
       - note     a small lowercase bluesky post
       - thread   a multi-post thread (when the cluster has internal structure)
       - doc      a long-form greengale blog post (when the cluster is dense
                  enough to sustain it)
       - no-action  the cluster is real but doesn't yet have a clear shape;
                    phi should reread it but not promote yet

title must be a single line, phi's voice, lowercase. rationale must be 1-3
sentences naming what specifically wants to come out and why now."""


class ClusterContext(BaseModel):
    """Input shape for the synthesis agent — kept tight so prompt size is bounded."""

    cluster_fine: int
    cluster_coarse: int
    cluster_label: str
    evidence: list[EvidenceRef]
    anchors: list[AnchorRef]
    tags: list[str]


class ByClusterContentHash(CachePolicy):
    """Cache one synthesis call per stable cluster content. If the cluster's
    evidence + anchors don't change, the candidate doesn't need to be
    regenerated. Mirrors flows/compact.py:ByObservationsHash."""

    def compute_key(
        self,
        task_ctx: TaskRunContext,
        inputs: dict[str, Any],
        flow_parameters: dict[str, Any],
        **kwargs: Any,
    ) -> str | None:
        ctx: ClusterContext | None = inputs.get("ctx")
        if ctx is None:
            return None
        signature = "|".join(
            [
                str(ctx.cluster_fine),
                str(ctx.cluster_coarse),
                ctx.cluster_label,
                ";".join(sorted(e.atlas_point_id for e in ctx.evidence)),
                ";".join(sorted(a.at_uri for a in ctx.anchors)),
                ";".join(sorted(ctx.tags)),
            ]
        )
        h = hashlib.md5(signature.encode()).hexdigest()[:12]
        return f"docket-synth/{h}"


def _candidate_id(ctx: ClusterContext) -> str:
    """Deterministic id from cluster content — stable across cache hits."""
    sig = f"{ctx.cluster_fine}|{ctx.cluster_coarse}|" + "|".join(
        sorted(e.atlas_point_id for e in ctx.evidence)
    )
    return f"cand-{hashlib.md5(sig.encode()).hexdigest()[:12]}"


@task(cache_policy=ByClusterContentHash())
async def synthesize_cluster(
    ctx: ClusterContext, anthropic_key: str
) -> DocketCandidate | None:
    """One LLM call → one DocketCandidate. None on synth failure."""
    logger = get_run_logger()
    model = AnthropicModel(
        SYNTHESIS_MODEL, provider=AnthropicProvider(api_key=anthropic_key)
    )
    agent: Agent[None, DocketCandidate] = Agent(
        model,
        system_prompt=SYNTHESIS_SYSTEM_PROMPT,
        output_type=DocketCandidate,
        name="phi-docket-synth",
        # The synth runs once per qualifying cluster per docket run — typically
        # ~12-15 calls within a few minutes. The SYNTHESIS_SYSTEM_PROMPT
        # (~800 tokens) is identical across them, so a cache write on the
        # first call → cache reads on the rest. 5m TTL is the right shape
        # here because the burst is bounded; cross-run reuse doesn't apply
        # (next docket fires after next atlas, hours later).
        model_settings={"anthropic_cache_instructions": "5m"},
    )

    prompt = _format_cluster_for_prompt(ctx)
    try:
        result = await agent.run(prompt)
    except Exception as e:
        logger.warning(f"synth failed for cluster {ctx.cluster_fine}: {e}")
        return None

    candidate = result.output
    # always overwrite id with our deterministic hash (the LLM might generate
    # a different one) and stamp the cluster ids (the LLM doesn't see them
    # as structured fields, only as labels in the prompt).
    candidate.id = _candidate_id(ctx)
    candidate.atlas_cluster_fine = ctx.cluster_fine
    candidate.atlas_cluster_coarse = ctx.cluster_coarse
    # ensure the candidate references the actual evidence/anchors we gave it
    # (avoid the LLM hallucinating atlas ids)
    candidate.private_evidence = ctx.evidence
    candidate.existing_public_anchors = ctx.anchors
    return candidate


def _format_cluster_for_prompt(ctx: ClusterContext) -> str:
    lines = [
        f"cluster_fine={ctx.cluster_fine}  cluster_coarse={ctx.cluster_coarse}",
        f"cluster_label: {ctx.cluster_label or '(unlabeled)'}",
        "",
        "private evidence (raw points in this cluster, most recent first):",
    ]
    for e in ctx.evidence:
        lines.append(f"- [{e.kind}] {e.atlas_point_id}: {e.snippet}")
    lines.append("")
    if ctx.anchors:
        lines.append("existing public anchors in the same coarse cluster:")
        for a in ctx.anchors:
            lines.append(f"- [{a.kind}] {a.at_uri}: {a.snippet}")
    else:
        lines.append("existing public anchors in the same coarse cluster: (none)")
    lines.append("")
    if ctx.tags:
        lines.append(f"tags: {', '.join(ctx.tags[:10])}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase D — upload + archive
# ---------------------------------------------------------------------------


def _docket_to_bytes(docket: Docket) -> bytes:
    return json.dumps(docket.model_dump(), separators=(",", ":")).encode("utf-8")


@task
def upload_docket_to_pds(
    docket: Docket, handle: str, password: str
) -> dict[str, Any]:
    """Blob upload + putRecord. Same pattern as phi_atlas.upload_atlas_to_pds.

    octet-stream content-type per the carried PDS bug — bsky atproto-pds
    serializes ReadStream objects when the stored mime is application/json.
    Consumers parse as JSON regardless.
    """
    logger = get_run_logger()
    session = _create_bsky_session(handle, password)
    headers = {"Authorization": f"Bearer {session['accessJwt']}"}
    docket_bytes = _docket_to_bytes(docket)

    blob_resp = httpx.post(
        f"{PDS_BASE}/xrpc/com.atproto.repo.uploadBlob",
        headers={**headers, "Content-Type": "application/octet-stream"},
        content=docket_bytes,
        timeout=60,
    )
    blob_resp.raise_for_status()
    blob_ref = blob_resp.json()["blob"]
    blob_cid = blob_ref.get("ref", {}).get("$link", "?")
    logger.info(
        f"uploaded docket blob: {len(docket_bytes)} bytes, cid={blob_cid}"
    )

    record = {
        "generatedAt": docket.generated_at,
        "candidateCount": len(docket.candidates),
        "atlasRecordCid": docket.atlas_record_cid,
        "blob": blob_ref,
    }
    put_resp = httpx.post(
        f"{PDS_BASE}/xrpc/com.atproto.repo.putRecord",
        headers={**headers, "Content-Type": "application/json"},
        json={
            "repo": session["did"],
            "collection": DOCKET_COLLECTION,
            "rkey": DOCKET_RKEY,
            "record": record,
        },
        timeout=15,
    )
    put_resp.raise_for_status()
    result = put_resp.json()
    logger.info(f"wrote {DOCKET_COLLECTION}/self: uri={result.get('uri')}")
    return result


def _db_path() -> str:
    return os.environ.get(
        "ANALYTICS_DB_PATH",
        os.environ.get("PREFECT_LOCAL_STORAGE_PATH", "/tmp") + "/analytics.duckdb",
    )


@task
def archive_to_duckdb(docket: Docket) -> None:
    """Append-only history projection. One row per generated docket."""
    db = duckdb.connect(_db_path())
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS raw_phi_dockets (
            generated_at TIMESTAMP,
            atlas_record_cid VARCHAR,
            candidate_count INTEGER,
            docket_json VARCHAR,
            fetched_at TIMESTAMP DEFAULT now()
        )
        """
    )
    db.execute(
        "INSERT INTO raw_phi_dockets "
        "(generated_at, atlas_record_cid, candidate_count, docket_json) "
        "VALUES (?, ?, ?, ?)",
        [
            datetime.now(UTC),
            docket.atlas_record_cid,
            len(docket.candidates),
            _docket_to_bytes(docket).decode("utf-8"),
        ],
    )
    db.close()


# ---------------------------------------------------------------------------
# main flow
# ---------------------------------------------------------------------------


@flow(name="docket", log_prints=True)
async def docket(dry_run: bool = False) -> dict[str, int]:
    """Establish today's promotion object.

    dry_run=True skips PDS write + DuckDB archive — useful for local
    validation against the live atlas.
    """
    logger = get_run_logger()

    anthropic_key = (await Secret.load("anthropic-api-key")).get()
    phi_handle = (await Secret.load("atproto-handle")).get()
    phi_password = (await Secret.load("atproto-password")).get()

    atlas = fetch_atlas()
    clusters = extract_pressure_pool(atlas)

    candidates: list[DocketCandidate] = []
    for c in clusters:
        ctx = ClusterContext(
            cluster_fine=c["cluster_fine"],
            cluster_coarse=c["cluster_coarse"],
            cluster_label=c["cluster_label"],
            evidence=_evidence_from_cluster(c["points"], MAX_EVIDENCE_PER_CLUSTER),
            anchors=c["anchors"],
            tags=c["tags"],
        )
        cand = await synthesize_cluster(ctx, anthropic_key)
        if cand:
            candidates.append(cand)

    # ensure we don't exceed the cap even with retries
    candidates = candidates[:MAX_CANDIDATES]

    docket_obj = Docket(
        generated_at=datetime.now(UTC).isoformat(),
        atlas_record_cid=atlas.get("_record_cid", ""),
        atlas_point_count=len(atlas.get("points") or []),
        candidates=candidates,
    )

    # at-a-glance distribution for the log
    shape_counts: dict[str, int] = {}
    for c in candidates:
        shape_counts[c.suggested_shape] = shape_counts.get(c.suggested_shape, 0) + 1
    logger.info(
        f"docket: {len(candidates)} candidates, "
        f"shapes={shape_counts}, atlas_cid={atlas.get('_record_cid', '?')[:12]}..."
    )

    if dry_run:
        logger.info("dry-run: skipping PDS upload + DuckDB archive")
        print(json.dumps(docket_obj.model_dump(), indent=2))
        return {"candidate_count": len(candidates), "dry_run": 1}

    upload_docket_to_pds(docket_obj, phi_handle, phi_password)
    archive_to_duckdb(docket_obj)

    return {"candidate_count": len(candidates), "dry_run": 0}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(docket(dry_run=args.dry_run))
