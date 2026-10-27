"""Durable non-secret lifecycle receipts for disposable Hetzner builders."""

from __future__ import annotations

import datetime as dt
import json
import math
import os
from decimal import Decimal
from pathlib import Path


def record(directory: Path, server: dict, pricing: dict, phase: str) -> Path:
    location = (server.get("location") or {}).get("name") or server["datacenter"]["location"][
        "name"
    ]
    server_price = next(
        price
        for entry in pricing["server_types"]
        if entry["id"] == server["server_type"]["id"]
        for price in entry["prices"]
        if price["location"] == location
    )
    ip_price = next(
        price
        for entry in pricing["primary_ips"]
        if entry["type"] == "ipv4"
        for price in entry["prices"]
        if price["location"] == location
    )
    now = dt.datetime.now(dt.UTC).isoformat()
    item = {
        "schema": 1,
        "id": server["id"],
        "name": server["name"],
        "flow_run_id": server["labels"]["flow-run"],
        "project": "typeahead",
        "created_at": server["created"],
        "observed_at": now,
        "phase": phase,
        "currency": pricing["currency"],
        "server_type": server["server_type"]["name"],
        "location": location,
        "server_price": server_price,
        "ipv4_price": ip_price,
        "method": "provider creation timestamp; deletion observation is an upper bound; hourly rounding and monthly caps; excludes traffic overage",
    }
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    filename = f"{server['id']}-{phase}.json"
    path = directory / filename
    if path.exists():
        return path
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as file:
        json.dump(item, file, sort_keys=True)
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)
    return path


def amount_usd(record: dict, end: dt.datetime) -> Decimal:
    if record["currency"] != "USD":
        raise ValueError("Builder receipt is not denominated in USD")
    start = dt.datetime.fromisoformat(record["created_at"].replace("Z", "+00:00"))
    # Split by calendar month so monthly caps apply independently.
    cost = Decimal(0)
    while start < end:
        boundary = (start.replace(day=28) + dt.timedelta(days=4)).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        stop = min(boundary, end)
        hours = math.ceil((stop - start).total_seconds() / 3600)
        for price in (record["server_price"], record["ipv4_price"]):
            cost += min(
                Decimal(price["price_monthly"]["gross"]),
                Decimal(price["price_hourly"]["gross"]) * hours,
            )
        start = stop
    return cost


def merge_records(records: list[dict]) -> list[dict]:
    grouped = {}
    for receipt in records:
        sid = receipt["id"]
        previous = grouped.get(sid)
        if previous is None or (
            receipt["phase"] == "deleted"
            and (previous["phase"] != "deleted" or receipt["observed_at"] < previous["observed_at"])
        ):
            grouped[sid] = receipt
    return list(grouped.values())
