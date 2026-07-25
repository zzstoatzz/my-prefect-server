"""Hetzner Cloud + Robot dedicated-server connector.

Hetzner has no public "current invoice" endpoint, but server inventory carries
the exact contracted monthly list price per server type/location. For fixed
servers that's the real recurring charge, so these are billed (not estimated);
the note records that volumes/traffic overage aren't included.

Hetzner Cloud API tokens are PROJECT-scoped — there is no account-wide token.
So we accept one token per project: HCLOUD_TOKEN plus any HCLOUD_TOKEN_<suffix>
env var, and each var may itself be a comma-separated list. All projects are
queried and merged; project attribution comes from server names (projects.py),
so the Hetzner project name isn't needed. Robot inventory identifies active
dedicated servers, while an explicit USD price registry records the contracted
monthly amount. That registry is required because auction-server inventory does
not expose the winning price.

Robot failures are fail-CLOSED: an unreachable Robot API or an active server with
no registered price raises, so the flow refuses to publish rather than shipping a
snapshot whose missing dedicated servers read as a real cost decrease. This is
deliberately stricter than the per-project Cloud token handling above, where one
bad token skips only its own project.
"""

from __future__ import annotations

import os

import httpx

from mps.costs.projects import project_for
from mps.costs.types import LineItem, Period

API = "https://api.hetzner.cloud/v1"
ROBOT_API = "https://robot-ws.your-server.de"
PROVIDER = "hetzner"


async def _servers(token: str) -> list[dict]:
    servers: list[dict] = []
    async with httpx.AsyncClient(
        base_url=API, headers={"Authorization": f"Bearer {token}"}, timeout=20
    ) as client:
        page = 1
        while True:
            resp = await client.get(
                "/servers", params={"page": page, "per_page": 50}
            )
            resp.raise_for_status()
            body = resp.json()
            servers.extend(body.get("servers", []))
            nxt = (body.get("meta", {}).get("pagination", {}) or {}).get("next_page")
            if not nxt:
                break
            page = nxt
    return servers


async def _robot_servers(username: str, password: str) -> list[dict]:
    async with httpx.AsyncClient(
        base_url=ROBOT_API, auth=(username, password), timeout=20
    ) as client:
        resp = await client.get("/server")
        resp.raise_for_status()
        return [entry["server"] for entry in resp.json()]


def _monthly_cents(server: dict) -> int:
    """Gross monthly price for the server's location, in cents."""
    location = (server.get("datacenter", {}).get("location", {}) or {}).get("name")
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

    def __init__(
        self,
        tokens: list[str] | None = None,
        robot_credentials: tuple[str, str] | None = None,
        robot_monthly_usd: dict[str, float] | None = None,
    ):
        # explicit per-project tokens (flow loads these from the hetzner-tokens
        # block). when omitted, fall back to HCLOUD_TOKEN env for local dev.
        self._tokens = tokens
        self._robot_credentials = robot_credentials
        self._robot_monthly_usd = robot_monthly_usd or {}

    async def collect(self, period: Period) -> list[LineItem]:
        tokens = (
            [("explicit", t) for t in self._tokens]
            if self._tokens
            else _project_tokens()
        )
        if not tokens and not self._robot_credentials:
            raise RuntimeError(
                "no Hetzner credentials provided (Cloud tokens or Robot credentials)"
            )

        items: list[LineItem] = []
        seen: set[int] = set()  # dedupe servers if tokens overlap
        for label, token in tokens:
            try:
                servers = await _servers(token)
            except httpx.HTTPError as exc:
                print(
                    f"  hetzner: project '{label}' UNMEASURED ({exc}); "
                    "its servers are omitted — check that token"
                )
                continue
            for server in servers:
                sid = server.get("id")
                if not isinstance(sid, int):
                    print(
                        f"  hetzner: project '{label}' returned a server without "
                        "an integer id; omitting it"
                    )
                    continue
                if sid in seen:
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
                        note="monthly list price; excludes volumes & traffic overage",
                    )
                )

        if self._robot_credentials:
            username, password = self._robot_credentials
            # a Robot outage would drop every dedicated server at once, so it
            # raises here and lets the flow refuse the snapshot rather than
            # publishing a hole that reads as a real cost decrease.
            robot_servers = await _robot_servers(username, password)

            unpriced: list[str] = []
            for server in robot_servers:
                if server.get("cancelled") or server.get("status") != "ready":
                    continue
                number = str(server.get("server_number", ""))
                name = server.get("server_name") or f"robot-{number}"
                product = server.get("product", "dedicated")
                price = next(
                    (
                        self._robot_monthly_usd[key]
                        for key in (number, name, product)
                        if key in self._robot_monthly_usd
                    ),
                    None,
                )
                if price is None:
                    unpriced.append(f"'{name}' ({number}, {product})")
                    continue
                items.append(
                    LineItem(
                        provider=PROVIDER,
                        project=project_for(name),
                        service=name,
                        amount=round(price * 100),
                        estimated=False,
                        usage=f"{product} · {server.get('dc', '?')}",
                        note="contracted Robot monthly price; excludes one-time setup",
                    )
                )

            if unpriced:
                raise RuntimeError(
                    "Robot servers with no contracted USD monthly price in the "
                    f"hetzner-robot block: {', '.join(unpriced)}. Add them — an "
                    "active server must never be omitted or counted as $0."
                )
        return items
