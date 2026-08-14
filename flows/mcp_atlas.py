"""Crawl tech.waow.mcp.server records into the atlas at mcp.waow.tech.

Publishers declare their MCP servers as records on their *own* PDSes; this
flow builds one view over them and pushes it to the worker's KV via a
bearer-authed POST (mcp-atlas/worker.js). No Cloudflare credentials needed —
the only secret is the ingest token.

Discovery is `com.atproto.sync.listReposByCollection` on the relay (DIDs
only), then a per-DID `listRecords` against the owner's PDS for the record
bodies. Remote servers (records with a `url`) get a liveness probe: a real
MCP `initialize` request — a 2xx means something MCP-shaped answered.

Expected env vars (set by the deployment):
  - MCP_ATLAS_INGEST_TOKEN (block: mcp-atlas-ingest-token)
"""

import datetime
import os
from typing import Any

import httpx
from mps.mcp_atlas import (
    COLLECTION,
    atlas_positions,
    handle_from_did_doc,
    normalize_record,
    pds_from_did_doc,
)
from prefect import flow, get_run_logger, task
from prefect.tasks import exponential_backoff

RELAYS = ["https://relay.waow.tech", "https://relay1.us-east.bsky.network"]
ATLAS_ENDPOINT = "https://mcp.waow.tech/api/atlas.json"
USER_AGENT = "mcp-atlas/0.1 (+https://mcp.waow.tech; @zzstoatzz.io)"


@task(
    retries=2,
    retry_delay_seconds=exponential_backoff(backoff_factor=10),
    retry_jitter_factor=1,
)
def enumerate_dids() -> list[str]:
    """List every DID with records in the collection.

    Union across relays rather than first-success: relays index new
    collections at different speeds, and a 200-with-empty-repos from one
    would otherwise mask DIDs the other already knows about.
    """
    logger = get_run_logger()
    dids: set[str] = set()
    errors: list[Exception] = []
    with httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT}) as client:
        for relay in RELAYS:
            found = 0
            cursor: str | None = None
            try:
                while True:
                    params: dict[str, Any] = {"collection": COLLECTION, "limit": 500}
                    if cursor:
                        params["cursor"] = cursor
                    resp = client.get(
                        f"{relay}/xrpc/com.atproto.sync.listReposByCollection",
                        params=params,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    for r in data.get("repos", []):
                        dids.add(r["did"])
                        found += 1
                    cursor = data.get("cursor")
                    if not cursor:
                        break
                logger.info(f"{relay}: {found} DIDs publish {COLLECTION}")
            except httpx.HTTPError as exc:
                logger.warning(f"{relay} failed: {exc!r}")
                errors.append(exc)
    if len(errors) == len(RELAYS):
        raise RuntimeError(f"every relay failed enumerating {COLLECTION}") from errors[
            0
        ]
    return sorted(dids)


@task(retries=1, retry_delay_seconds=15)
def fetch_repo_records(did: str) -> list[dict[str, Any]]:
    """Resolve a DID to its PDS and read its records in the collection."""
    logger = get_run_logger()
    with httpx.Client(timeout=15, headers={"User-Agent": USER_AGENT}) as client:
        if did.startswith("did:plc:"):
            doc = client.get(f"https://plc.directory/{did}").raise_for_status().json()
        elif did.startswith("did:web:"):
            domain = did.removeprefix("did:web:")
            doc = (
                client.get(f"https://{domain}/.well-known/did.json")
                .raise_for_status()
                .json()
            )
        else:
            logger.warning(f"unsupported DID method: {did}")
            return []

        pds = pds_from_did_doc(doc)
        if pds is None:
            logger.warning(f"{did}: no PDS endpoint in DID document")
            return []
        handle = handle_from_did_doc(doc)

        entries: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {
                "repo": did,
                "collection": COLLECTION,
                "limit": 100,
            }
            if cursor:
                params["cursor"] = cursor
            resp = client.get(f"{pds}/xrpc/com.atproto.repo.listRecords", params=params)
            resp.raise_for_status()
            data = resp.json()
            for rec in data.get("records", []):
                entry = normalize_record(
                    did, handle, rec.get("uri", ""), rec.get("value", {})
                )
                if entry is not None:
                    entries.append(entry)
            cursor = data.get("cursor")
            if not cursor or not data.get("records"):
                break
    return entries


@task
def probe_liveness(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """MCP `initialize` against each remote endpoint; annotate `alive`.

    Short timeouts on purpose: an unreachable server should cost seconds,
    not stall the crawl. `alive` is only meaningful when `url` is set.
    """
    logger = get_run_logger()
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "mcp-atlas-crawler", "version": "0.1"},
        },
    }
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=5, headers=headers, follow_redirects=True) as client:
        for entry in entries:
            if not entry["url"]:
                entry["alive"] = None
                entry["authRequired"] = None
                continue
            try:
                resp = client.post(entry["url"], json=initialize)
                # 401/403 means something is serving and enforcing auth —
                # that's a live server, not an unreachable one
                entry["authRequired"] = resp.status_code in (401, 403)
                entry["alive"] = resp.is_success or entry["authRequired"]
            except httpx.HTTPError as exc:
                logger.info(f"{entry['name']} ({entry['url']}) unreachable: {exc!r}")
                entry["alive"] = False
                entry["authRequired"] = False
    return entries


@task(
    retries=2,
    retry_delay_seconds=exponential_backoff(backoff_factor=10),
    retry_jitter_factor=1,
)
def publish_atlas(atlas: dict[str, Any]) -> None:
    token = os.environ["MCP_ATLAS_INGEST_TOKEN"]
    resp = httpx.post(
        ATLAS_ENDPOINT,
        json=atlas,
        headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
        timeout=30,
    )
    resp.raise_for_status()


@flow(log_prints=True)
def mcp_atlas() -> None:
    logger = get_run_logger()
    dids = enumerate_dids()

    entries: list[dict[str, Any]] = []
    for future in fetch_repo_records.map(dids):
        entries.extend(future.result())
    entries = probe_liveness(entries)
    entries.sort(key=lambda e: (e["createdAt"] or "", e["uri"]), reverse=True)
    for entry, (x, y) in zip(entries, atlas_positions(entries)):
        entry["x"], entry["y"] = round(x, 4), round(y, 4)

    atlas = {
        "generatedAt": datetime.datetime.now(datetime.UTC).isoformat(),
        "collection": COLLECTION,
        "servers": entries,
    }
    publish_atlas(atlas)
    alive = sum(1 for e in entries if e["alive"])
    logger.info(
        f"atlas published: {len(entries)} servers from {len(dids)} DIDs ({alive} live remotes)"
    )


if __name__ == "__main__":
    mcp_atlas()
