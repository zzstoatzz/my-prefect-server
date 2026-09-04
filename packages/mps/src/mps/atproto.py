"""Small atproto helpers shared by phi-identity flows."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from atproto import AsyncClient

PDS_BASE = "https://bsky.social"


def session_did(client: AsyncClient) -> str:
    """The did of the logged-in account; a client that never logged in has none."""
    if client.me is None:
        raise RuntimeError("atproto client is not logged in")
    return client.me.did


def create_bsky_session(handle: str, password: str) -> dict[str, Any]:
    """Create an app-password session through the bsky entryway."""
    resp = httpx.post(
        f"{PDS_BASE}/xrpc/com.atproto.server.createSession",
        json={"identifier": handle, "password": password},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()
