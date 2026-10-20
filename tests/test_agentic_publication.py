import pytest

from flows.agentic_publish_patch import contribution_unchanged, references_issue


@pytest.mark.parametrize(
    "body",
    [
        '<a href="https://redirect.github.com/astral-sh/setup-uv/issues/603">#603</a>',
        "[#603](https://github.com/other/repository/issues/603)",
        "References other/repository#603",
        "Fixes #6030",
    ],
)
def test_foreign_or_different_issue_is_not_competing_work(body: str) -> None:
    assert not references_issue(body, 603)


@pytest.mark.parametrize(
    "body",
    [
        "Fixes #603",
        "References PrefectHQ/fastmcp#603",
        "[report](https://github.com/PrefectHQ/fastmcp/issues/603)",
        '<a href="https://github.com/PrefectHQ/fastmcp/issues/603">report</a>',
    ],
)
def test_current_repository_reference_blocks_duplicate_work(body: str) -> None:
    assert references_issue(body, 603)


@pytest.fixture
def prior_contribution():
    return {
        "number": 2143,
        "title": "Progress replacing asyncio with anyio",
        "body": "A partial migration. Relates to #2132.",
        "state": "closed",
        "draft": False,
        "merged": True,
        "updated_at": "2025-10-19T18:08:07Z",
        "head_sha": "a" * 40,
    }


def test_unchanged_prior_art_preserves_the_matcher_decision(prior_contribution):
    current = dict(prior_contribution, head={"sha": prior_contribution["head_sha"]})
    assert contribution_unchanged(current, prior_contribution)


@pytest.mark.parametrize(
    "change",
    [
        {"head": {"sha": "b" * 40}},
        {"body": "Now fixes the entire client lifecycle. Closes #2132."},
        {"state": "open"},
        {"updated_at": "2026-10-20T08:00:00Z"},
        {"merged": False},
        {"title": "Replace client session lifecycle"},
    ],
)
def test_changed_contribution_requires_another_review(prior_contribution, change):
    current = dict(prior_contribution, head={"sha": prior_contribution["head_sha"]})
    current.update(change)
    assert not contribution_unchanged(current, prior_contribution)
