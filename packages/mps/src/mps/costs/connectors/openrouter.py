"""Provider-reported OpenRouter spend for the configured key's UTC month."""

from __future__ import annotations

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
        return [
            LineItem(
                provider=self.name,
                project="shared",
                service="deepseek:api-key",
                amount=round(usage * 100),
                estimated=False,
                basis="month_to_date",
                usage=f"${usage:f} provider-reported spend this UTC month",
                note="Provider-reported current UTC month-to-date spend; not a monthly forecast. Current configured key only; Includes all models used by this key; excludes other keys, BYOK upstream charges and credit-purchase fees.",
            )
        ]
