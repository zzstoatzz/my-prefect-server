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


@dataclass
class LikedPost:
    subject_uri: str  # the post URI (primary key)
    author_handle: str
    author_did: str
    text: str
    created_at: str  # post's creation time
    liked_at: str  # when nate liked it
    embed_type: str = ""  # external/images/record/video/""
    embed_text: str = ""  # link card title+url, image alt, quote text


def summarize_embed(embed: dict) -> tuple[str, str]:
    """Extract type + summary text from a post embed (raw API JSON)."""
    if not embed:
        return "", ""
    typ = embed.get("$type", "")
    if "external" in typ:
        ext = embed.get("external", {})
        title = ext.get("title", "")
        uri = ext.get("uri", "")
        desc = ext.get("description", "")
        parts = [p for p in [title, uri, desc] if p]
        return "external", " — ".join(parts)
    if "images" in typ:
        alts = [img.get("alt", "") for img in embed.get("images", [])]
        return "images", " | ".join(a for a in alts if a)
    if "record" in typ:
        rec = embed.get("record", {})
        # recordWithMedia wraps record + media
        if "record" in rec:
            rec = rec["record"]
        value = rec.get("value", rec)
        text = value.get("text", "")
        return "record", text[:500] if text else ""
    if "video" in typ:
        alt = embed.get("alt", "")
        return "video", alt
    return "", ""


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
