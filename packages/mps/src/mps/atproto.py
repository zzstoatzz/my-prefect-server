"""Small atproto helpers shared by flows."""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING, Any

import httpx
from atproto_identity.resolver import IdResolver

if TYPE_CHECKING:
    from atproto import AsyncClient

PDS_BASE = "https://bsky.social"


@cache
def resolve_pds(did: str) -> str:
    """The PDS endpoint a DID document names, resolved once per process."""
    endpoint = IdResolver().did.ensure_resolve(did).get_pds_endpoint()
    if not endpoint:
        raise RuntimeError(f"{did}: DID document has no #atproto_pds service")
    return endpoint


def resolve_handle_pds(handle: str) -> str:
    """The PDS of the account a handle names."""
    return resolve_pds(IdResolver().handle.ensure_resolve(handle))


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
