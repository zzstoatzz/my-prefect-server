"""Tangled.org PDS fetch helpers and models."""

import httpx
from pydantic import BaseModel

PDS_BASE = "https://pds.zzstoatzz.io"
DID = "did:plc:xbtmt2zjwlrfegqvch7fboei"
HANDLE = "zzstoatzz.io"
TARGET_REPOS = ["zat", "zlay", "plyr.fm", "at-me", "pollz", "typeahead"]

XRPC = f"{PDS_BASE}/xrpc/com.atproto.repo.listRecords"


class TangledItem(BaseModel):
    """A tangled.org issue, PR, or comment fetched from the PDS."""

    repo: str
    kind: str  # "issue" | "pr" | "comment"
    title: str | None = None
    body: str = ""
    url: str
    at_uri: str
    author_did: str
    author_handle: str
    created_at: str
    parent_uri: str | None = None


def build_tangled_url(repo_name: str, kind: str) -> str:
    """Construct a tangled.org web URL.

    Links to the issues/pulls list page — the PDS doesn't store sequential
    issue numbers (those are appview-only), so we can't deep-link yet.
    """
    segment = "pulls" if kind == "pr" else "issues"
    return f"https://tangled.org/{HANDLE}/{repo_name}/{segment}"


def fetch_repo_at_uris(client: httpx.Client) -> dict[str, str]:
    """Read sh.tangled.repo records and return {at_uri: repo_name} for target repos."""
    resp = client.get(
        XRPC,
        params={"repo": DID, "collection": "sh.tangled.repo", "limit": 100},
    )
    resp.raise_for_status()

    lookup: dict[str, str] = {}
    for record in resp.json().get("records", []):
        name = record.get("value", {}).get("name", "")
        if name in TARGET_REPOS:
            lookup[record["uri"]] = name
    return lookup


def fetch_items(
    client: httpx.Client,
    collection: str,
    repo_uris: dict[str, str],
) -> list[TangledItem]:
    """Fetch records of a given collection, filtering to target repos."""
    is_comment = "comment" in collection
    is_pr = "pull" in collection
    kind = "comment" if is_comment else ("pr" if is_pr else "issue")

    cursor: str | None = None
    items: list[TangledItem] = []

    while True:
        params: dict[str, str | int] = {
            "repo": DID,
            "collection": collection,
            "limit": 100,
        }
        if cursor:
            params["cursor"] = cursor

        resp = client.get(XRPC, params=params)
        resp.raise_for_status()
        data = resp.json()

        for record in data.get("records", []):
            uri = record["uri"]
            value = record.get("value", {})

            # resolve repo — comments have a "subject" pointing to the parent
            # issue/PR, whose repo we already know
            repo_uri = value.get("repo", "")
            parent_uri = value.get("subject", "") if is_comment else None

            # for comments, resolve repo from the parent's repo field
            # by checking if the parent's repo URI is in our lookup
            if is_comment:
                repo_name = None
                # try to match parent subject to a known issue/PR repo
                for known_uri, name in repo_uris.items():
                    if parent_uri and known_uri in parent_uri:
                        repo_name = name
                        break
                if repo_name is None:
                    continue
            else:
                repo_name = repo_uris.get(repo_uri)
                if repo_name is None:
                    continue

            items.append(
                TangledItem(
                    repo=repo_name,
                    kind=kind,
                    title=value.get("title"),
                    body=value.get("body", ""),
                    url=build_tangled_url(repo_name, kind),
                    at_uri=uri,
                    author_did=DID,
                    author_handle=HANDLE,
                    created_at=value.get("createdAt", ""),
                    parent_uri=parent_uri if is_comment else None,
                )
            )

        cursor = data.get("cursor")
        if not cursor:
            break

    return items


# --- write path: open a pull request -----------------------------------------
#
# tangled pulls are patch-based. the changeset is gzipped, uploaded as a blob on
# the *author's* PDS, and referenced from a sh.tangled.repo.pull record. no push
# access to the target repo is involved, so opening a PR cannot write to it.
#
# ported from the operator's tangled-mcp (which implements the same record
# layer) rather than depended on: that package pins a prerelease fastmcp, and an
# MCP server framework has no business in a flow run to use forty lines of it.

import gzip as _gzip
import os as _os
import subprocess as _subprocess
import time as _time
from datetime import datetime as _dt
from datetime import timezone as _tz
from typing import Any

BOBBIN_URL = "https://api.tangled.org"
PULL_NSID = "sh.tangled.repo.pull"
_B32 = "234567abcdefghijklmnopqrstuvwxyz"


def _tid() -> str:
    """atproto TID (sortable base32 timestamp rkey)"""
    n = (_time.time_ns() // 1000) << 10 | int.from_bytes(_os.urandom(2), "big") % 1024
    return "".join(_B32[(n >> (60 - 5 * i)) & 31] for i in range(13))


def _now() -> str:
    return _dt.now(_tz.utc).isoformat().replace("+00:00", "Z")


def _bobbin(nsid: str, **params: Any) -> dict[str, Any]:
    resp = httpx.get(f"{BOBBIN_URL}/xrpc/{nsid}", params=params, timeout=20)
    if not resp.is_success:
        raise RuntimeError(f"{nsid} failed ({resp.status_code}) {resp.text[:200]}")
    return resp.json()


def resolve_pds(did: str) -> str:
    doc_url = (
        f"https://{did.removeprefix('did:web:')}/.well-known/did.json"
        if did.startswith("did:web:")
        else f"https://plc.directory/{did}"
    )
    doc = httpx.get(doc_url, timeout=20).json()
    for svc in doc.get("service", []):
        if svc.get("type") == "AtprotoPersonalDataServer":
            return svc["serviceEndpoint"]
    raise RuntimeError(f"no PDS endpoint in DID document for {did}")


def _resolve_repo_record(owner_did: str, name: str) -> tuple[str, dict[str, Any]]:
    """find a repo's sh.tangled.repo record, handling both rkey conventions.

    new-style records are keyed by repo name; legacy ones use a TID rkey and
    carry the name in the value, so the name lookup 502s and we page instead.
    """
    try:
        uri = f"at://{owner_did}/sh.tangled.repo/{name}"
        return uri, _bobbin("sh.tangled.repo.getRepo", repo=uri)["value"]
    except RuntimeError:
        pass

    cursor = None
    while True:
        params = {"subject": owner_did, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        page = _bobbin("sh.tangled.repo.listRepos", **params)
        items = page.get("items") or []
        for item in items:
            value = item.get("value") or {}
            if value.get("name") == name or item["uri"].rsplit("/", 1)[-1] == name:
                return item["uri"], value
        cursor = page.get("cursor")
        if not cursor or not items:
            raise ValueError(f"repo '{name}' not found for owner {owner_did}")


def build_patch(
    cwd: str, base: str, title: str, author: str, email: str | None = None
) -> str:
    """commit whatever changed in the working tree and render it as a git format-patch."""
    _subprocess.run(["git", "add", "-A"], cwd=cwd, check=True)
    status = _subprocess.run(
        ["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True
    ).stdout.strip()
    if not status:
        return ""
    _subprocess.run(
        [
            "git",
            "-c",
            f"user.name={author}",
            "-c",
            f"user.email={email or f'{author}@users.noreply'}",
            "commit",
            "-m",
            title,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
    )
    return _subprocess.run(
        ["git", "format-patch", f"{base}..HEAD", "--stdout"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _default_branch(record_uri: str) -> str:
    """the repo's default branch, "main" when the appview cannot say.

    the appview proxies this to the knot, and for some repos the knot
    answers 404 even though the repo clones and pulls fine (bot,
    2026-09-03). a pull is opened against a branch name, so a missing
    answer is not a reason to lose the patch."""
    try:
        return (
            _bobbin("sh.tangled.repo.getDefaultBranch", repo=record_uri).get("name")
            or "main"
        )
    except RuntimeError as e:
        print(f"getDefaultBranch unavailable, assuming main: {e}")
        return "main"


def create_pull(
    owner: str, repo: str, title: str, patch: str, body: str, handle: str, password: str
) -> dict[str, str]:
    """open a patch-based pull request, authored by `handle`."""
    owner_did = httpx.get(
        "https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle",
        params={"handle": owner},
        timeout=20,
    ).json()["did"]
    record_uri, value = _resolve_repo_record(owner_did, repo)
    repo_did = value.get("repoDid")
    if not repo_did:
        raise ValueError(f"repo '{owner}/{repo}' has no repoDid; cannot open pulls")
    branch = _default_branch(record_uri)

    author_did = httpx.get(
        "https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle",
        params={"handle": handle},
        timeout=20,
    ).json()["did"]
    pds = resolve_pds(author_did)
    session = httpx.post(
        f"{pds}/xrpc/com.atproto.server.createSession",
        json={"identifier": handle, "password": password},
        timeout=20,
    )
    session.raise_for_status()
    jwt = session.json()["accessJwt"]
    auth = {"Authorization": f"Bearer {jwt}"}

    blob_resp = httpx.post(
        f"{pds}/xrpc/com.atproto.repo.uploadBlob",
        content=_gzip.compress(patch.encode()),
        headers={**auth, "Content-Type": "application/gzip"},
        timeout=60,
    )
    blob_resp.raise_for_status()
    blob = blob_resp.json()["blob"]

    record: dict[str, Any] = {
        "$type": PULL_NSID,
        "title": title,
        "target": {"repo": repo_did, "branch": branch},
        "rounds": [{"patchBlob": blob, "createdAt": _now()}],
        "createdAt": _now(),
    }
    if body:
        record["body"] = body

    put = httpx.post(
        f"{pds}/xrpc/com.atproto.repo.putRecord",
        json={
            "repo": author_did,
            "collection": PULL_NSID,
            "rkey": _tid(),
            "record": record,
        },
        headers=auth,
        timeout=30,
    )
    put.raise_for_status()
    return {
        "uri": put.json()["uri"],
        # the appview assigns sequential pull numbers we can't know here
        "url": f"https://tangled.org/{owner}/{repo}/pulls",
    }


# --- pull conversation: rounds and comments ----------------------------------
#
# ported from tangled-mcp like create_pull above. two lexicon facts matter:
# comments rendered on the pull page are sh.tangled.feed.comment (subject
# strong-ref + markdown body object); sh.tangled.repo.pull.comment is legacy
# and read-only for us. rounds live inside the pull record itself and are
# appended, never rewritten.

FEED_COMMENT_NSID = "sh.tangled.feed.comment"
LEGACY_COMMENT_NSID = "sh.tangled.repo.pull.comment"
MARKDOWN_NSID = "sh.tangled.markup.markdown"


def get_record(uri: str) -> dict[str, Any]:
    """fetch any record by at-uri from its owner's PDS: {uri, cid, value}."""
    did, collection, rkey = uri.removeprefix("at://").split("/", 2)
    resp = httpx.get(
        f"{resolve_pds(did)}/xrpc/com.atproto.repo.getRecord",
        params={"repo": did, "collection": collection, "rkey": rkey},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def login(handle: str, password: str) -> tuple[str, str, dict[str, str]]:
    """createSession on the handle's own PDS: (pds, did, auth headers)."""
    did = httpx.get(
        "https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle",
        params={"handle": handle},
        timeout=20,
    ).json()["did"]
    pds = resolve_pds(did)
    resp = httpx.post(
        f"{pds}/xrpc/com.atproto.server.createSession",
        json={"identifier": handle, "password": password},
        timeout=20,
    )
    resp.raise_for_status()
    return pds, did, {"Authorization": f"Bearer {resp.json()['accessJwt']}"}


def append_round(
    pull_uri: str, patch: str, note: str, handle: str, password: str
) -> int:
    """add a new round to an existing pull you authored; returns the round count."""
    did, collection, rkey = pull_uri.removeprefix("at://").split("/", 2)
    if collection != PULL_NSID:
        raise ValueError(f"not a pull uri: {pull_uri}")
    pds, session_did, auth = login(handle, password)
    if session_did != did:
        raise ValueError("rounds can only be added to your own pulls")

    current = get_record(pull_uri)["value"]
    blob_resp = httpx.post(
        f"{pds}/xrpc/com.atproto.repo.uploadBlob",
        content=_gzip.compress(patch.encode()),
        headers={**auth, "Content-Type": "application/gzip"},
        timeout=60,
    )
    blob_resp.raise_for_status()
    rounds = [
        *current.get("rounds", []),
        {"patchBlob": blob_resp.json()["blob"], "createdAt": _now()},
    ]
    record = {**current, "rounds": rounds}
    if note:
        record["body"] = (
            f"{current.get('body', '')}\n\n---\nround {len(rounds)}: {note}".strip()
        )
    put = httpx.post(
        f"{pds}/xrpc/com.atproto.repo.putRecord",
        json={"repo": did, "collection": PULL_NSID, "rkey": rkey, "record": record},
        headers=auth,
        timeout=30,
    )
    put.raise_for_status()
    return len(rounds)


def comment_on_pull(pull_uri: str, body: str, handle: str, password: str) -> str:
    """comment on a pull as `handle`; the record lands in their repo. returns its uri."""
    target = get_record(pull_uri)
    rounds = target["value"].get("rounds", [])
    pds, did, auth = login(handle, password)
    record = {
        "$type": FEED_COMMENT_NSID,
        "subject": {"uri": pull_uri, "cid": target.get("cid")},
        "body": {"$type": MARKDOWN_NSID, "text": body, "original": body},
        "pullRoundIdx": max(len(rounds) - 1, 0),
        "createdAt": _now(),
    }
    put = httpx.post(
        f"{pds}/xrpc/com.atproto.repo.putRecord",
        json={
            "repo": did,
            "collection": FEED_COMMENT_NSID,
            "rkey": _tid(),
            "record": record,
        },
        headers=auth,
        timeout=30,
    )
    put.raise_for_status()
    return put.json()["uri"]


def comment_subject(value: dict[str, Any]) -> str:
    """the pull/issue at-uri a comment points at, for either comment lexicon."""
    subject = value.get("subject")
    if isinstance(subject, dict):
        return subject.get("uri", "")
    if isinstance(subject, str):
        return subject
    return value.get("pull", "") or value.get("issue", "")


def comment_text(value: dict[str, Any]) -> str:
    """the comment body text, for either comment lexicon."""
    body = value.get("body")
    if isinstance(body, dict):
        return body.get("text", "")
    return body or ""


def list_pull_comments(commenter_did: str, pull_uri: str) -> list[dict[str, str]]:
    """all of `commenter_did`'s comments on one pull, oldest first.

    reads the commenter's PDS directly — the authority — so this is the
    reconcile path that catches anything the stream missed.
    """
    pds = resolve_pds(commenter_did)
    out: list[dict[str, str]] = []
    for collection in (FEED_COMMENT_NSID, LEGACY_COMMENT_NSID):
        cursor = None
        while True:
            params: dict[str, Any] = {
                "repo": commenter_did,
                "collection": collection,
                "limit": 100,
            }
            if cursor:
                params["cursor"] = cursor
            resp = httpx.get(
                f"{pds}/xrpc/com.atproto.repo.listRecords", params=params, timeout=20
            )
            if resp.status_code == 400:
                break
            resp.raise_for_status()
            data = resp.json()
            for rec in data.get("records", []):
                value = rec.get("value", {})
                if comment_subject(value) == pull_uri:
                    out.append(
                        {
                            "uri": rec["uri"],
                            "text": comment_text(value),
                            "created_at": value.get("createdAt", ""),
                        }
                    )
            cursor = data.get("cursor")
            if not cursor:
                break
    return sorted(out, key=lambda c: c["created_at"])
