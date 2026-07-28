from __future__ import annotations

import numpy as np
import pytest

from flows.phi_atlas import COARSE_GROUPS, assign_clusters


@pytest.fixture
def coords():
    """A map shaped like phi's real one: many small fine clusters packed into
    one dense region, plus scattered noise.

    The real atlas on 2026-07-27 had 4,416 points, 164 fine clusters and 38%
    HDBSCAN noise. A handful of clean well-separated Gaussians is the wrong
    fixture — it produces no noise and one enormous fine cluster, which is a
    map the coarse layer legitimately cannot subdivide.
    """
    rng = np.random.default_rng(0)
    parts = []
    # dense core: many small clusters crowded together (the "one blob" UMAP
    # region that the old coarse pass reduced to a single group)
    for _ in range(48):
        centre = rng.normal(loc=(0, 0), scale=2.0, size=2)
        parts.append(rng.normal(loc=centre, scale=0.12, size=(rng.integers(8, 40), 2)))
    # a few outlying groups
    for centre in [(10, 10), (-11, 9), (9, -10)]:
        parts.append(rng.normal(loc=centre, scale=0.3, size=(60, 2)))
    # uniform noise across the whole extent
    parts.append(rng.uniform(low=-12, high=12, size=(500, 2)))
    return np.vstack(parts).astype(np.float32)


def test_no_single_coarse_group_swallows_the_map(coords) -> None:
    # the bug: on 2026-07-27 the coarse pass returned 2 clusters, one holding
    # 93.5% of 4,416 points, labelled "audit trails and memory". At the top
    # level the map said "everything" and nothing else.
    _, coarse = assign_clusters(coords)
    counts = np.bincount(coarse)
    assert counts.max() / len(coords) < 0.6, (
        f"largest coarse group holds {counts.max() / len(coords):.1%} of points"
    )


def test_coarse_group_count_is_bounded_and_plural(coords) -> None:
    _, coarse = assign_clusters(coords)
    n = len(set(coarse.tolist()))
    assert 2 <= n <= COARSE_GROUPS


def test_every_fine_cluster_belongs_to_exactly_one_coarse_group(coords) -> None:
    """The old pass ran two independent HDBSCANs, so a fine cluster's members
    could disagree about their coarse parent and `parent_coarse` was a majority
    vote. Deriving coarse from fine makes the hierarchy real."""
    fine, coarse = assign_clusters(coords)
    for fid in {int(f) for f in fine if f != -1}:
        parents = {int(c) for c, f in zip(coarse, fine) if f == fid}
        assert len(parents) == 1, f"fine cluster {fid} spans coarse groups {parents}"


def test_noise_points_still_get_a_coarse_group(coords) -> None:
    """~38% of real points are HDBSCAN noise. The old coarse pass labelled
    every point, and the docket's anchor lookup filters on cluster_coarse — so
    dropping them would silently shrink the anchor pool."""
    fine, coarse = assign_clusters(coords)
    assert (fine == -1).sum() > 0, "fixture should produce some noise to be meaningful"
    assert all(c >= 0 for c in coarse)


def test_degenerate_input_does_not_raise(coords) -> None:
    """Too few points to form fine clusters — must return, not explode."""
    tiny = coords[:3]
    fine, coarse = assign_clusters(tiny)
    assert len(fine) == len(coarse) == 3
