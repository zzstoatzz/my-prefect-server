"""Durable daily Fly compute observations, including deleted Machines."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
from decimal import Decimal

import httpx

from mps.costs.connectors.builder import archive_client
from mps.costs.projects import project_for
from mps.costs.types import LineItem

PREFIX = "billing/fly-compute/v1/"
SAMPLE_SECONDS = 15
MONTH_SECONDS = 30 * 86400
DIMENSIONS = ("app", "instance", "region", "cpu_kind", "cpu_count", "memory_mb")


def is_ad_hoc(machine: dict) -> bool:
    config = machine.get("config") or {}
    return bool(config.get("auto_destroy")) or (
        config.get("metadata", {}).get("mps.cost_basis") == "month_to_date"
    )


def usage_rows(result: dict, seconds: int) -> list[dict]:
    if result.get("status") != "success" or result.get("isPartial"):
        raise RuntimeError("Fly compute usage query failed or returned partial data")
    if result["data"]["resultType"] != "vector":
        raise ValueError("Expected Fly compute usage vector")
    rows = []
    for series in result["data"]["result"]:
        labels = series["metric"]
        row = {key: labels[key] for key in DIMENSIONS}
        if row["cpu_kind"] not in {"shared", "performance"}:
            raise ValueError("Unknown Fly CPU kind")
        if int(row["cpu_count"]) <= 0 or int(row["memory_mb"]) <= 0:
            raise ValueError("Invalid Fly Machine size")
        samples = Decimal(series["value"][1])
        if not samples.is_finite() or samples < 0:
            raise ValueError("Invalid Fly sample count")
        row["seconds"] = min(seconds, int(samples * SAMPLE_SECONDS))
        rows.append(row)
    return rows


def merge_day(previous: dict | None, observation: dict) -> dict:
    if previous and previous["day"] != observation["day"]:
        raise ValueError("Cannot merge different Fly usage days")
    rows = {}
    for source in (previous, observation):
        if not source:
            continue
        for row in source["rows"]:
            key = tuple(row[key] for key in DIMENSIONS)
            old = rows.get(key)
            rows[key] = {
                **row,
                "seconds": max(row["seconds"], old["seconds"] if old else 0),
                "ad_hoc": row["ad_hoc"] or bool(old and old["ad_hoc"]),
            }
    return {
        "day": observation["day"],
        "observed_to": max(observation["observed_to"], previous["observed_to"] if previous else ""),
        "rows": list(rows.values()),
    }


def line_items(
    days: list[dict], now: dt.datetime, monthly_price
) -> tuple[list[LineItem], list[str]]:
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    scoped = {
        day["day"]: day
        for day in days
        if month_start.date().isoformat() <= day["day"] <= now.date().isoformat()
    }
    missing = []
    day = month_start
    while day < now:
        observed = scoped.get(day.date().isoformat())
        end = min(day + dt.timedelta(days=1), now)
        if not observed or dt.datetime.fromisoformat(observed["observed_to"]) < end:
            missing.append(day.date().isoformat())
        day += dt.timedelta(days=1)
    totals: dict[str, Decimal] = {}
    machines: dict[str, set[str]] = {}
    for observation in scoped.values():
        for row in observation["rows"]:
            if not row["ad_hoc"]:
                continue
            app = row["app"]
            guest = {
                "cpu_kind": row["cpu_kind"],
                "cpus": int(row["cpu_count"]),
                "memory_mb": int(row["memory_mb"]),
            }
            amount = Decimal(monthly_price(guest, row["region"])) * row["seconds"] / MONTH_SECONDS
            totals[app] = totals.get(app, Decimal(0)) + amount
            machines.setdefault(app, set()).add(row["instance"])
    amounts = {app: int(amount) for app, amount in totals.items()}
    remaining = round(sum(totals.values())) - sum(amounts.values())
    for app in sorted(totals, key=lambda app: (totals[app] - amounts[app], app), reverse=True)[
        :remaining
    ]:
        amounts[app] += 1
    items = [
        LineItem(
            provider="fly",
            project=project_for(app),
            service=f"{app}:ad-hoc-compute",
            amount=amounts[app],
            estimated=True,
            basis="month_to_date",
            usage=f"{len(machines[app])} Machine(s); observed compute since {month_start.date()} UTC",
            note=(
                "Ad hoc compute this UTC month from retained 15-second Fly metrics, including deleted Machines. "
                "Auto-destroy Machines, explicitly marked usage, and apps without continuing Machines; no monthly extrapolation. "
                "Estimated at list prices; sampling can miss short runs. Excludes storage, network, taxes and credits; not an invoice."
                + (" Coverage incomplete: " + ", ".join(missing) + "." if missing else "")
            ),
        )
        for app, amount in sorted(totals.items())
    ]
    gaps = ["ad hoc compute day coverage: " + ", ".join(missing)] if missing else []
    return items, gaps


def retain_days(client, bucket: str, org: str, observations: list[dict], month: str) -> list[dict]:
    prefix = f"{PREFIX}{org}/{month}-"
    days = {}
    keys = set()
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.add(obj["Key"])
            body = json.loads(client.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read())
            days[body["day"]] = merge_day(days.get(body["day"]), body)
    for observation in observations:
        merged = merge_day(days.get(observation["day"]), observation)
        body = json.dumps(observation, sort_keys=True).encode()
        digest = hashlib.sha256(body).hexdigest()
        key = f"{PREFIX}{org}/{observation['day']}/{digest}.json"
        if key not in keys:
            client.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
            keys.add(key)
        days[merged["day"]] = merged
    return list(days.values())


async def collect_usage(token: str, org: str, resources: dict, now: dt.datetime, monthly_price):
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    first_day = max(
        month_start, now.replace(hour=0, minute=0, second=0, microsecond=0) - dt.timedelta(days=6)
    )
    recurring_apps = {
        app
        for app, inventory in resources.items()
        if any(not is_ad_hoc(machine) for machine in inventory["machines"])
    }
    disposable_ids = {
        machine["id"]
        for inventory in resources.values()
        for machine in inventory["machines"]
        if is_ad_hoc(machine)
    }
    auth = token if token.startswith("FlyV1 ") else "FlyV1 " + token
    observations = []
    async with httpx.AsyncClient(timeout=60) as client:
        day = first_day
        while day < now:
            end = min(day + dt.timedelta(days=1), now)
            seconds = int((end - day).total_seconds())
            query = (
                "sum by ("
                + ",".join(DIMENSIONS)
                + f")(count_over_time(fly_instance_info[{seconds}s]))"
            )
            response = await client.get(
                f"https://api.fly.io/prometheus/{org}/api/v1/query",
                headers={"Authorization": auth},
                params={"query": query, "time": end.timestamp()},
            )
            response.raise_for_status()
            rows = usage_rows(response.json(), seconds)
            for row in rows:
                row["ad_hoc"] = (
                    row["app"] not in recurring_apps or row["instance"] in disposable_ids
                )
            observations.append(
                {"day": day.date().isoformat(), "observed_to": end.isoformat(), "rows": rows}
            )
            day += dt.timedelta(days=1)

    def retain():
        client, bucket = archive_client()
        return retain_days(client, bucket, org, observations, month_start.strftime("%Y-%m"))

    days = await asyncio.to_thread(retain)
    return line_items(days, now, monthly_price)
