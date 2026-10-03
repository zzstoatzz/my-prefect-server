"""The likes flow shares phi-users-* observation rows with the bot.

2026-10-03: an audit of active observations found 284 pairs within cosine
distance 0.25, and 276 of them were two rows this flow had written. It added
facts without looking at their vector neighbours and hard-deleted on UPDATE.
"""

from flows.compact import (
    ACTIVE_OBSERVATIONS,
    Reconciliation,
    plan_observation_write,
    reconciliation_prompt,
)

EXISTING = [
    {"id": "a", "content": "created chef.cee.wtf", "tags": ["tools"], "source_uris": ["at://1"]},
    {"id": "b", "content": "lives in chicago", "tags": ["location"], "source_uris": ["at://2"]},
    {"id": "c", "content": "built chef.cee.wtf", "tags": ["tools"], "source_uris": ["at://3"]},
]


def plan(decision: Reconciliation):
    return plan_observation_write(decision, EXISTING, "new fact", ["t"], ["at://new"])


def test_reconciler_is_shown_every_neighbour():
    prompt = reconciliation_prompt(EXISTING, "new fact", ["t"])
    for n, row in enumerate(EXISTING, start=1):
        assert f"EXISTING {n}: {row['content']}" in prompt
    assert prompt.endswith("NEW observation: new fact\nNEW tags: ['t']")


def test_superseded_rows_are_not_existing_knowledge():
    assert ("status", "NotEq", "superseded") in ACTIVE_OBSERVATIONS[1]


def test_noop_writes_nothing():
    assert plan(Reconciliation(action="NOOP", targets=[1])) == (None, [])


def test_add_supersedes_nothing():
    new, superseded = plan(Reconciliation(action="ADD"))
    assert superseded == []
    assert new is not None and new["supersedes"] == ""


def test_update_merges_every_target_and_unions_their_sources():
    new, superseded = plan(
        Reconciliation(action="update", targets=[3, 1], new_content="merged", new_tags=["m"])
    )
    assert superseded == ["c", "a"]
    assert new == {
        "content": "merged",
        "tags": ["m"],
        "source_uris": ["at://3", "at://1", "at://new"],
        "supersedes": "c",
    }


def test_delete_supersedes_a_farther_neighbour_and_keeps_its_own_sources():
    new, superseded = plan(Reconciliation(action="DELETE", targets=[2]))
    assert superseded == ["b"]
    assert new == {
        "content": "new fact",
        "tags": ["t"],
        "source_uris": ["at://new"],
        "supersedes": "b",
    }


def test_targets_that_were_never_offered_fall_back_to_the_nearest():
    _, superseded = plan(Reconciliation(action="DELETE", targets=[7, 0]))
    assert superseded == ["a"]
