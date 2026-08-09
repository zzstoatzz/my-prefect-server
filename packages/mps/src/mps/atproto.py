"""Small atproto helpers shared by phi-identity flows."""

from __future__ import annotations

from typing import Any

import httpx

PDS_BASE = "https://bsky.social"
CHAT_BASE = "https://api.bsky.chat"
# DMs live on a separate service; requests must declare the proxy target, and
# the app password must have been created with DM access enabled
CHAT_PROXY = "did:web:api.bsky.chat#bsky_chat"


def create_bsky_session(handle: str, password: str) -> dict[str, Any]:
    """Create an app-password session through the bsky entryway."""
    resp = httpx.post(
        f"{PDS_BASE}/xrpc/com.atproto.server.createSession",
        json={"identifier": handle, "password": password},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def resolve_did(handle: str) -> str:
    resp = httpx.get(
        f"{PDS_BASE}/xrpc/com.atproto.identity.resolveHandle",
        params={"handle": handle},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["did"]


def send_dm(session: dict[str, Any], recipient_handle: str, text: str) -> str:
    """Send a bluesky DM from an authenticated session. Returns the message id."""
    headers = {
        "Authorization": f"Bearer {session['accessJwt']}",
        "atproto-proxy": CHAT_PROXY,
    }
    convo = httpx.get(
        f"{CHAT_BASE}/xrpc/chat.bsky.convo.getConvoForMembers",
        params={"members": resolve_did(recipient_handle)},
        headers=headers,
        timeout=15,
    )
    convo.raise_for_status()
    convo_id = convo.json()["convo"]["id"]

    sent = httpx.post(
        f"{CHAT_BASE}/xrpc/chat.bsky.convo.sendMessage",
        json={"convoId": convo_id, "message": {"text": text}},
        headers=headers,
        timeout=15,
    )
    sent.raise_for_status()
    return sent.json()["id"]
