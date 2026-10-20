import pytest

from flows.agentic_publish_patch import references_issue


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
