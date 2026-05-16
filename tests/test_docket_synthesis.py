"""Regression tests for the docket flow's pure-Python extraction logic.

The synthesis step itself is an LLM call — not unit-tested here (covered
by the manual dry-run against the live atlas). What we DO test:

  - extract_pressure_pool filters to raw points
  - groups by cluster_fine
  - drops noise (cluster_fine < 0)
  - drops below-density clusters
  - caps total candidates at MAX_CANDIDATES
  - public anchor extraction picks public-layer points in the same coarse cluster
  - cluster ranking by density

These guarantees keep the docket "evidence-first work items" rather than
"horoscope over noise."

The pure-Python pieces live in `_extract_pressure_pool_impl` and
`_public_anchors_in_coarse` so tests can call them directly without
needing a prefect run context.
"""

from flows.docket import (
    MAX_CANDIDATES,
    MIN_CLUSTER_DENSITY,
    _extract_pressure_pool_impl,
    _public_anchors_in_coarse,
)


def _point(
    *,
    pid: str,
    kind: str,
    promotion_status: str,
    cluster_fine: int,
    cluster_coarse: int = 0,
    layer: str = "private-working",
    label: str = "",
    at_uri: str = "",
    created_at: str = "2026-05-14T00:00:00Z",
    tags: list[str] | None = None,
) -> dict:
    return {
        "id": pid,
        "kind": kind,
        "label": label or f"label for {pid}",
        "layer": layer,
        "promotion_status": promotion_status,
        "cluster_fine": cluster_fine,
        "cluster_coarse": cluster_coarse,
        "tags": tags or [],
        "refs": {"at_uri": at_uri} if at_uri else {},
        "created_at": created_at,
    }


def _atlas(points: list[dict], fine_labels: dict[int, str] | None = None) -> dict:
    labels = fine_labels or {}
    return {
        "generated_at": "2026-05-14T05:48:00Z",
        "points": points,
        "clusters_coarse": [],
        "clusters_fine": [
            {"id": cid, "label": label}
            for cid, label in labels.items()
        ],
    }


# ---------------------------------------------------------------------------
# extract_pressure_pool
# ---------------------------------------------------------------------------


def test_only_raw_points_included():
    """Promoted, summarized, connected points are NOT pressure — they have
    their resolution. Only raw points belong in the pool."""
    points = (
        [
            _point(pid=f"raw-{i}", kind="observation", promotion_status="raw", cluster_fine=1)
            for i in range(MIN_CLUSTER_DENSITY)
        ]
        + [_point(pid="p-1", kind="observation", promotion_status="promoted", cluster_fine=1)]
        + [_point(pid="s-1", kind="observation", promotion_status="summarized", cluster_fine=1)]
        + [_point(pid="c-1", kind="note", promotion_status="connected", cluster_fine=1, layer="public-knowledge")]
    )
    clusters = _extract_pressure_pool_impl(_atlas(points))
    assert len(clusters) == 1
    member_ids = {p["id"] for p in clusters[0]["points"]}
    assert all(pid.startswith("raw-") for pid in member_ids)


def test_noise_cluster_minus_one_excluded():
    """HDBSCAN labels noise as -1 — these have no real spatial relationship,
    must not be treated as a cluster."""
    points = [
        _point(pid=f"raw-{i}", kind="observation", promotion_status="raw", cluster_fine=-1)
        for i in range(10)
    ]
    clusters = _extract_pressure_pool_impl(_atlas(points))
    assert clusters == []


def test_below_density_clusters_dropped():
    """A cluster with too few raw points isn't pressure — it's a fluke."""
    sparse_points = [
        _point(pid=f"r-{i}", kind="observation", promotion_status="raw", cluster_fine=2)
        for i in range(MIN_CLUSTER_DENSITY - 1)
    ]
    clusters = _extract_pressure_pool_impl(_atlas(sparse_points))
    assert clusters == []


def test_at_density_threshold_included():
    """Exactly at the threshold: in."""
    points = [
        _point(pid=f"r-{i}", kind="observation", promotion_status="raw", cluster_fine=3)
        for i in range(MIN_CLUSTER_DENSITY)
    ]
    clusters = _extract_pressure_pool_impl(_atlas(points))
    assert len(clusters) == 1
    assert len(clusters[0]["points"]) == MIN_CLUSTER_DENSITY


def test_clusters_ranked_by_density():
    """Most-pressure clusters come first — caller takes top-N."""
    big = [
        _point(pid=f"big-{i}", kind="observation", promotion_status="raw", cluster_fine=10)
        for i in range(8)
    ]
    medium = [
        _point(pid=f"med-{i}", kind="observation", promotion_status="raw", cluster_fine=20)
        for i in range(5)
    ]
    small = [
        _point(pid=f"small-{i}", kind="observation", promotion_status="raw", cluster_fine=30)
        for i in range(MIN_CLUSTER_DENSITY)
    ]
    clusters = _extract_pressure_pool_impl(_atlas(big + medium + small))
    cluster_ids = [c["cluster_fine"] for c in clusters]
    assert cluster_ids == [10, 20, 30]


def test_candidate_cap_respected():
    """Even if there are more qualifying clusters than MAX_CANDIDATES,
    only the top MAX_CANDIDATES are returned."""
    points = []
    n_clusters = MAX_CANDIDATES + 5
    for cid in range(100, 100 + n_clusters):
        for i in range(MIN_CLUSTER_DENSITY):
            points.append(
                _point(
                    pid=f"c{cid}-{i}",
                    kind="observation",
                    promotion_status="raw",
                    cluster_fine=cid,
                )
            )
    clusters = _extract_pressure_pool_impl(_atlas(points))
    assert len(clusters) == MAX_CANDIDATES


def test_cluster_label_pulled_from_atlas():
    points = [
        _point(pid=f"r-{i}", kind="observation", promotion_status="raw", cluster_fine=42)
        for i in range(MIN_CLUSTER_DENSITY)
    ]
    atlas = _atlas(points, fine_labels={42: "memory and identity"})
    clusters = _extract_pressure_pool_impl(atlas)
    assert clusters[0]["cluster_label"] == "memory and identity"


def test_tags_merged_and_ordered_by_frequency():
    points = [
        _point(pid="a", kind="observation", promotion_status="raw", cluster_fine=1, tags=["memory", "atproto"]),
        _point(pid="b", kind="observation", promotion_status="raw", cluster_fine=1, tags=["memory"]),
        _point(pid="c", kind="observation", promotion_status="raw", cluster_fine=1, tags=["memory", "ai"]),
    ]
    clusters = _extract_pressure_pool_impl(_atlas(points))
    tags = clusters[0]["tags"]
    # 'memory' appears 3 times, 'atproto' and 'ai' once each
    assert tags[0] == "memory"
    assert set(tags[1:]) == {"atproto", "ai"}


# ---------------------------------------------------------------------------
# _public_anchors_in_coarse — surrounding public state for the LLM
# ---------------------------------------------------------------------------


def test_anchors_in_same_coarse_only():
    """Anchors come from the SAME coarse cluster as the candidate — that's
    'nearby public state.'"""
    points = [
        _point(
            pid="card-near",
            kind="note",
            promotion_status="promoted",
            cluster_fine=99,
            cluster_coarse=5,
            layer="public-knowledge",
            at_uri="at://x/network.cosmik.card/near",
        ),
        _point(
            pid="card-far",
            kind="note",
            promotion_status="promoted",
            cluster_fine=98,
            cluster_coarse=99,
            layer="public-knowledge",
            at_uri="at://x/network.cosmik.card/far",
        ),
    ]
    anchors = _public_anchors_in_coarse(_atlas(points), cluster_coarse=5, max_anchors=10)
    uris = [a.at_uri for a in anchors]
    assert "at://x/network.cosmik.card/near" in uris
    assert "at://x/network.cosmik.card/far" not in uris


def test_anchors_only_public_layers():
    """Private-working points in the same coarse cluster are NOT anchors —
    they're MORE PRESSURE, not resolution."""
    points = [
        _point(
            pid="obs-private",
            kind="observation",
            promotion_status="raw",
            cluster_fine=1,
            cluster_coarse=7,
            layer="private-working",
        ),
        _point(
            pid="blog-public",
            kind="blog",
            promotion_status="promoted",
            cluster_fine=2,
            cluster_coarse=7,
            layer="public-output",
            at_uri="at://x/app.greengale.document/post",
        ),
    ]
    anchors = _public_anchors_in_coarse(_atlas(points), cluster_coarse=7, max_anchors=10)
    assert [a.kind for a in anchors] == ["blog"]


def test_anchors_capped_at_max():
    points = [
        _point(
            pid=f"card-{i}",
            kind="note",
            promotion_status="promoted",
            cluster_fine=99,
            cluster_coarse=3,
            layer="public-knowledge",
            at_uri=f"at://x/network.cosmik.card/{i}",
        )
        for i in range(20)
    ]
    anchors = _public_anchors_in_coarse(_atlas(points), cluster_coarse=3, max_anchors=4)
    assert len(anchors) == 4


def test_anchors_skip_points_without_at_uri():
    """Public-layer points without an at_uri can't be linked — they're
    not useful as anchors. Skip them."""
    points = [
        _point(
            pid="post-no-uri",
            kind="post",
            promotion_status="promoted",
            cluster_fine=1,
            cluster_coarse=1,
            layer="public-output",
            at_uri="",  # no uri
        ),
        _point(
            pid="post-with-uri",
            kind="post",
            promotion_status="promoted",
            cluster_fine=1,
            cluster_coarse=1,
            layer="public-output",
            at_uri="at://x/app.bsky.feed.post/abc",
        ),
    ]
    anchors = _public_anchors_in_coarse(_atlas(points), cluster_coarse=1, max_anchors=10)
    assert [a.at_uri for a in anchors] == ["at://x/app.bsky.feed.post/abc"]
