import datetime as dt
from unittest import mock

import duckdb
import httpx
from mps.db import fastmcp_watermark, write_fastmcp_items
from mps.fastmcp_items import FastmcpItem, fetch_updated


def activity(login, association, at, typename="User"):
    return {
        "authorAssociation": association,
        "createdAt": at,
        "author": {"login": login, "__typename": typename},
    }


def pr_node(number=1, updated="2026-11-01T12:00:00Z", comments=(), reviews=()):
    return {
        "number": number,
        "title": "fix a thing",
        "state": "OPEN",
        "createdAt": "2026-10-01T00:00:00Z",
        "updatedAt": updated,
        "closedAt": None,
        "mergedAt": None,
        "isDraft": False,
        "headRefName": "fix/thing",
        "authorAssociation": "FIRST_TIME_CONTRIBUTOR",
        "author": {"login": "newcomer", "__typename": "User"},
        "labels": {"nodes": [{"name": "bug"}]},
        "comments": {"totalCount": len(comments), "nodes": list(comments)},
        "reviews": {"nodes": list(reviews)},
    }


def test_maintainer_response_ignores_bots_the_author_and_non_maintainers():
    item = FastmcpItem.from_node(
        "pr",
        pr_node(
            comments=[
                activity("chatgpt-codex-connector", "CONTRIBUTOR", "2026-10-01T00:05:00Z", "Bot"),
                activity("newcomer", "FIRST_TIME_CONTRIBUTOR", "2026-10-01T01:00:00Z"),
                activity("bystander", "NONE", "2026-10-01T02:00:00Z"),
                activity("zzstoatzz", "MEMBER", "2026-10-03T00:00:00Z"),
            ],
            reviews=[
                activity("fastmcp-triage-reviewer", "CONTRIBUTOR", "2026-10-01T00:30:00Z", "Bot"),
                activity("jlowin", "OWNER", "2026-10-02T00:00:00Z"),
            ],
        ),
    )
    assert item.first_maintainer_responder == "jlowin"
    assert item.first_maintainer_response_at == dt.datetime(2026, 10, 2, tzinfo=dt.UTC)
    assert item.last_maintainer_activity_at == dt.datetime(2026, 10, 3, tzinfo=dt.UTC)
    assert item.triage_review_at == dt.datetime(2026, 10, 1, 0, 30, tzinfo=dt.UTC)


def test_a_maintainer_replying_on_their_own_pr_is_not_a_response():
    node = pr_node(comments=[activity("zzstoatzz", "MEMBER", "2026-10-02T00:00:00Z")])
    node["author"] = {"login": "zzstoatzz", "__typename": "User"}
    node["authorAssociation"] = "MEMBER"
    assert FastmcpItem.from_node("pr", node).first_maintainer_response_at is None


def test_incremental_fetch_stops_at_the_watermark():
    pages = [
        [pr_node(3, "2026-11-01T03:00:00Z"), pr_node(2, "2026-11-01T02:00:00Z")],
        [pr_node(1, "2026-10-31T00:00:00Z")],
    ]
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        nodes = pages[len(calls) - 1]
        return httpx.Response(
            200,
            json={
                "data": {
                    "repository": {
                        "items": {
                            "pageInfo": {"endCursor": "c", "hasNextPage": len(calls) < 2},
                            "nodes": nodes,
                        }
                    }
                }
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        since = dt.datetime(2026, 11, 1, 1, tzinfo=dt.UTC)
        assert [i.number for i in fetch_updated(client, "pr", since)] == [3, 2]
        calls.clear()
        assert [i.number for i in fetch_updated(client, "pr", None)] == [3, 2, 1]


def test_items_round_trip_through_the_warehouse(tmp_path, monkeypatch):
    monkeypatch.delenv("PREFECT_API_URL", raising=False)
    db = str(tmp_path / "analytics.duckdb")
    items = [FastmcpItem.from_node("pr", pr_node(n, f"2026-11-01T0{n}:00:00Z")) for n in (1, 2)]
    with mock.patch("prefect.settings.PREFECT_API_URL") as setting:
        setting.value.return_value = None
        assert fastmcp_watermark(db) is None
        assert write_fastmcp_items(items, db) == 2
        assert write_fastmcp_items(items, db) == 2
    assert fastmcp_watermark(db) == dt.datetime(2026, 11, 1, 2, tzinfo=dt.UTC)
    with duckdb.connect(db, read_only=True) as con:
        assert con.execute("SELECT labels FROM raw_fastmcp_items WHERE number = 1").fetchone() == (
            ["bug"],
        )
