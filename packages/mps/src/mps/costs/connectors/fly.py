"""Fly.io connector.

Fly has no stable public per-app billing endpoint, so we inventory machines via
the Machines API and estimate monthly compute from guest size. Every line item
is flagged estimated=True — treat it as a planning figure, reconcile against the
Fly dashboard for the billed total. Volumes, bandwidth, and IPs are not counted.

Auth: FLY_API_TOKEN. Org via FLY_ORG_SLUG (default "personal").
"""

from __future__ import annotations

import os

import httpx

from mps.costs.projects import project_for
from mps.costs.types import LineItem, Period

API = "https://api.machines.dev/v1"
PROVIDER = "fly"

# approximate fly compute rates (USD/month), 2026. shared vCPU and RAM are
# billed separately; performance vCPUs cost ~16x a shared one.
_SHARED_VCPU_MO = 1.94
_PERF_VCPU_MO = 31.00
_RAM_GB_MO = 5.00


def _machine_monthly_cents(guest: dict, started: bool) -> int:
    cpus = guest.get("cpus", 1)
    cpu_kind = guest.get("cpu_kind", "shared")
    mem_gb = guest.get("memory_mb", 256) / 1024
    vcpu_rate = _PERF_VCPU_MO if cpu_kind == "performance" else _SHARED_VCPU_MO
    monthly = cpus * vcpu_rate + mem_gb * _RAM_GB_MO
    # fly bills stopped machines for storage only (~rootfs); approximate as 15%.
    if not started:
        monthly *= 0.15
    return round(monthly * 100)


async def _apps_with_machines(token: str, org: str) -> dict[str, list[dict]]:
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=API, headers=headers, timeout=30) as client:
        resp = await client.get("/apps", params={"org_slug": org})
        resp.raise_for_status()
        apps = [a["name"] for a in resp.json().get("apps", [])]

        out: dict[str, list[dict]] = {}
        for app in apps:
            r = await client.get(f"/apps/{app}/machines")
            if r.status_code != 200:
                out[app] = []
                continue
            out[app] = r.json()
    return out


class FlyConnector:
    name = PROVIDER

    async def collect(self, period: Period) -> list[LineItem]:
        token = os.environ.get("FLY_API_TOKEN")
        if not token:
            raise RuntimeError("FLY_API_TOKEN not set")
        org = os.environ.get("FLY_ORG_SLUG", "personal")

        items: list[LineItem] = []
        for app, machines in (await _apps_with_machines(token, org)).items():
            if not machines:
                continue
            cents = 0
            sizes: list[str] = []
            for m in machines:
                guest = (m.get("config", {}) or {}).get("guest", {}) or {}
                started = m.get("state") == "started"
                cents += _machine_monthly_cents(guest, started)
                sizes.append(
                    f"{guest.get('cpu_kind', '?')}-{guest.get('cpus', '?')}x"
                    f"/{guest.get('memory_mb', '?')}MB"
                )
            items.append(
                LineItem(
                    provider=PROVIDER,
                    project=project_for(app),
                    service=app,
                    amount=cents,
                    estimated=True,
                    usage=f"{len(machines)} machine(s): " + ", ".join(sizes),
                    note="estimated from machine inventory; reconcile with fly dashboard",
                )
            )
        return items
