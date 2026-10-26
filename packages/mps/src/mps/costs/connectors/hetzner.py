"""Hetzner Cloud connector.

Hetzner has no public "current invoice" endpoint, but server inventory carries
the exact contracted monthly list price per server type/location. For fixed
servers that's the real recurring charge, so these are billed (not estimated);
the note records that traffic overage isn't included. Volumes, snapshot/backup
images and primary IPv4 addresses bill separately from the server type, so each
gets its own line at the /pricing list price.

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
from dataclasses import dataclass

import httpx

from mps.costs.projects import project_for
from mps.costs.types import LineItem, Period

API = "https://api.hetzner.cloud/v1"
PROVIDER = "hetzner"


async def _paged(token: str, path: str, key: str, **params: str) -> list[dict]:
    out: list[dict] = []
    async with httpx.AsyncClient(
        base_url=API, headers={"Authorization": f"Bearer {token}"}, timeout=20
    ) as client:
        page = 1
        while True:
            resp = await client.get(path, params={**params, "page": page, "per_page": 50})
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


async def _images(token: str) -> list[dict]:
    return await _paged(token, "/images", "images", type="snapshot") + await _paged(
        token, "/images", "images", type="backup"
    )


async def _primary_ips(token: str) -> list[dict]:
    return await _paged(token, "/primary_ips", "primary_ips")


@dataclass
class _Prices:
    """Gross monthly list prices in cents. Primary IPs are keyed by
    (type, location); IPv6 has no price entry because it is free."""

    volume_gb: float
    image_gb: float
    primary_ip: dict[tuple[str, str], float]


async def _pricing(token: str) -> _Prices:
    async with httpx.AsyncClient(
        base_url=API, headers={"Authorization": f"Bearer {token}"}, timeout=20
    ) as client:
        resp = await client.get("/pricing")
        resp.raise_for_status()
        pricing = resp.json()["pricing"]
    return _Prices(
        volume_gb=float(pricing["volume"]["price_per_gb_month"]["gross"]) * 100,
        image_gb=float(pricing["image"]["price_per_gb_month"]["gross"]) * 100,
        primary_ip={
            (ip["type"], price["location"]): float(price["price_monthly"]["gross"]) * 100
            for ip in pricing.get("primary_ips", [])
            for price in ip.get("prices", [])
        },
    )


def _ip_location(ip: dict) -> str | None:
    return (ip.get("location") or {}).get("name") or (
        (ip.get("datacenter") or {}).get("location") or {}
    ).get("name")


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
        self.unmeasured: list[str] = []
        tokens = [("explicit", t) for t in self._tokens] if self._tokens else _project_tokens()
        if not tokens:
            raise RuntimeError("no hetzner tokens provided (hetzner-tokens block or HCLOUD_TOKEN)")

        items: list[LineItem] = []
        seen: set[int] = set()  # dedupe servers if tokens overlap
        seen_volumes: set[int] = set()
        seen_images: set[int] = set()
        seen_ips: set[int] = set()
        for label, token in tokens:
            try:
                servers = await _servers(token)
                volumes = await _volumes(token)
                images = await _images(token)
                primary_ips = await _primary_ips(token)
                prices = await _pricing(token) if volumes or images or primary_ips else None
            except httpx.HTTPError as exc:
                self.unmeasured.append(f"project '{label}' ({exc})")
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
            if prices is None:
                continue
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
                        amount=round(size_gb * prices.volume_gb),
                        estimated=False,
                        usage=f"{size_gb}GB block storage",
                        note="provisioned volume at per-GB list price",
                    )
                )
            # images and IPs are named after the server they came from, so the
            # inventory's server costKey (hetzner:zlay) also owns hetzner:zlay:ipv4
            # and hetzner:zlay:snapshot-<id> by colon-suffix match.
            for image in images:
                iid = image.get("id")
                if iid is None or iid in seen_images:
                    continue
                seen_images.add(iid)
                source = (image.get("created_from") or {}).get("name") or "image"
                size_gb = float(image.get("image_size") or 0)
                kind = image.get("type", "snapshot")
                description = image.get("description")
                items.append(
                    LineItem(
                        provider=PROVIDER,
                        project=project_for(source),
                        service=f"{source}:{kind}-{iid}",
                        amount=round(size_gb * prices.image_gb),
                        estimated=False,
                        usage=f"{size_gb:.2f}GB {kind} image"
                        + (f": {description}" if description else ""),
                        note="stored image size at per-GB list price",
                    )
                )
            for ip in primary_ips:
                pid = ip.get("id")
                if pid is None or pid in seen_ips:
                    continue
                seen_ips.add(pid)
                cents = prices.primary_ip.get((ip.get("type", ""), _ip_location(ip) or ""))
                if not cents:
                    continue
                owner = server_names.get(ip.get("assignee_id")) or ip.get("name", "unknown")
                items.append(
                    LineItem(
                        provider=PROVIDER,
                        project=project_for(owner),
                        service=f"{owner}:{ip['type']}",
                        amount=round(cents),
                        estimated=False,
                        usage=f"primary {ip['type']}"
                        + ("" if ip.get("assignee_id") else ", unassigned"),
                        note="primary IP monthly list price; billed separately from the server",
                    )
                )
        return items
