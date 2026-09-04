"""Regression tests for ingest surviving renamed/transferred github repos.

ingest-13ea7ef3 failed because github 301'd repos/SerenityUiX/ableton-mcp
to its numeric /repositories/<id>/ url after a rename, and the httpx client
in fetch_issue_or_pr didn't follow redirects. We now follow redirects and
store the canonical repo name from html_url so rows converge post-rename.
"""

from functools import partial
from unittest.mock import patch

import httpx
from mps.github import IssueRef

from flows.ingest import fetch_issue_or_pr, gh_client

ISSUE_JSON = {
    "title": "hello",
    "state": "open",
    "html_url": "https://github.com/NewOwner/new-name/issues/23",
    "labels": [],
    "user": {"login": "someone"},
    "comments": 0,
    "reactions": {"total_count": 0},
}


def _renamed_repo_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/repos/OldOwner/old-name/issues/23":
        return httpx.Response(
            301, headers={"location": "https://api.github.com/repositories/9514/issues/23"}
        )
    if request.url.path == "/repositories/9514/issues/23":
        return httpx.Response(200, json=ISSUE_JSON)
    return httpx.Response(404)


def test_fetch_follows_301_and_stores_canonical_repo():
    transport = httpx.MockTransport(_renamed_repo_handler)
    ref = IssueRef(repo="OldOwner/old-name", number=23, subject_type="Issue")
    with patch("flows.ingest.gh_client", partial(gh_client, transport=transport)):
        result = fetch_issue_or_pr.fn(ref, token="t")

    assert result is not None
    assert result.repo == "NewOwner/new-name"
    assert result.title == "hello"


def test_fetch_keeps_ref_repo_when_html_url_missing():
    payload = {**ISSUE_JSON, "html_url": None}
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    ref = IssueRef(repo="Owner/name", number=5, subject_type="Issue")
    with patch("flows.ingest.gh_client", partial(gh_client, transport=transport)):
        result = fetch_issue_or_pr.fn(ref, token="t")

    assert result is not None
    assert result.repo == "Owner/name"
