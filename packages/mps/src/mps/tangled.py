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
