from __future__ import annotations

import pytest

from flows.docket import _public_anchors_in_coarse


def _pt(pid, x, y, fine, coarse, layer="public-output", uri=None, label=""):
    return {
        "id": pid,
        "x": x,
        "y": y,
        "cluster_fine": fine,
        "cluster_coarse": coarse,
        "layer": layer,
        "kind": "post",
        "label": label or pid,
        "refs": {"at_uri": uri or f"at://example/{pid}"},
    }


@pytest.fixture
def atlas():
    """Two fine clusters far apart, both inside one coarse group — the shape
    that made every candidate share anchors when 93.5% of points were coarse 1."""
    return {
        "points": [
            # fine cluster 10, near the origin, with its own public neighbours
            _pt("private-a", 0.0, 0.0, 10, 1, layer="private-working"),
            _pt("near-1", 0.1, 0.1, 11, 1, label="mushroom foraging"),
            _pt("near-2", 0.2, 0.0, 11, 1, label="mycology notes"),
            # fine cluster 20, far away, with different public neighbours
            _pt("private-b", 9.0, 9.0, 20, 1, layer="private-working"),
            _pt("far-1", 9.1, 9.1, 21, 1, label="prefect deployment"),
            _pt("far-2", 9.2, 9.0, 21, 1, label="worker restarts"),
        ]
    }


def test_anchors_are_the_nearest_public_points(atlas) -> None:
    near = _public_anchors_in_coarse(atlas, 1, 2, cluster_fine=10)
    assert {a.snippet for a in near} == {"mushroom foraging", "mycology notes"}

    far = _public_anchors_in_coarse(atlas, 1, 2, cluster_fine=20)
    assert {a.snippet for a in far} == {"prefect deployment", "worker restarts"}


def test_respects_the_coarse_scope(atlas) -> None:
    assert _public_anchors_in_coarse(atlas, 99, 6, cluster_fine=10) == []


def test_without_a_fine_cluster_it_still_returns_anchors(atlas) -> None:
    """Callers that have no fine cluster (or a cluster with no placed members)
    should degrade to 'any public point in scope', not to an exception."""
    got = _public_anchors_in_coarse(atlas, 1, 6)
    assert len(got) == 4

    missing = _public_anchors_in_coarse(atlas, 1, 6, cluster_fine=12345)
    assert len(missing) == 4
