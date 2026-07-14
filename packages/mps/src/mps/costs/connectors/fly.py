"""Fly.io connector.

Fly has no stable public per-app billing endpoint, so we inventory machines and
volumes via the Machines API and estimate their monthly list-price cost. Every
line item is flagged estimated=True — treat it as a planning figure and
reconcile it against the Fly dashboard. Bandwidth and paid IPs are not counted.

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
_VOLUME_GB_MO = 0.15

# The named CPU preset includes this much RAM per vCPU. Only RAM above the
# preset is billed at _RAM_GB_MO; charging for the full allocation overstated a
# shared-cpu-1x/1GB Machine by $1.25/month.
_INCLUDED_RAM_GB_PER_CPU = {"shared": 0.25, "performance": 2.0}

# Fly's published region multipliers. ewr/iad are the baseline.
_REGION_MARKUPS = {
    "ams": 1.038461538,
    "arn": 1.038461538,
    "bom": 1.076923077,
    "cdg": 1.134615385,
    "dfw": 1.25,
    "ewr": 1.0,
    "fra": 1.153846154,
    "gru": 1.615384615,
    "iad": 1.0,
    "jnb": 1.302884615,
    "lax": 1.199519231,
    "lhr": 1.134615385,
    "nrt": 1.307692308,
    "ord": 1.25,
    "sin": 1.269230769,
    "sjc": 1.192307692,
    "syd": 1.269230769,
    "yyz": 1.115384615,
}

# Exact baseline-region prices for the most common one-CPU presets, from Fly's
# pricing table. The generic calculation below covers custom sizes.
_BASELINE_PRESET_CENTS = {
    ("shared", 1, 256): 194,
    ("shared", 1, 512): 319,
    ("shared", 1, 1024): 570,
    ("shared", 1, 2048): 1070,
    ("performance", 1, 2048): 3100,
    ("performance", 1, 4096): 4101,
    ("performance", 1, 8192): 6102,
}


def _machine_monthly_cents(guest: dict, started: bool, region: str = "iad") -> int:
    cpus = guest.get("cpus", 1)
    cpu_kind = guest.get("cpu_kind", "shared")
    memory_mb = guest.get("memory_mb", 256)
    mem_gb = memory_mb / 1024
    vcpu_rate = _PERF_VCPU_MO if cpu_kind == "performance" else _SHARED_VCPU_MO
    markup = _REGION_MARKUPS.get(region, 1.0)

    preset = _BASELINE_PRESET_CENTS.get((cpu_kind, cpus, memory_mb))
    if preset is not None:
        cents = round(preset * markup)
    else:
        included_ram = _INCLUDED_RAM_GB_PER_CPU.get(cpu_kind, 0) * cpus
        extra_ram = max(0.0, mem_gb - included_ram)
        cents = round((cpus * vcpu_rate + extra_ram * _RAM_GB_MO) * markup * 100)

    # fly bills stopped machines for storage only (~rootfs); approximate as 15%.
    if not started:
        cents = round(cents * 0.15)
    return cents


def _volume_monthly_cents(volumes: list[dict]) -> int:
    return round(sum(v.get("size_gb", 0) for v in volumes) * _VOLUME_GB_MO * 100)


async def _apps_with_resources(
    token: str, org: str
) -> dict[str, dict[str, list[dict]]]:
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=API, headers=headers, timeout=30) as client:
        resp = await client.get("/apps", params={"org_slug": org})
        resp.raise_for_status()
        apps = [a["name"] for a in resp.json().get("apps", [])]

        out: dict[str, dict[str, list[dict]]] = {}
        for app in apps:
            machines = await client.get(f"/apps/{app}/machines")
            machines.raise_for_status()
            volumes = await client.get(f"/apps/{app}/volumes")
            volumes.raise_for_status()
            out[app] = {"machines": machines.json(), "volumes": volumes.json()}
    return out


class FlyConnector:
    name = PROVIDER

    async def collect(self, period: Period) -> list[LineItem]:
        token = os.environ.get("FLY_API_TOKEN")
        if not token:
            raise RuntimeError("FLY_API_TOKEN not set")
        org = os.environ.get("FLY_ORG_SLUG", "personal")

        items: list[LineItem] = []
        for app, resources in (await _apps_with_resources(token, org)).items():
            machines = resources["machines"]
            volumes = resources["volumes"]
            if not machines and not volumes:
                continue
            cents = 0
            sizes: list[str] = []
            for m in machines:
                guest = (m.get("config", {}) or {}).get("guest", {}) or {}
                started = m.get("state") == "started"
                cents += _machine_monthly_cents(guest, started, m.get("region", "iad"))
                sizes.append(
                    f"{guest.get('cpu_kind', '?')}-{guest.get('cpus', '?')}x"
                    f"/{guest.get('memory_mb', '?')}MB"
                )
            volume_gb = sum(v.get("size_gb", 0) for v in volumes)
            cents += _volume_monthly_cents(volumes)
            usage = f"{len(machines)} machine(s): " + ", ".join(sizes)
            if volumes:
                usage += f"; {len(volumes)} volume(s): {volume_gb:g}GB"
            items.append(
                LineItem(
                    provider=PROVIDER,
                    project=project_for(app),
                    service=app,
                    amount=cents,
                    estimated=True,
                    usage=usage,
                    note=(
                        "estimated from machine and volume inventory; excludes bandwidth "
                        "and paid IPs; reconcile with Fly dashboard"
                    ),
                )
            )
        return items
