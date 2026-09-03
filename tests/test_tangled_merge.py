from __future__ import annotations

import gzip

import httpx
import pytest
from mps import tangled
from mps.tangled import parse_verdict, pull_patch, review_verdict, touched_paths


def test_parse_verdict_reads_the_word_after_the_marker() -> None:
    assert parse_verdict("looks good\n\nVERDICT: approve") == "approve"
    assert parse_verdict("VERDICT:  Request-Changes because x") == "request-changes"
    assert parse_verdict("verdict: escalate") == "escalate"


def test_parse_verdict_none_without_a_marker() -> None:
    assert parse_verdict("i approve of this in spirit") is None
    assert parse_verdict("") is None
    assert parse_verdict("VERDICT: maybe") is None


def test_touched_paths_from_format_patch_headers() -> None:
    patch = (
        "From abc Mon Sep 17 00:00:00 2001\n"
        "diff --git a/src/bot/core/policy.py b/src/bot/core/policy.py\n"
        "--- a/src/bot/core/policy.py\n"
        "diff --git a/tests/test_x.py b/tests/test_x.py\n"
        "diff --git a/src/bot/core/policy.py b/src/bot/core/policy.py\n"
    )
    assert touched_paths(patch) == ["src/bot/core/policy.py", "tests/test_x.py"]
    assert touched_paths("") == []


PULL = "at://did:plc:author/sh.tangled.repo.pull/3abc"


def test_review_verdict_picks_the_latest_verdict_on_this_pull(monkeypatch) -> None:
    def fake_get(url, params=None, timeout=None):
        assert params["collection"] == tangled.FEED_COMMENT_NSID
        records = [
            _comment("c1", PULL, "VERDICT: request-changes", "2026-09-03T01:00:00Z"),
            _comment(
                "c2",
                "at://other/sh.tangled.repo.pull/x",
                "VERDICT: approve",
                "2026-09-03T03:00:00Z",
            ),
            _comment("c3", PULL, "no verdict here", "2026-09-03T04:00:00Z"),
            _comment("c4", PULL, "VERDICT: approve", "2026-09-03T02:00:00Z"),
        ]
        return _resp(url, json={"records": records})

    monkeypatch.setattr(tangled, "resolve_pds", lambda did: "https://pds.test")
    monkeypatch.setattr(httpx, "get", fake_get)
    got = review_verdict(PULL, "did:plc:reviewer")
    assert got is not None
    assert got["verdict"] == "approve"
    assert got["uri"].endswith("/c4")


def test_review_verdict_none_when_reviewer_has_not_spoken(monkeypatch) -> None:
    monkeypatch.setattr(tangled, "resolve_pds", lambda did: "https://pds.test")
    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, params=None, timeout=None: _resp(url, json={"records": []}),
    )
    assert review_verdict(PULL, "did:plc:reviewer") is None


def test_pull_patch_reads_the_latest_round_blob_gunzipped(monkeypatch) -> None:
    patch_text = "diff --git a/f b/f\n+hello\n"
    record = {
        "uri": PULL,
        "value": {
            "title": "t",
            "body": "b",
            "target": {"repo": "did:plc:repo", "branch": "main"},
            "rounds": [
                {"patchBlob": {"ref": {"$link": "old"}}},
                {"patchBlob": {"ref": {"$link": "new"}}},
            ],
        },
    }

    def fake_get(url, params=None, timeout=None):
        assert url.endswith("/xrpc/com.atproto.sync.getBlob")
        assert params == {"did": "did:plc:author", "cid": "new"}
        return _resp(url, content=gzip.compress(patch_text.encode()))

    monkeypatch.setattr(tangled, "get_record", lambda uri: record)
    monkeypatch.setattr(tangled, "resolve_pds", lambda did: "https://pds.test")
    monkeypatch.setattr(httpx, "get", fake_get)
    got = pull_patch(PULL)
    assert got["patch"] == patch_text
    assert got["rounds"] == 2
    assert got["target_repo_did"] == "did:plc:repo"
    assert got["branch"] == "main"


def test_pull_patch_rejects_non_pull_uris() -> None:
    with pytest.raises(ValueError):
        pull_patch("at://did:plc:author/sh.tangled.repo.issue/3abc")


def _resp(url: str, **kw) -> httpx.Response:
    return httpx.Response(200, request=httpx.Request("GET", url), **kw)


def _comment(rkey: str, subject: str, text: str, created: str) -> dict:
    return {
        "uri": f"at://did:plc:reviewer/{tangled.FEED_COMMENT_NSID}/{rkey}",
        "value": {
            "subject": {"uri": subject},
            "body": {"$type": tangled.MARKDOWN_NSID, "text": text},
            "createdAt": created,
        },
    }
