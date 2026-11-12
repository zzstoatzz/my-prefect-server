"""Discover Neon billing scope without inventing a retired plan minimum.

Current Neon plans are usage-based. The consumption API exposes project usage,
but invoice reconciliation (including allowances and plan-specific rates) is not
implemented here. Unknown spend must not be represented as zero or divided evenly.
"""

from __future__ import annotations

import os

import httpx

from mps.costs.types import LineItem, Period

API = "https://console.neon.tech/api/v2"


class NeonConnector:
    name = "neon"

    def __init__(self):
        self.unmeasured: list[str] = []

    async def collect(self, period: Period) -> list[LineItem]:
        key = os.environ.get("NEON_API_KEY")
        if not key:
            raise RuntimeError("NEON_API_KEY not set")
        self.unmeasured = []
        async with httpx.AsyncClient(
            base_url=API,
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            timeout=20,
        ) as client:
            response = await client.get("/users/me/organizations")
            response.raise_for_status()
        for org in response.json()["organizations"]:
            self.unmeasured.append(
                f"{org['name']} ({org.get('plan', 'unknown')} plan): usage-based billing is not reconciled; retired plan minimums and equal project allocations are excluded"
            )
        return []
