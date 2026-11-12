from __future__ import annotations

import httpx
from pydantic import BaseModel, Field, TypeAdapter

from mps.costs.types import LineItem

INVENTORY_URL = "https://nate.tngl.io/services.json"

LIMITS = {
    "fly": "Inventory and measured uptime at list prices; excludes network, paid IPs, discounts and taxes.",
    "hetzner": "Inventory/lifecycle receipts at list prices; excludes traffic, discounts and taxes.",
    "cloudflare": "R2 storage estimates and configured fixed charges; fixed charges need invoice reconciliation; operations and other usage are excluded.",
    "neon": "Legacy plan-price estimate, not billed consumption; plan rates and usage need reconciliation.",
    "turso": "Configured plan list price, not live billing; annual discounts and overage are excluded.",
    "anthropic": "Coral process-lifetime meter extrapolated to 30 days; other applications are excluded.",
    "typesafe": "Coral process-lifetime meter extrapolated to 30 days; account-wide billing is not measured.",
    "openrouter": "Configured key only; other keys, upstream BYOK charges and credit-purchase fees are excluded.",
}


class Resource(BaseModel):
    provider: str
    state: str = ""


class Project(BaseModel):
    name: str
    cost_keys: list[str] = Field(alias="costKeys")
    resources: list[Resource]


async def inventory() -> list[Project]:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(INVENTORY_URL)
        response.raise_for_status()
    projects = TypeAdapter(list[Project]).validate_python(response.json())
    if not projects or len({p.name for p in projects}) != len(projects):
        raise ValueError("Empty or duplicate cost ownership inventory")
    return projects


def reconcile(items: list[LineItem], projects: list[Project]) -> tuple[list[LineItem], list[str]]:
    result = []
    issues = []
    for item in items:
        key = f"{item.provider}:{item.service}"
        owners = [
            p.name
            for p in projects
            if any(key == resource or key.startswith(resource + ":") for resource in p.cost_keys)
        ]
        if len(owners) == 1:
            owner = owners[0]
        elif not owners and item.project == "shared":
            owner = "shared"
        else:
            owner = "unattributed"
            issues.append(f"{key}: {'conflicting ownership' if owners else 'unassigned resource'}")
        result.append(item.model_copy(update={"project": owner}))
    measured = {item.provider for item in items}
    expected = {
        r.provider
        for p in projects
        for r in p.resources
        if r.state not in {"deleted", "retired"} and r.provider != "wisp"
    }
    issues += [f"{p}: no amount collected; not verified free" for p in sorted(expected - measured)]
    issues += [f"{p}: {LIMITS[p]}" for p in sorted(measured & LIMITS.keys())]
    return result, issues
