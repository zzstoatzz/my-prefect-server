"""Fetch like records from nate's PDS."""

from dataclasses import dataclass

import httpx

PDS_BASE = "https://pds.zzstoatzz.io"
DID = "did:plc:xbtmt2zjwlrfegqvch7fboei"
XRPC = f"{PDS_BASE}/xrpc/com.atproto.repo.listRecords"


@dataclass
class LikeRecord:
    at_uri: str  # like record URI (primary key)
    subject_uri: str  # the post that was liked
    created_at: str  # ISO timestamp from like record


def fetch_likes(client: httpx.Client) -> list[LikeRecord]:
    """Fetch like records from nate's PDS. Cursor-based pagination."""
    cursor: str | None = None
    items: list[LikeRecord] = []

    while True:
        params: dict[str, str | int] = {
            "repo": DID,
            "collection": "app.bsky.feed.like",
            "limit": 100,
        }
        if cursor:
            params["cursor"] = cursor

        resp = client.get(XRPC, params=params)
        resp.raise_for_status()
        data = resp.json()

        for record in data.get("records", []):
            value = record.get("value", {})
            subject = value.get("subject", {})
            items.append(
                LikeRecord(
                    at_uri=record["uri"],
                    subject_uri=subject.get("uri", ""),
                    created_at=value.get("createdAt", ""),
                )
            )

        cursor = data.get("cursor")
        if not cursor:
            break

    return items
