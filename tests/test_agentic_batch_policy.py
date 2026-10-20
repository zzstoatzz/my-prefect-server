import pytest

from flows.agentic_batch_policy import batch_branch


def policy():
    return {
        "id": "bounded-test",
        "started_at": 100,
        "expires_at": 200,
        "max_prs": 3,
        "issue_numbers": [1, 2, 3],
    }


def test_only_named_batch_branch_is_authorized():
    assert batch_branch(policy(), 2, 2, now=150) == "agentic/bounded-test/issue-2"


@pytest.mark.parametrize(
    "change",
    [
        {"expires_at": 150},
        {"expires_at": 7301},
        {"started_at": 151},
        {"max_prs": 4},
        {"max_prs": True},
        {"issue_numbers": [1]},
        {"issue_numbers": list(range(1, 14))},
        {"issue_numbers": [2, 2]},
        {"id": "../main"},
        {"id": "main/release"},
    ],
)
def test_invalid_or_expired_authority_blocks_publication(change):
    with pytest.raises(ValueError):
        batch_branch(policy() | change, 2, now=150)


def test_quota_counts_closed_drafts_and_stops_at_target():
    with pytest.raises(ValueError, match="limit reached"):
        batch_branch(policy(), 2, 3, now=150)


def test_unbounded_legacy_request_cannot_publish():
    with pytest.raises(ValueError):
        batch_branch({}, 2, now=150)
