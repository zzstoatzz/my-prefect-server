"""GitHub API helpers and shared models."""

from typing import Literal

from pydantic import BaseModel


class IssueRef(BaseModel):
    """Parsed from a GitHub notification — what we need to fetch."""
    repo: str
    number: int
    subject_type: Literal["Issue", "PullRequest"]


class IssueOrPR(BaseModel):
    """Fetched issue or PR data — persisted as JSON result."""
    repo: str
    number: int
    type: str
    title: str | None = None
    state: str | None = None
    body: str = ""
    url: str | None = None
    labels: list[str] = []
    created_at: str | None = None
    updated_at: str | None = None
    user: str | None = None
    comments: int = 0
    reactions_total: int = 0


def gh_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
