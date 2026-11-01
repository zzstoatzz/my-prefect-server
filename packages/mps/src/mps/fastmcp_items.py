"""fastmcp issues and PRs, one row each, for the triage models in analytics/.

Fetched through GraphQL so one request carries 50 items with their comments
and reviews. Bots such as the triage reviewer carry contributor-level
`authorAssociation`, so a maintainer response means a human OWNER, MEMBER or
COLLABORATOR other than the item's author; the triage reviewer's first
review is tracked on its own.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from dataclasses import dataclass

import httpx

REPO_OWNER, REPO_NAME = "PrefectHQ", "fastmcp"
MAINTAINER_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
TRIAGE_REVIEWER = "fastmcp-triage-reviewer"
PAGE_SIZE = 50

_ACTIVITY = "authorAssociation createdAt author { login __typename }"
_FIELDS = f"""
  number title state createdAt updatedAt closedAt authorAssociation
  author {{ login __typename }}
  labels(first: 30) {{ nodes {{ name }} }}
  comments(first: 50) {{ totalCount nodes {{ {_ACTIVITY} }} }}
"""
QUERIES = {
    "issue": f"""
query($cursor: String) {{
  repository(owner: "{REPO_OWNER}", name: "{REPO_NAME}") {{
    items: issues(first: {PAGE_SIZE}, after: $cursor, orderBy: {{field: UPDATED_AT, direction: DESC}}) {{
      pageInfo {{ endCursor hasNextPage }}
      nodes {{ {_FIELDS} stateReason }}
    }}
  }}
}}""",
    "pr": f"""
query($cursor: String) {{
  repository(owner: "{REPO_OWNER}", name: "{REPO_NAME}") {{
    items: pullRequests(first: {PAGE_SIZE}, after: $cursor, orderBy: {{field: UPDATED_AT, direction: DESC}}) {{
      pageInfo {{ endCursor hasNextPage }}
      nodes {{ {_FIELDS} isDraft mergedAt headRefName reviews(first: 50) {{ nodes {{ {_ACTIVITY} }} }} }}
    }}
  }}
}}""",
}

COLUMNS = (
    "number",
    "kind",
    "title",
    "author",
    "author_association",
    "author_is_bot",
    "state",
    "state_reason",
    "is_draft",
    "head_ref",
    "labels",
    "created_at",
    "updated_at",
    "closed_at",
    "merged_at",
    "comment_count",
    "first_maintainer_response_at",
    "first_maintainer_responder",
    "last_maintainer_activity_at",
    "triage_review_at",
    "fetched_at",
)

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS raw_fastmcp_items (
    number INTEGER PRIMARY KEY,
    kind VARCHAR,
    title VARCHAR,
    author VARCHAR,
    author_association VARCHAR,
    author_is_bot BOOLEAN,
    state VARCHAR,
    state_reason VARCHAR,
    is_draft BOOLEAN,
    head_ref VARCHAR,
    labels VARCHAR[],
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    merged_at TIMESTAMPTZ,
    comment_count INTEGER,
    first_maintainer_response_at TIMESTAMPTZ,
    first_maintainer_responder VARCHAR,
    last_maintainer_activity_at TIMESTAMPTZ,
    triage_review_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ DEFAULT now()
)
"""


def _at(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _time(value: str | None) -> dt.datetime | None:
    return _at(value) if value else None


@dataclass
class FastmcpItem:
    number: int
    kind: str
    title: str
    author: str
    author_association: str
    author_is_bot: bool
    state: str
    state_reason: str | None
    is_draft: bool
    head_ref: str | None
    labels: list[str]
    created_at: dt.datetime
    updated_at: dt.datetime
    closed_at: dt.datetime | None
    merged_at: dt.datetime | None
    comment_count: int
    first_maintainer_response_at: dt.datetime | None = None
    first_maintainer_responder: str | None = None
    last_maintainer_activity_at: dt.datetime | None = None
    triage_review_at: dt.datetime | None = None

    @classmethod
    def from_node(cls, kind: str, node: dict) -> FastmcpItem:
        author = node.get("author") or {}
        login = author.get("login") or "ghost"
        activity = [*node["comments"]["nodes"], *(node.get("reviews") or {}).get("nodes", [])]
        maintainer = sorted(
            (_at(a["createdAt"]), (a.get("author") or {}).get("login"))
            for a in activity
            if a.get("authorAssociation") in MAINTAINER_ASSOCIATIONS
            and (a.get("author") or {}).get("__typename") == "User"
            and (a.get("author") or {}).get("login") != login
        )
        triage = sorted(
            _at(a["createdAt"])
            for a in (node.get("reviews") or {}).get("nodes", [])
            if (a.get("author") or {}).get("login") == TRIAGE_REVIEWER
        )
        return cls(
            number=node["number"],
            kind=kind,
            title=node.get("title") or "",
            author=login,
            author_association=node.get("authorAssociation") or "NONE",
            author_is_bot=author.get("__typename") == "Bot",
            state=node["state"],
            state_reason=node.get("stateReason"),
            is_draft=bool(node.get("isDraft")),
            head_ref=node.get("headRefName"),
            labels=[label["name"] for label in node["labels"]["nodes"]],
            created_at=_at(node["createdAt"]),
            updated_at=_at(node["updatedAt"]),
            closed_at=_time(node.get("closedAt")),
            merged_at=_time(node.get("mergedAt")),
            comment_count=node["comments"]["totalCount"],
            first_maintainer_response_at=maintainer[0][0] if maintainer else None,
            first_maintainer_responder=maintainer[0][1] if maintainer else None,
            last_maintainer_activity_at=maintainer[-1][0] if maintainer else None,
            triage_review_at=triage[0] if triage else None,
        )

    def row(self, fetched_at: dt.datetime) -> tuple:
        values = {**vars(self), "fetched_at": fetched_at}
        return tuple(values[c] for c in COLUMNS)


def fetch_updated(
    client: httpx.Client, kind: str, since: dt.datetime | None
) -> Iterator[FastmcpItem]:
    """Items of one kind updated at or after `since`, newest first; all of them when None."""
    cursor = None
    while True:
        resp = client.post(
            "https://api.github.com/graphql",
            json={"query": QUERIES[kind], "variables": {"cursor": cursor}},
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("errors"):
            raise RuntimeError(f"GitHub GraphQL errors: {body['errors']}")
        page = body["data"]["repository"]["items"]
        for node in page["nodes"]:
            item = FastmcpItem.from_node(kind, node)
            if since is not None and item.updated_at < since:
                return
            yield item
        if not page["pageInfo"]["hasNextPage"]:
            return
        cursor = page["pageInfo"]["endCursor"]
