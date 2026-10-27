"""Provider-reported OpenRouter spend for the configured key's UTC month."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import httpx

from mps.costs.types import LineItem, Period


class OpenRouterConnector:
    name = "openrouter"

    def __init__(self, token: str | None = None, block_name: str = "openrouter-api-key"):
        self.token = token
        self.block_name = block_name

    async def collect(self, period: Period) -> list[LineItem]:
        from mps.blocks import secret

        token = self.token or await secret(self.block_name)
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                "https://openrouter.ai/api/v1/key",
                headers={"Authorization": "Bearer " + token},
            )
            response.raise_for_status()
        data = response.json()["data"]
        usage = Decimal(str(data["usage_monthly"]))
        if not usage.is_finite() or usage < 0:
            raise ValueError("Invalid OpenRouter monthly usage")
        now = dt.datetime.now(dt.UTC)
        elapsed = Decimal(
            str(
                (
                    now - now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                ).total_seconds()
            )
        )
        if elapsed < 3600:
            raise RuntimeError(
                "OpenRouter monthly window is under one hour; cannot project a stable run-rate"
            )
        projected = usage * Decimal(30 * 86400) / elapsed
        return [
            LineItem(
                provider=self.name,
                project="shared",
                service="deepseek:api-key",
                amount=round(projected * 100),
                estimated=True,
                usage=f"${usage:f} provider-reported spend this UTC month",
                note="30-day run-rate scaled from provider-reported UTC month-to-date spend. Current configured key only; Includes all models used by this key; excludes other keys, BYOK upstream charges and credit-purchase fees.",
            )
        ]
