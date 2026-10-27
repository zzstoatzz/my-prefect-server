"""Month-to-date disposable builder charges from durable lifecycle receipts."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import boto3
from dotenv import dotenv_values

from mps.builder_billing import amount_usd, merge_records
from mps.costs.types import LineItem, Period

PREFIX = "billing/hetzner-builders/"


def archive_client():
    config = dotenv_values(Path.home() / ".config/prefect-home/r2.env")
    return boto3.client(
        "s3",
        endpoint_url=config["RCLONE_CONFIG_R2_ENDPOINT"],
        aws_access_key_id=config["RCLONE_CONFIG_R2_ACCESS_KEY_ID"],
        aws_secret_access_key=config["RCLONE_CONFIG_R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    ), config["PREFECT_HOME_R2_BUCKET"]


def receipts() -> list[dict]:
    local = Path(
        os.environ.get(
            "BUILDER_BILLING_DIRECTORY", str(Path.home() / "hcloud-builder/state/billing")
        )
    )
    result = [json.loads(path.read_text()) for path in local.glob("*.json")]
    client, bucket = archive_client()
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=PREFIX):
        for item in page.get("Contents", []):
            if item["Key"].endswith(".json"):
                result.append(
                    json.loads(client.get_object(Bucket=bucket, Key=item["Key"])["Body"].read())
                )
    if not result:
        raise RuntimeError("No builder billing receipts; coverage has not been established")
    return merge_records(result)


class BuilderCostsConnector:
    name = "hetzner-builders"

    async def collect(self, period: Period) -> list[LineItem]:
        import asyncio

        records = await asyncio.to_thread(receipts)
        now = period.end
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        from decimal import Decimal

        total = Decimal(0)
        covered_since = now
        active = 0
        count = 0
        self.unmeasured = []
        for record in records:
            observed = dt.datetime.fromisoformat(record["observed_at"].replace("Z", "+00:00"))
            end = observed if record["phase"] == "deleted" else now
            if end <= month_start:
                continue
            created = dt.datetime.fromisoformat(record["created_at"].replace("Z", "+00:00"))
            covered_since = min(covered_since, max(created, month_start))
            scoped = {**record, "created_at": max(created, month_start).isoformat()}
            total += amount_usd(scoped, min(end, now))
            count += 1
            active += record["phase"] != "deleted"
        return [
            LineItem(
                provider="hetzner",
                project="typeahead",
                service="typeahead:ephemeral-builds",
                amount=round(total * 100),
                estimated=True,
                basis="month_to_date",
                usage=f"${total:f} observed this UTC month; {count} VM(s), {active} not yet confirmed deleted; coverage since {covered_since.isoformat()}",
                note="Current UTC month-to-date spend from durable VM and IPv4 lifecycle receipts over the stated coverage window. Hourly list prices and monthly caps; deletion observation bounds may overestimate time. Includes commissioning runs; not extrapolated into recurring spend. Not an invoice. Qualification/backfill coverage is documented in docs/costs.md; excludes traffic overage.",
            )
        ]
