"""Regression tests for the phi-atlas lifecycle classifier + cluster summaries.

The classifier (`compute_lifecycle_metadata`) is the part of the atlas that
downstream consumers (docket, cockpit, future phi tools) will read off most
directly: it answers "what stage of commitment is this point at?" without any
LLM in the path. Drift here would silently mislabel everything.

The priority rules for promotion_status, in order:
  1. connected   — point's at_uri is source or target of a cosmik connection
  2. promoted    — point is itself in a public-knowledge/public-output layer,
                   OR shares a fine cluster with one
  3. summarized  — point is active-attention, OR shares a fine cluster with
                   a `summary` point
  4. raw         — everything else (including noise points with no real cluster)

These tests call `.fn(...)` on the prefect tasks to skip the prefect machinery
— no harness needed, runs in milliseconds.
"""

from flows.phi_atlas import (
    AtlasPoint,
    _cluster_summaries,
    compute_lifecycle_metadata,
)


def _point(
    pid: str,
    kind: str,
    *,
    cluster_fine: int = 0,
    cluster_coarse: int = 0,
    at_uri: str = "",
) -> AtlasPoint:
    return AtlasPoint(
        id=pid,
        kind=kind,
        label=pid,
        cluster_fine=cluster_fine,
        cluster_coarse=cluster_coarse,
        refs={"at_uri": at_uri} if at_uri else {},
    )


# ---------------------------------------------------------------------------
# lifecycle classifier
# ---------------------------------------------------------------------------


def test_layer_assignment_from_kind():
    """Each kind maps to exactly one layer, deterministically."""
    cases = [
        ("observation", "private-working"),
        ("summary", "private-working"),
        ("interaction", "private-working"),
        ("episodic", "private-working"),
        ("goal", "durable-intent"),
        ("note", "public-knowledge"),
        ("url", "public-knowledge"),
        ("post", "public-output"),
        ("blog", "public-output"),
        ("handle-engaged", "private-working"),
    ]
    points = [_point(f"x-{kind}", kind) for kind, _ in cases]
    compute_lifecycle_metadata.fn(points, [])
    for (kind, expected_layer), p in zip(cases, points, strict=True):
        assert p.layer == expected_layer, f"{kind} → {p.layer} (expected {expected_layer})"


def test_connected_outranks_promoted():
    """If a point's at_uri appears in a cosmik connection, status is
    `connected` even if the point is in a public layer that would
    otherwise be `promoted`."""
    cited = _point(
        "card-x",
        "note",
        cluster_fine=1,
        at_uri="at://did:plc:abc/network.cosmik.card/x",
    )
    connections = [
        {
            "value": {
                "source": "at://did:plc:abc/network.cosmik.card/x",
                "target": "at://did:plc:abc/network.cosmik.card/y",
            }
        }
    ]
    compute_lifecycle_metadata.fn([cited], connections)
    assert cited.promotion_status == "connected"


def test_public_layers_default_to_promoted():
    """note/url/post/blog/goal points are themselves public commitments —
    they're 'promoted' (the act of writing them was the promotion)."""
    kinds = ["note", "url", "post", "blog", "goal"]
    points = [_point(f"x-{k}", k) for k in kinds]
    compute_lifecycle_metadata.fn(points, [])
    for p in points:
        assert p.promotion_status == "promoted", f"{p.kind} → {p.promotion_status}"


def test_private_working_promoted_when_clustered_with_public():
    """An observation in the same fine cluster as a cosmik card has a
    public anchor nearby — phi has committed something on this theme."""
    obs = _point("obs-1", "observation", cluster_fine=5)
    card = _point("card-1", "note", cluster_fine=5)
    compute_lifecycle_metadata.fn([obs, card], [])
    assert obs.promotion_status == "promoted"


def test_private_working_summarized_when_clustered_with_summary():
    """Observation in same cluster as a compact-generated summary —
    phi has at least synthesized it privately, even if not committed publicly."""
    obs = _point("obs-1", "observation", cluster_fine=7)
    summary = _point("sum-1", "summary", cluster_fine=7)
    compute_lifecycle_metadata.fn([obs, summary], [])
    assert obs.promotion_status == "summarized"


def test_private_working_raw_when_alone():
    """A private observation with no nearby public anchor and no summary
    in its cluster is `raw` — the promotion-pressure candidate."""
    obs = _point("obs-1", "observation", cluster_fine=9)
    ix = _point("ix-1", "interaction", cluster_fine=9)
    compute_lifecycle_metadata.fn([obs, ix], [])
    assert obs.promotion_status == "raw"
    assert ix.promotion_status == "raw"


def test_promoted_outranks_summarized():
    """Both public AND summary in cluster → public wins (the stronger signal)."""
    obs = _point("obs-1", "observation", cluster_fine=3)
    summary = _point("sum-1", "summary", cluster_fine=3)
    card = _point("card-1", "note", cluster_fine=3)
    compute_lifecycle_metadata.fn([obs, summary, card], [])
    assert obs.promotion_status == "promoted"


def test_noise_cluster_minus_one_falls_to_raw():
    """HDBSCAN labels noise points as cluster_fine=-1. They share no real
    cluster with anything — observation-in-noise next to card-in-noise is
    NOT clustered, just both unclustered. The classifier must not inherit
    composition across noise points."""
    obs = _point("obs-1", "observation", cluster_fine=-1)
    card = _point("card-1", "note", cluster_fine=-1)
    compute_lifecycle_metadata.fn([obs, card], [])
    assert obs.promotion_status == "raw"
    # the card is itself public-knowledge → still promoted regardless of cluster
    assert card.promotion_status == "promoted"


# ---------------------------------------------------------------------------
# cluster summaries
# ---------------------------------------------------------------------------


def test_cluster_summary_kind_counts():
    points = [
        _point("a", "observation", cluster_fine=1),
        _point("b", "observation", cluster_fine=1),
        _point("c", "note", cluster_fine=1),
    ]
    for p in points:
        p.x = 1.0
        p.y = 2.0
    clusters = _cluster_summaries(points, "cluster_fine", {1: "label"})
    assert len(clusters) == 1
    c = clusters[0]
    assert c.id == 1
    assert c.count == 3
    assert c.kind_counts == {"observation": 2, "note": 1}
    assert c.label == "label"


def test_cluster_summary_excludes_noise():
    """Points with cluster=-1 (HDBSCAN noise) don't form a cluster."""
    points = [
        _point("a", "observation", cluster_fine=-1),
        _point("b", "observation", cluster_fine=2),
    ]
    for p in points:
        p.x = 0.0
        p.y = 0.0
    clusters = _cluster_summaries(points, "cluster_fine", {2: "c2"})
    assert {c.id for c in clusters} == {2}


def test_cluster_summary_parent_coarse_mode():
    """Fine cluster's parent_coarse is the mode (most-common) coarse id
    among its members. Most members of a fine cluster should land in the
    same coarse cluster, but on the boundary there can be disagreement."""
    points = [
        _point("a", "observation", cluster_fine=1, cluster_coarse=10),
        _point("b", "observation", cluster_fine=1, cluster_coarse=10),
        _point("c", "observation", cluster_fine=1, cluster_coarse=20),
    ]
    for p in points:
        p.x = 0.0
        p.y = 0.0
    clusters = _cluster_summaries(points, "cluster_fine", {1: ""}, parent_attr="cluster_coarse")
    assert clusters[0].parent_coarse == 10


def test_cluster_summary_centroid_is_member_mean():
    """x/y are the mean over members' coords."""
    points = [_point(f"p{i}", "observation", cluster_fine=1) for i in range(3)]
    points[0].x, points[0].y = 0.0, 0.0
    points[1].x, points[1].y = 2.0, 0.0
    points[2].x, points[2].y = 1.0, 3.0
    clusters = _cluster_summaries(points, "cluster_fine", {1: ""})
    assert clusters[0].x == 1.0  # (0 + 2 + 1) / 3
    assert clusters[0].y == 1.0  # (0 + 0 + 3) / 3
