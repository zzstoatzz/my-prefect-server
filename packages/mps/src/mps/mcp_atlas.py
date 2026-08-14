"""Pure helpers for the mcp-atlas crawl (flows/mcp_atlas.py).

The atlas is a view over ``tech.waow.mcp.server`` records that publishers
keep on their own PDSes. Nothing here talks to the network — normalization
is kept pure so it can be tested against record shapes we don't control.
"""

from __future__ import annotations

from typing import Any

COLLECTION = "tech.waow.mcp.server"

MAX_TOOLS = 64
MAX_NAME = 128
MAX_DESCRIPTION = 500


def _http_url(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith(("https://", "http://")):
        return value
    return None


def normalize_record(
    did: str, handle: str | None, uri: str, value: dict[str, Any]
) -> dict[str, Any] | None:
    """Turn one PDS record into an atlas entry, or None if unusable.

    Records are arbitrary user data: anything beyond the lexicon's required
    ``name`` and ``description`` is optional, wrong types are dropped rather
    than failing the crawl, and string fields are clamped to the lexicon
    limits so one hostile record can't bloat the atlas.
    """
    name = value.get("name")
    description = value.get("description")
    if not isinstance(name, str) or not name.strip():
        return None
    if not isinstance(description, str) or not description.strip():
        return None

    tools_raw = value.get("tools")
    tools = [
        t[:MAX_NAME]
        for t in (tools_raw if isinstance(tools_raw, list) else [])
        if isinstance(t, str) and t.strip()
    ][:MAX_TOOLS]

    framework = value.get("framework")
    return {
        "did": did,
        "handle": handle,
        "uri": uri,
        "name": name.strip()[:MAX_NAME],
        "description": description.strip()[:MAX_DESCRIPTION],
        "repo": _http_url(value.get("repo")),
        "url": _http_url(value.get("url")),
        "manifest": _http_url(value.get("manifest")),
        "framework": framework.strip() if isinstance(framework, str) else None,
        "tools": tools,
        "createdAt": value.get("createdAt")
        if isinstance(value.get("createdAt"), str)
        else None,
    }


def handle_from_did_doc(doc: dict[str, Any]) -> str | None:
    """Extract the bare handle from a DID document's alsoKnownAs."""
    for aka in doc.get("alsoKnownAs", []):
        if isinstance(aka, str) and aka.startswith("at://"):
            return aka.removeprefix("at://")
    return None


def pds_from_did_doc(doc: dict[str, Any]) -> str | None:
    """Extract the PDS service endpoint from a DID document."""
    for svc in doc.get("service", []):
        if svc.get("id", "").endswith("#atproto_pds"):
            endpoint = svc.get("serviceEndpoint")
            return endpoint if isinstance(endpoint, str) else None
    return None
