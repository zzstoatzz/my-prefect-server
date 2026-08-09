"""Small atproto helpers shared by phi-identity flows."""

from __future__ import annotations

from typing import Any

import httpx

PDS_BASE = "https://bsky.social"


def create_bsky_session(handle: str, password: str) -> dict[str, Any]:
    """Create an app-password session through the bsky entryway."""
    resp = httpx.post(
        f"{PDS_BASE}/xrpc/com.atproto.server.createSession",
        json={"identifier": handle, "password": password},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()

