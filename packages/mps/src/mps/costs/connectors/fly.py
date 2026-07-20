"""Fly.io connector.

Fly has no stable public per-app billing endpoint, so we inventory running
machines, volumes, and volume snapshots via the Machines API and estimate their
monthly list-price cost. Each billing category gets its own line item so storage
is attributed to the app that owns it. Every line item is flagged estimated=True
— treat it as a current run-rate, not an invoice, and reconcile it against the
Fly dashboard. Bandwidth, paid IPs, and stopped-Machine rootfs are not counted.

Auth: FLY_API_TOKEN. Org via FLY_ORG_SLUG (default "personal").
"""

from __future__ import annotations

import asyncio
import os

import httpx

from mps.costs.projects import project_for
from mps.costs.types import LineItem, Period

API = "https://api.machines.dev/v1"
PROVIDER = "fly"

# Fly compute rates (USD/month), July 2026. shared vCPU and RAM are billed
# separately; performance vCPUs cost ~16x a shared one.
_SHARED_VCPU_MO = 2.02
_PERF_VCPU_MO = 32.19
_RAM_GB_MO = 5.00
_VOLUME_GB_MO = 0.15
_SNAPSHOT_GB_MO = 0.08
_SNAPSHOT_FREE_BYTES = 10_000_000_000

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

# Exact baseline-region prices for common presets, from Fly's pricing table.
# The generic calculation below covers custom sizes.
_BASELINE_PRESET_CENTS = {
    ("shared", 1, 256): 202,
    ("shared", 1, 512): 332,
    ("shared", 1, 1024): 592,
    ("shared", 1, 2048): 1111,
    ("shared", 2, 512): 404,
    ("shared", 2, 1024): 664,
    ("shared", 2, 2048): 1183,
    ("shared", 2, 4096): 2222,
    ("shared", 4, 1024): 808,
    ("shared", 4, 2048): 1327,
    ("shared", 4, 4096): 2366,
    ("shared", 4, 8192): 4444,
    ("performance", 1, 2048): 3219,
    ("performance", 1, 4096): 4258,
    ("performance", 1, 8192): 6336,
    ("performance", 2, 4096): 6439,
    ("performance", 2, 8192): 8517,
    ("performance", 2, 16384): 12672,
}


def _machine_monthly_cents(guest: dict, region: str = "iad") -> int:
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

    return cents


def _volume_monthly_cents(volumes: list[dict]) -> int:
    return round(
        sum(
            v.get("size_gb", 0)
            for v in volumes
            if v.get("state") != "pending_destroy"
        )
        * _VOLUME_GB_MO
        * 100
    )


def _allocate_cents(total_cents: int, weights: dict[str, int]) -> dict[str, int]:
    """Allocate an account-level charge without losing cents to rounding."""
    total_weight = sum(weights.values())
    if total_cents <= 0 or total_weight <= 0:
        return {}

    allocations: dict[str, int] = {}
    remainders: list[tuple[int, str]] = []
    assigned = 0
    for key, weight in weights.items():
        raw = total_cents * weight
        cents, remainder = divmod(raw, total_weight)
        allocations[key] = cents
        assigned += cents
        remainders.append((remainder, key))
    for _, key in sorted(remainders, reverse=True)[: total_cents - assigned]:
        allocations[key] += 1
    return {key: cents for key, cents in allocations.items() if cents > 0}


def _snapshot_monthly_cents_by_app(
    resources_by_app: dict[str, dict[str, list[dict]]],
) -> dict[str, int]:
    """Apply Fly's 10 GB organization free tier once, then attribute it."""
    stored_by_app = {
        app: sum(int(s.get("size", 0) or 0) for s in resources.get("snapshots", []))
        for app, resources in resources_by_app.items()
    }
    total_bytes = sum(stored_by_app.values())
    billable_bytes = max(0, total_bytes - _SNAPSHOT_FREE_BYTES)
    total_cents = round(billable_bytes / 1_000_000_000 * _SNAPSHOT_GB_MO * 100)
    return _allocate_cents(total_cents, stored_by_app)


async def _apps_with_resources(
    token: str, org: str
) -> dict[str, dict[str, list[dict]]]:
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=API, headers=headers, timeout=30) as client:
        resp = await client.get("/apps", params={"org_slug": org})
        resp.raise_for_status()
        apps = [a["name"] for a in resp.json().get("apps", [])]

        # Keep collection comfortably inside the flow timeout without flooding
        # the Machines API: apps are independent, but all requests share a
        # modest concurrency cap.
        semaphore = asyncio.Semaphore(8)

        async def get_json(path: str) -> list[dict]:
            async with semaphore:
                response = await client.get(path)
                response.raise_for_status()
                return response.json()

        async def app_resources(app: str) -> tuple[str, dict[str, list[dict]]]:
            machines, app_volumes = await asyncio.gather(
                get_json(f"/apps/{app}/machines"),
                get_json(f"/apps/{app}/volumes"),
            )
            snapshot_groups = await asyncio.gather(
                *(
                    get_json(f"/apps/{app}/volumes/{volume['id']}/snapshots")
                    for volume in app_volumes
                )
            )
            return app, {
                "machines": machines,
                "volumes": app_volumes,
                "snapshots": [snapshot for group in snapshot_groups for snapshot in group],
            }

        return dict(await asyncio.gather(*(app_resources(app) for app in apps)))


class FlyConnector:
    name = PROVIDER

    async def collect(self, period: Period) -> list[LineItem]:
        token = os.environ.get("FLY_API_TOKEN")
        if not token:
            raise RuntimeError("FLY_API_TOKEN not set")
        org = os.environ.get("FLY_ORG_SLUG", "personal")

        resources_by_app = await _apps_with_resources(token, org)
        snapshot_cents = _snapshot_monthly_cents_by_app(resources_by_app)
        items: list[LineItem] = []
        stopped_count = 0
        for app, resources in resources_by_app.items():
            machines = resources["machines"]
            volumes = resources["volumes"]
            snapshots = resources["snapshots"]
            started_machines = [m for m in machines if m.get("state") == "started"]
            stopped = len(machines) - len(started_machines)
            stopped_count += stopped

            compute_cents = 0
            sizes: list[str] = []
            for m in started_machines:
                guest = (m.get("config", {}) or {}).get("guest", {}) or {}
                compute_cents += _machine_monthly_cents(guest, m.get("region", "iad"))
                sizes.append(
                    f"{guest.get('cpu_kind', '?')}-{guest.get('cpus', '?')}x"
                    f"/{guest.get('memory_mb', '?')}MB"
                )
            if compute_cents:
                usage = f"{len(started_machines)} started machine(s): " + ", ".join(sizes)
                if stopped:
                    usage += f"; {stopped} stopped machine rootfs unmeasured"
                items.append(
                    LineItem(
                        provider=PROVIDER,
                        project=project_for(app),
                        service=f"{app}:compute",
                        amount=compute_cents,
                        estimated=True,
                        usage=usage,
                        note=(
                            "current started-machine inventory extrapolated at July 2026 "
                            "list prices; excludes stopped rootfs and actual uptime"
                        ),
                    )
                )

            billable_volumes = [
                volume for volume in volumes if volume.get("state") != "pending_destroy"
            ]
            volume_gb = sum(v.get("size_gb", 0) for v in billable_volumes)
            if billable_volumes:
                unattached = sum(
                    not v.get("attached_machine_id") for v in billable_volumes
                )
                usage = f"{len(billable_volumes)} volume(s): {volume_gb:g} GB"
                if unattached:
                    usage += f"; {unattached} unattached"
                items.append(
                    LineItem(
                        provider=PROVIDER,
                        project=project_for(app),
                        service=f"{app}:volumes",
                        amount=_volume_monthly_cents(billable_volumes),
                        estimated=True,
                        usage=usage,
                        note="provisioned capacity at $0.15/GB-month; billed even unattached",
                    )
                )

            if cents := snapshot_cents.get(app, 0):
                stored_bytes = sum(int(s.get("size", 0) or 0) for s in snapshots)
                items.append(
                    LineItem(
                        provider=PROVIDER,
                        project=project_for(app),
                        service=f"{app}:snapshots",
                        amount=cents,
                        estimated=True,
                        usage=f"{len(snapshots)} snapshot(s): {stored_bytes / 1_000_000_000:.2f} GB stored",
                        note=(
                            "allocated share of snapshot storage after Fly's 10 GB "
                            "organization free tier"
                        ),
                    )
                )

        if stopped_count:
            print(
                f"  fly: {stopped_count} stopped machine rootfs cost(s) UNMEASURED; "
                "Machines API does not report billable rootfs bytes"
            )
        return items
