from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
from decimal import Decimal

import httpx
from pydantic import BaseModel, Field, TypeAdapter

from mps.costs.connectors.builder import archive_client
from mps.costs.types import LineItem, Period

PREFIX = "billing/voyage/bufo/v1/"
QUERY = """
SELECT date_trunc('day', start_timestamp) AS day,
       count(*) AS spans,
       count(*) FILTER (WHERE span_name = 'embedding.generate') AS calls,
       count(*) FILTER (WHERE span_name = 'embedding.generate' AND
         (attributes->>'model' IS DISTINCT FROM 'voyage-multimodal-3' OR
          attributes->>'usage.text_tokens' IS NULL OR
          attributes->>'usage.image_pixels' IS NULL OR
          ((attributes->>'usage.image_pixels')::bigint > 0 AND
           attributes->>'cost.billable_image_pixels' IS NULL))) AS unmetered,
       coalesce(sum(CASE WHEN span_name = 'embedding.generate' AND
         attributes->>'model' = 'voyage-multimodal-3'
         THEN (attributes->>'usage.text_tokens')::bigint ELSE 0 END), 0) AS text_tokens,
       coalesce(sum(CASE WHEN span_name = 'embedding.generate' AND
         attributes->>'model' = 'voyage-multimodal-3'
         THEN (attributes->>'cost.billable_image_pixels')::bigint ELSE 0 END), 0) AS image_pixels
FROM records
WHERE kind = 'span' AND service_name = 'find-bufo'
  AND (span_name = 'embedding.generate' OR span_name LIKE 'HTTP %')
GROUP BY date_trunc('day', start_timestamp)
ORDER BY date_trunc('day', start_timestamp)
"""


class Day(BaseModel):
    day: dt.date
    observed_to: dt.datetime
    spans: int = Field(ge=0)
    calls: int = Field(ge=0)
    unmetered: int = Field(ge=0)
    text_tokens: int = Field(ge=0)
    image_pixels: int = Field(ge=0)


def merge_day(previous: Day | None, current: Day) -> Day:
    if previous is None:
        return current
    if previous.day != current.day:
        raise ValueError("Cannot merge different usage days")
    return Day(
        day=current.day,
        observed_to=max(previous.observed_to, current.observed_to),
        **{
            field: max(getattr(previous, field), getattr(current, field))
            for field in ("spans", "calls", "unmetered", "text_tokens", "image_pixels")
        },
    )


def retain_days(client, bucket: str, observations: list[Day], month: str) -> list[Day]:
    days: dict[dt.date, Day] = {}
    keys = set()
    prefix = PREFIX + month
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            keys.add(item["Key"])
            day = Day.model_validate_json(
                client.get_object(Bucket=bucket, Key=item["Key"])["Body"].read()
            )
            days[day.day] = merge_day(days.get(day.day), day)
    for day in observations:
        body = day.model_dump_json().encode()
        key = f"{PREFIX}{day.day}/{hashlib.sha256(body).hexdigest()}.json"
        if key not in keys:
            client.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
            keys.add(key)
        days[day.day] = merge_day(days.get(day.day), day)
    return list(days.values())


def line_items(days: list[Day], now: dt.datetime) -> tuple[list[LineItem], list[str]]:
    first = now.date().replace(day=1)
    by_day: dict[dt.date, Day] = {}
    for day in days:
        if first <= day.day <= now.date():
            by_day[day.day] = merge_day(by_day.get(day.day), day)
    issues = [
        "Find Bufo telemetry at list price only; account free allowances, credits, other apps and ingestion are not reconciled with billing."
    ]
    missing = []
    day = first
    while day <= now.date():
        if day not in by_day:
            missing.append(day.isoformat())
        day += dt.timedelta(days=1)
    if missing:
        issues.append("no Find Bufo telemetry for " + ", ".join(missing))
    unmetered = sum(day.unmetered for day in by_day.values())
    if unmetered:
        issues.append(
            f"{unmetered} observed calls lack complete supported-model usage; their cost is unknown"
        )
    if not by_day:
        return [], issues
    text_tokens = sum(day.text_tokens for day in by_day.values())
    pixels = sum(day.image_pixels for day in by_day.values())
    usd = (
        Decimal(text_tokens) * Decimal("0.12") / 1_000_000
        + Decimal(pixels) * Decimal("0.60") / 1_000_000_000
    )
    return [
        LineItem(
            provider="voyage",
            project="find-bufo",
            service="find-bufo:embeddings",
            amount=round(usd * 100),
            estimated=True,
            basis="month_to_date",
            usage=f"{text_tokens} text tokens, {pixels} billable image pixels; ${usd:f} before free allowances",
            note="Observed find-bufo service spans for voyage-multimodal-3 this UTC month, retained daily. List prices verified 2026-11-11: $0.12/M text tokens and $0.60/B pixels. Not an invoice or forecast; account free allowances can make the actual charge $0. Excludes other applications, ingestion and missing telemetry.",
        )
    ], issues


class VoyageConnector:
    name = "voyage"

    def __init__(self, block_name: str = "logfire-read-token"):
        self.block_name = block_name
        self.unmeasured: list[str] = []

    async def collect(self, period: Period) -> list[LineItem]:
        from mps.blocks import secret

        now = period.end.astimezone(dt.UTC)
        first = max(
            now.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
            now.replace(hour=0, minute=0, second=0, microsecond=0) - dt.timedelta(days=13),
        )
        token = await secret(self.block_name)
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                "https://logfire-us.pydantic.dev/v2/query",
                headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
                json={
                    "sql": QUERY,
                    "min_timestamp": first.isoformat(),
                    "max_timestamp": now.isoformat(),
                    "limit": 100,
                },
            )
            response.raise_for_status()
        rows = response.json()["data"]
        if len(rows) >= 100:
            raise ValueError("Voyage usage query truncated")
        observations = TypeAdapter(list[Day]).validate_python(
            [
                {**row, "day": dt.datetime.fromisoformat(row["day"]).date(), "observed_to": now}
                for row in rows
            ]
        )

        def retain():
            client, bucket = archive_client()
            return retain_days(client, bucket, observations, now.strftime("%Y-%m"))

        days = await asyncio.to_thread(retain)
        items, self.unmeasured = line_items(days, now)
        return items
