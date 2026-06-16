"""Cloudflare connector.

Most of your Cloudflare usage is free tier; the cost that actually accrues is R2
storage. We query stored bytes via the GraphQL analytics API and price it at the
standard storage rate ($0.015/GB-month), then add a configurable fixed amount for
domain registrations / paid plans that have no usage API.

Auth: CLOUDFLARE_API_TOKEN (already a secret block in prefect.yaml).
Optional: CLOUDFLARE_ACCOUNT_ID (else the first account is used),
          CLOUDFLARE_FIXED_USD (domains/plans, default 0).
"""

from __future__ import annotations

import datetime as dt
import os

import httpx

from mps.costs.types import LineItem, Period

REST = "https://api.cloudflare.com/client/v4"
GRAPHQL = "https://api.cloudflare.com/client/v4/graphql"
PROVIDER = "cloudflare"

_R2_GB_MONTH_USD = 0.015

_R2_STORAGE_QUERY = """
query ($account: String!, $start: Time!, $end: Time!) {
  viewer {
    accounts(filter: {accountTag: $account}) {
      r2StorageAdaptiveGroups(
        limit: 1
        filter: {datetime_geq: $start, datetime_leq: $end}
      ) {
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


async def _r2_stored_bytes(client: httpx.AsyncClient, account: str, period: Period) -> int:
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
        return 0  # genuinely no account data
    groups = accounts[0].get("r2StorageAdaptiveGroups", [])
    if not groups:
        return 0  # genuinely no stored objects
    mx = groups[0].get("max", {})
    return int(mx.get("payloadSize", 0)) + int(mx.get("metadataSize", 0))


class CloudflareConnector:
    name = PROVIDER

    async def collect(self, period: Period) -> list[LineItem]:
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
            # R2 storage analytics needs "Account Analytics: Read" on the token.
            # if we can't read it, OMIT r2 (never invent a $0 line) and warn —
            # but still report the real fixed costs below.
            try:
                stored = await _r2_stored_bytes(client, account, period)
                gb = stored / 1_000_000_000
                cents = round(gb * _R2_GB_MONTH_USD * 100)
                if cents > 0:
                    items.append(
                        LineItem(
                            provider=PROVIDER,
                            project="shared",
                            service="r2-storage",
                            amount=cents,
                            estimated=True,
                            usage=f"{gb:.2f} GB stored",
                            note="r2 storage at $0.015/GB-mo; class A/B ops & egress not counted",
                        )
                    )
            except Exception as exc:
                print(
                    f"  cloudflare: R2 storage UNMEASURED ({exc}); "
                    "add 'Account Analytics: Read' to the token to include it"
                )

        fixed = float(os.environ.get("CLOUDFLARE_FIXED_USD", "0"))
        if fixed > 0:
            items.append(
                LineItem(
                    provider=PROVIDER,
                    project="shared",
                    service="domains-and-plans",
                    amount=round(fixed * 100),
                    estimated=False,
                    note="fixed domain registrations / paid plans (CLOUDFLARE_FIXED_USD)",
                )
            )
        return items


def _iso(d: dt.datetime) -> str:
    return d.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
