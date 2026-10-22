"""Turso connector.

One org-level Turso plan backs many projects (typeahead, atlas, ...), so it is
attributed to `shared` rather than split. The prefect blocks hold database auth
tokens, which cannot read the platform billing API, so the line is the plan's
list price, flagged estimated. Overage is not modeled.

Plan via TURSO_PLAN (default "scaler", per `turso plan show`); override the
price with TURSO_PLAN_USD.
"""

from __future__ import annotations

import os

from mps.costs.types import LineItem, Period

PROVIDER = "turso"

# monthly list price per Turso plan (USD), turso.tech/pricing October 2026.
# annual billing discounts Scaler to $24.92/mo.
_PLAN_USD = {"free": 0.0, "developer": 4.99, "scaler": 29.0, "pro": 499.0}


class TursoConnector:
    name = PROVIDER

    async def collect(self, period: Period) -> list[LineItem]:
        plan = os.environ.get("TURSO_PLAN", "scaler").lower()
        override = os.environ.get("TURSO_PLAN_USD")
        if override is not None:
            usd = float(override)
        elif plan in _PLAN_USD:
            usd = _PLAN_USD[plan]
        else:
            raise RuntimeError(f"unknown turso plan {plan!r}; set TURSO_PLAN_USD")
        cents = round(usd * 100)
        if cents == 0:
            return []
        return [
            LineItem(
                provider=PROVIDER,
                project="shared",
                service=f"{plan}-plan",
                amount=cents,
                estimated=True,
                usage=f"{plan} plan",
                note=(
                    f"turso {plan} list price (${usd:.2f}/mo) for the org; overage and "
                    "annual-billing discount not measured"
                ),
            )
        ]
