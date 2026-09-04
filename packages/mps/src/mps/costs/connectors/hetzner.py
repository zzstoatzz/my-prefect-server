"""Hetzner Cloud connector.

Hetzner has no public "current invoice" endpoint, but server inventory carries
the exact contracted monthly list price per server type/location. For fixed
servers that's the real recurring charge, so these are billed (not estimated);
the note records that volumes/traffic overage aren't included.

Hetzner Cloud API tokens are PROJECT-scoped — there is no account-wide token.
So we accept one token per project: HCLOUD_TOKEN plus any HCLOUD_TOKEN_<suffix>
env var, and each var may itself be a comma-separated list. All projects are
queried and merged; project attribution comes from server names (projects.py),
so the Hetzner project name isn't needed. A token that fails to read is warned
loudly and skipped (its servers are omitted, never counted as $0) rather than
sinking the other projects. (Robot/dedicated servers use a different API and
are out of scope.)
"""

from __future__ import annotations

import os

import httpx

from mps.costs.projects import project_for
from mps.costs.types import LineItem, Period

API = "https://api.hetzner.cloud/v1"
PROVIDER = "hetzner"


async def _paged(token: str, path: str, key: str) -> list[dict]:
    out: list[dict] = []
    async with httpx.AsyncClient(
        base_url=API, headers={"Authorization": f"Bearer {token}"}, timeout=20
    ) as client:
        page = 1
        while True:
            resp = await client.get(path, params={"page": page, "per_page": 50})
            resp.raise_for_status()
            body = resp.json()
            out.extend(body.get(key, []))
            nxt = (body.get("meta", {}).get("pagination", {}) or {}).get("next_page")
            if not nxt:
                break
            page = nxt
    return out


async def _servers(token: str) -> list[dict]:
    return await _paged(token, "/servers", "servers")


async def _volumes(token: str) -> list[dict]:
    return await _paged(token, "/volumes", "volumes")


async def _volume_gb_month_cents(token: str) -> float:
    """Gross per-GB monthly volume price in cents, from /pricing."""
    async with httpx.AsyncClient(
        base_url=API, headers={"Authorization": f"Bearer {token}"}, timeout=20
    ) as client:
        resp = await client.get("/pricing")
        resp.raise_for_status()
        gross = resp.json()["pricing"]["volume"]["price_per_gb_month"]["gross"]
        return float(gross) * 100


def _monthly_cents(server: dict) -> int:
    """Gross monthly price for the server's location, in cents."""
    # /servers returns location at the top level; older shapes nested it
    # under datacenter. missing both falls through to the first listed price.
    location = (server.get("location") or {}).get("name") or (
        (server.get("datacenter") or {}).get("location") or {}
    ).get("name")
    for price in server.get("server_type", {}).get("prices", []):
        if price.get("location") == location:
            gross = float(price["price_monthly"]["gross"])
            return round(gross * 100)
    # fall back to the first listed price if location didn't match
    prices = server.get("server_type", {}).get("prices", [])
    if prices:
        return round(float(prices[0]["price_monthly"]["gross"]) * 100)
    return 0


def _project_tokens() -> list[tuple[str, str]]:
    """(label, token) for every HCLOUD_TOKEN / HCLOUD_TOKEN_<suffix> env var;
    each var may be a comma-separated list of tokens."""
    out: list[tuple[str, str]] = []
    for key, value in os.environ.items():
        if key != "HCLOUD_TOKEN" and not key.startswith("HCLOUD_TOKEN_"):
            continue
        label = "default" if key == "HCLOUD_TOKEN" else key[len("HCLOUD_TOKEN_") :].lower()
        for tok in (t.strip() for t in value.split(",")):
            if tok:
                out.append((label, tok))
    return out


class HetznerConnector:
    name = PROVIDER

    def __init__(self, tokens: list[str] | None = None):
        # explicit per-project tokens (flow loads these from the hetzner-tokens
        # block). when omitted, fall back to HCLOUD_TOKEN env for local dev.
        self._tokens = tokens

    async def collect(self, period: Period) -> list[LineItem]:
        tokens = [("explicit", t) for t in self._tokens] if self._tokens else _project_tokens()
        if not tokens:
            raise RuntimeError("no hetzner tokens provided (hetzner-tokens block or HCLOUD_TOKEN)")

        items: list[LineItem] = []
        seen: set[int] = set()  # dedupe servers if tokens overlap
        seen_volumes: set[int] = set()
        for label, token in tokens:
            try:
                servers = await _servers(token)
                volumes = await _volumes(token)
                gb_cents = await _volume_gb_month_cents(token) if volumes else 0.0
            except httpx.HTTPError as exc:
                print(
                    f"  hetzner: project '{label}' UNMEASURED ({exc}); "
                    "its resources are omitted — check that token"
                )
                continue
            for server in servers:
                sid = server.get("id")
                if sid is None or sid in seen:
                    continue
                seen.add(sid)
                name = server.get("name", "unknown")
                stype = server.get("server_type", {}).get("name", "?")
                items.append(
                    LineItem(
                        provider=PROVIDER,
                        project=project_for(name),
                        service=name,
                        amount=_monthly_cents(server),
                        estimated=False,
                        usage=stype,
                        note="monthly list price; excludes traffic overage",
                    )
                )
            # volumes bill per provisioned GB regardless of attachment; attribute
            # by the volume's own name (fall back to the attached server's name
            # when the volume name is auto-generated and matches no project).
            server_names = {s.get("id"): s.get("name", "") for s in servers}
            for volume in volumes:
                vid = volume.get("id")
                if vid is None or vid in seen_volumes:
                    continue
                seen_volumes.add(vid)
                vname = volume.get("name", "unknown")
                size_gb = int(volume.get("size", 0))
                project = project_for(vname)
                if project == "unattributed" and volume.get("server") in server_names:
                    project = project_for(server_names[volume["server"]])
                items.append(
                    LineItem(
                        provider=PROVIDER,
                        project=project,
                        service=f"{vname}:volume",
                        amount=round(size_gb * gb_cents),
                        estimated=False,
                        usage=f"{size_gb}GB block storage",
                        note="provisioned volume at per-GB list price",
                    )
                )
        return items
