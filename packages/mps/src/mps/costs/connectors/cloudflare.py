"""Cloudflare connector.

Most of your Cloudflare usage is free tier; the cost that actually accrues is R2
storage plus a few fixed subscriptions/registrations. We list every R2 bucket
(the REST endpoint pages at 20 by default, which is why `wrangler r2 bucket list`
stops there), query stored bytes per bucket via the GraphQL analytics API, apply
the 10 GB account-level free tier, then allocate the paid storage back across
buckets. Fixed costs can be supplied
as resource-shaped JSON so project attribution has something real to match.

Auth: CLOUDFLARE_API_TOKEN (already a secret block in prefect.yaml). The bucket
census needs "Workers R2 Storage: Read" and stored bytes need "Account
Analytics: Read"; without them R2 is reported unmeasured, never $0.
Optional: CLOUDFLARE_ACCOUNT_ID (else the first account is used),
          CLOUDFLARE_FIXED_COSTS_JSON, or legacy CLOUDFLARE_FIXED_USD.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any

import httpx

from mps.costs.projects import project_for
from mps.costs.types import LineItem, Period

REST = "https://api.cloudflare.com/client/v4"
GRAPHQL = "https://api.cloudflare.com/client/v4/graphql"
PROVIDER = "cloudflare"

_R2_GB_MONTH_USD = 0.015
_R2_IA_GB_MONTH_USD = 0.01
_R2_FREE_GB = 10.0

_R2_STORAGE_QUERY = """
query ($account: String!, $start: Time!, $end: Time!) {
  viewer {
    accounts(filter: {accountTag: $account}) {
      r2StorageAdaptiveGroups(
        limit: 1000
        filter: {datetime_geq: $start, datetime_leq: $end}
      ) {
        dimensions { bucketName }
        max { payloadSize metadataSize }
      }
    }
  }
}
"""


async def _account_id(client: httpx.AsyncClient) -> str | None:
    if env := os.environ.get("CLOUDFLARE_ACCOUNT_ID"):
        return env
    # account-scoped API tokens can't list /accounts (returns 404/empty), so
    # this only works for account-or-broader tokens. otherwise set the env var.
    resp = await client.get(f"{REST}/accounts")
    if resp.status_code != 200:
        return None
    accounts = resp.json().get("result") or []
    return accounts[0]["id"] if accounts else None


async def _r2_buckets(client: httpx.AsyncClient, account: str) -> list[str]:
    names: list[str] = []
    cursor: str | None = None
    while True:
        params = {"per_page": "1000", **({"cursor": cursor} if cursor else {})}
        resp = await client.get(f"{REST}/accounts/{account}/r2/buckets", params=params)
        resp.raise_for_status()
        body = resp.json()
        names += [bucket["name"] for bucket in (body.get("result") or {}).get("buckets", [])]
        cursor = (body.get("result_info") or {}).get("cursor")
        if not cursor:
            return sorted(names)


async def _r2_stored_bytes_by_bucket(
    client: httpx.AsyncClient, account: str, period: Period
) -> dict[str, int]:
    resp = await client.post(
        GRAPHQL,
        json={
            "query": _R2_STORAGE_QUERY,
            "variables": {
                "account": account,
                "start": _iso(period.start),
                "end": _iso(period.end),
            },
        },
    )
    resp.raise_for_status()
    body = resp.json()
    # a graphql error means we couldn't measure — must NOT be reported as $0
    if errors := body.get("errors"):
        raise RuntimeError(f"r2 storage query failed: {errors}")
    accounts = (body.get("data", {}) or {}).get("viewer", {}).get("accounts", [])
    if not accounts:
        return {}  # genuinely no account data
    groups = accounts[0].get("r2StorageAdaptiveGroups", [])
    if not groups:
        return {}  # genuinely no stored objects

    buckets: dict[str, int] = {}
    for group in groups:
        name = ((group.get("dimensions") or {}).get("bucketName") or "unknown-r2-bucket").strip()
        mx = group.get("max", {}) or {}
        stored = int(mx.get("payloadSize", 0) or 0) + int(mx.get("metadataSize", 0) or 0)
        buckets[name] = buckets.get(name, 0) + stored
    return buckets


async def _r2_account_metrics(client: httpx.AsyncClient, account: str) -> dict:
    resp = await client.get(f"{REST}/accounts/{account}/r2/metrics")
    resp.raise_for_status()
    return resp.json()["result"]


def _r2_account_line_items(metrics: dict) -> list[LineItem]:
    """One account-wide line from /r2/metrics, used when per-bucket analytics is
    unauthorized. It needs only R2 read, but cannot attribute storage to buckets."""

    def stored_gb(storage_class: str) -> float:
        published = (metrics.get(storage_class) or {}).get("published") or {}
        return (
            int(published.get("payloadSize") or 0) + int(published.get("metadataSize") or 0)
        ) / 1e9

    standard, infrequent = stored_gb("standard"), stored_gb("infrequentAccess")
    cents = round(
        (max(0.0, standard - _R2_FREE_GB) * _R2_GB_MONTH_USD + infrequent * _R2_IA_GB_MONTH_USD)
        * 100
    )
    if standard + infrequent <= 0:
        return []
    return [
        LineItem(
            provider=PROVIDER,
            project="shared",
            service="r2:account",
            amount=cents,
            estimated=True,
            usage=f"{standard:.2f} GB standard, {infrequent:.2f} GB infrequent access",
            note=(
                "account-level R2 storage after 10 GB free tier; not attributed to buckets "
                "(needs Account Analytics: Read); class A/B ops & egress not counted"
            ),
        )
    ]


def _allocate_cents(total_cents: int, weights: dict[str, int]) -> dict[str, int]:
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


def _r2_line_items(stored_by_bucket: dict[str, int]) -> list[LineItem]:
    total_bytes = sum(stored_by_bucket.values())
    if total_bytes <= 0:
        return []

    total_gb = total_bytes / 1_000_000_000
    billable_gb = max(0.0, total_gb - _R2_FREE_GB)
    total_cents = round(billable_gb * _R2_GB_MONTH_USD * 100)
    allocations = _allocate_cents(total_cents, stored_by_bucket)

    items: list[LineItem] = []
    for bucket, cents in sorted(allocations.items()):
        gb = stored_by_bucket[bucket] / 1_000_000_000
        items.append(
            LineItem(
                provider=PROVIDER,
                project=project_for(bucket),
                service=f"{bucket}:r2",
                amount=cents,
                estimated=True,
                usage=f"{gb:.2f} GB stored",
                note=(
                    "allocated share of R2 storage after 10 GB account free tier; "
                    "class A/B ops & egress not counted"
                ),
            )
        )
    return items


def _fixed_line_items() -> list[LineItem]:
    raw = os.environ.get("CLOUDFLARE_FIXED_COSTS_JSON")
    if raw:
        return _fixed_line_items_from_json(raw)

    fixed = float(os.environ.get("CLOUDFLARE_FIXED_USD", "0"))
    if fixed <= 0:
        return []
    return [
        LineItem(
            provider=PROVIDER,
            project="shared",
            service="domains-and-plans",
            amount=round(fixed * 100),
            estimated=False,
            note="fixed domain registrations / paid plans (CLOUDFLARE_FIXED_USD)",
        )
    ]


def _fixed_line_items_from_json(raw: str) -> list[LineItem]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("CLOUDFLARE_FIXED_COSTS_JSON is not valid JSON") from exc

    if not isinstance(data, dict):
        raise RuntimeError("CLOUDFLARE_FIXED_COSTS_JSON must be an object")

    items: list[LineItem] = []
    for service, spec in data.items():
        if isinstance(spec, int | float):
            amount = float(spec)
            project = project_for(service)
            note = "fixed Cloudflare cost (CLOUDFLARE_FIXED_COSTS_JSON)"
        elif isinstance(spec, dict):
            amount = _amount_from_spec(service, spec)
            project = str(spec.get("project") or project_for(service))
            note = str(spec.get("note") or "fixed Cloudflare cost (CLOUDFLARE_FIXED_COSTS_JSON)")
        else:
            raise RuntimeError(f"Cloudflare fixed cost {service!r} must be a number or object")

        if amount <= 0:
            continue
        items.append(
            LineItem(
                provider=PROVIDER,
                project=project,
                service=str(service),
                amount=round(amount * 100),
                estimated=False,
                note=note,
            )
        )
    return items


def _amount_from_spec(service: str, spec: dict[str, Any]) -> float:
    if "amount" in spec:
        return float(spec["amount"])
    if "usd" in spec:
        return float(spec["usd"])
    raise RuntimeError(f"Cloudflare fixed cost {service!r} needs amount or usd")


class CloudflareConnector:
    name = PROVIDER

    async def collect(self, period: Period) -> list[LineItem]:
        self.unmeasured: list[str] = []
        token = os.environ.get("CLOUDFLARE_API_TOKEN")
        if not token:
            raise RuntimeError("CLOUDFLARE_API_TOKEN not set")

        items: list[LineItem] = []
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(headers=headers, timeout=30) as client:
            account = await _account_id(client)
            if not account:
                # don't report a partial/zero CF cost that hides R2 — fail loud.
                raise RuntimeError(
                    "could not resolve cloudflare account id; set CLOUDFLARE_ACCOUNT_ID "
                    "(account-scoped tokens can't list /accounts)"
                )
            # if we can't read R2, OMIT it (never invent a $0 line) and warn —
            # but still report the real fixed costs below.
            try:
                self.r2_buckets = await _r2_buckets(client, account)
            except Exception as exc:
                self.r2_buckets = None
                print(
                    f"  cloudflare: R2 bucket census unavailable ({exc}); "
                    "add 'Workers R2 Storage: Read' to the token"
                )
            try:
                items.extend(
                    _r2_line_items(await _r2_stored_bytes_by_bucket(client, account, period))
                )
            except Exception as exc:
                try:
                    items.extend(_r2_account_line_items(await _r2_account_metrics(client, account)))
                    print(
                        f"  cloudflare: R2 per-bucket analytics unavailable ({exc}); "
                        "reporting account-level storage unattributed"
                    )
                    items.extend(_fixed_line_items())
                    return items
                except Exception:
                    pass
                census = (
                    f"{len(self.r2_buckets)} bucket(s)"
                    if self.r2_buckets is not None
                    else "unknown bucket count"
                )
                self.unmeasured.append(f"R2 storage, {census} ({exc})")
                print(
                    f"  cloudflare: R2 storage UNMEASURED for {census} ({exc}); "
                    "add 'Account Analytics: Read' to the token to include it"
                )

        items.extend(_fixed_line_items())
        return items


def _iso(d: dt.datetime) -> str:
    return d.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
