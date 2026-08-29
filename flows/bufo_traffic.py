"""Roll find-bufo.com request traffic from logfire into one PDS record per day.

Hourly. Each run re-queries a trailing window (default 2 days) and upserts
every day it touches at rkey=YYYY-MM-DD, so re-runs overwrite cleanly and the
current day converges as its hours fill in. `days_back` is the only knob: the
first run used 15 to backfill everything logfire still held (2026-08-15 on).

The record is what the bot stats page draws "requests all time" and the
per-bucket rate from — it reads `io.zzstoatzz.bufo.traffic` straight off the
public PDS, so this flow is the whole pipeline.

Read token: `LOGFIRE_READ_TOKEN` (Secret block `logfire-read-token`, project
prefect/bufo, minted with `logfire read-tokens create`). Write creds: the
operator-atproto-creds block, same as `costs`.

ad hoc:  uv run python flows/bufo_traffic.py               # dry-run, prints days
         uv run python flows/bufo_traffic.py --write --days 15
"""

from __future__ import annotations

import datetime as dt
import json
import os

import httpx
from atproto import AsyncClient
from pydantic import BaseModel, Field

from prefect import flow, task
from prefect.artifacts import create_table_artifact
from prefect.blocks.system import Secret
from prefect.cache_policies import NONE

from pdsx._internal.auth import login

from mps.bufo_traffic import COLLECTION, QUERY, DayTraffic, parse_rows, rollup
from mps.observability import configure_logfire

LOGFIRE_QUERY_URL = "https://logfire-us.pydantic.dev/v2/query"
QUERY_ROW_LIMIT = 10_000
OPERATOR_CREDS_BLOCK = "operator-atproto-creds"


class TrafficConfig(BaseModel):
    dry_run: bool = Field(
        default=True,
        description="print the day records instead of writing them to PDS",
        json_schema_extra=dict(position=0),
    )
    days_back: int = Field(
        default=2,
        ge=1,
        le=14,
        description="trailing window to re-roll, in days (logfire caps a query at 14)",
    )


@task(cache_policy=NONE, retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
async def query_logfire(since: dt.datetime, until: dt.datetime) -> dict[dt.date, DayTraffic]:
    token = os.environ["LOGFIRE_READ_TOKEN"]
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            LOGFIRE_QUERY_URL,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            json={
                "sql": QUERY,
                "min_timestamp": since.isoformat(),
                "max_timestamp": until.isoformat(),
                "limit": QUERY_ROW_LIMIT,
            },
        )
        resp.raise_for_status()
    rows = parse_rows(resp.json())
    print(f"  logfire: {len(rows)} (hour, route, status) rows since {since.isoformat()}")
    if len(rows) >= QUERY_ROW_LIMIT:
        # a truncated result would publish an undercount that looks complete
        raise RuntimeError(f"logfire query hit the {QUERY_ROW_LIMIT}-row cap; narrow days_back")
    return rollup(rows)


async def _operator_creds() -> tuple[str, str, str]:
    raw = (await Secret.load(OPERATOR_CREDS_BLOCK)).get()
    if isinstance(raw, dict) and "handle" not in raw and "value" in raw:
        raw = raw["value"]
    creds = json.loads(raw) if isinstance(raw, str) else raw
    return creds["handle"], creds["password"], creds["pds"]


@task(cache_policy=NONE, retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
async def write_days(days: list[DayTraffic], generated_at: dt.datetime) -> list[str]:
    handle, password, pds = await _operator_creds()
    client = AsyncClient(base_url=pds)
    await login(client, handle, password, silent=True, required=True)
    uris: list[str] = []
    for day in days:
        resp = await client.com.atproto.repo.put_record(
            {
                "repo": client.me.did,
                "collection": COLLECTION,
                "rkey": day.rkey,
                "record": day.to_record(generated_at),
            }
        )
        uris.append(resp.uri)
    print(f"  wrote {len(uris)} day record(s): {days[0].rkey} .. {days[-1].rkey}")
    return uris


@flow(name="bufo-traffic", log_prints=True, timeout_seconds=600)
async def bufo_traffic(config: TrafficConfig | None = None):
    """Roll the trailing window of find-bufo.com requests into per-day PDS records."""
    configure_logfire("prefect-flow-bufo-traffic")
    config = config or TrafficConfig()

    now = dt.datetime.now(dt.UTC)
    since = (now - dt.timedelta(days=config.days_back)).replace(hour=0, minute=0, second=0, microsecond=0)
    print(f"rolling find-bufo.com traffic {since.date()} .. {now.date()}")

    days = sorted((await query_logfire(since, now)).values(), key=lambda d: d.day)
    if not days:
        print("no requests in window — nothing to write")
        return []

    create_table_artifact(
        key="bufo-traffic",
        table=[
            {
                "day": d.rkey,
                "requests": d.total,
                "search": d.by_route.get("/api/search", 0),
                "images (/e/)": d.by_route.get("/e/{name}", 0),
                "5xx": d.by_status.get("5xx", 0),
            }
            for d in days
        ],
        description=f"find-bufo.com requests per day, {days[0].rkey} .. {days[-1].rkey}",
    )

    if config.dry_run:
        for d in days:
            print(json.dumps(d.to_record(now)))
        return [d.rkey for d in days]

    return await write_days(days, now)


if __name__ == "__main__":
    import asyncio
    import sys

    days_back = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 2
    asyncio.run(bufo_traffic(TrafficConfig(dry_run="--write" not in sys.argv, days_back=days_back)))
