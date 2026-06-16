"""Neon (serverless Postgres) connector.

Neon API keys are org-scoped: list projects under each org (org_id is required),
and bill per plan, not per project. Turning the consumption API into dollars
means modeling plan quotas + overage, so we keep it honest and simple — take the
org's plan-tier base and spread it across that org's projects, flagged estimated.

Auth: NEON_API_KEY. Plan base is derived from the org's plan tier; override any
tier with NEON_PLAN_USD.
"""

from __future__ import annotations

import os

import httpx

from mps.costs.projects import project_for
from mps.costs.types import LineItem, Period

API = "https://console.neon.tech/api/v2"
PROVIDER = "neon"

# monthly base per Neon plan tier (USD), 2026. usage-based overage on top of
# these is not modeled (hence estimated=True).
_PLAN_BASE_USD = {"free": 0.0, "launch": 19.0, "scale": 69.0, "business": 700.0}


async def _orgs(client: httpx.AsyncClient) -> list[dict]:
    resp = await client.get("/users/me/organizations")
    resp.raise_for_status()
    return resp.json().get("organizations", [])


async def _projects(client: httpx.AsyncClient, org_id: str) -> list[dict]:
    resp = await client.get("/projects", params={"org_id": org_id})
    resp.raise_for_status()
    return resp.json().get("projects", [])


class NeonConnector:
    name = PROVIDER

    async def collect(self, period: Period) -> list[LineItem]:
        key = os.environ.get("NEON_API_KEY")
        if not key:
            raise RuntimeError("NEON_API_KEY not set")
        override = os.environ.get("NEON_PLAN_USD")

        headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
        items: list[LineItem] = []
        async with httpx.AsyncClient(base_url=API, headers=headers, timeout=20) as client:
            for org in await _orgs(client):
                org_id = org["id"]
                plan = (org.get("plan") or "free").lower()
                base = float(override) if override is not None else _PLAN_BASE_USD.get(plan, 0.0)
                base_cents = round(base * 100)

                projects = await _projects(client, org_id)
                if not projects or base_cents == 0:
                    continue

                # spread the org's plan base evenly across its projects
                per, remainder = divmod(base_cents, len(projects))
                for i, proj in enumerate(projects):
                    name = proj.get("name", proj.get("id", "unknown"))
                    items.append(
                        LineItem(
                            provider=PROVIDER,
                            project=project_for(name),
                            service=name,
                            amount=per + (1 if i < remainder else 0),
                            estimated=True,
                            usage=f"{plan} plan",
                            note=f"share of neon {plan} base (${base:.0f}/mo); overage not computed",
                        )
                    )
        return items
