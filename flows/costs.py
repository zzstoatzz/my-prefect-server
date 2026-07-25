"""Collect infrastructure costs across providers and write a daily snapshot to PDS.

Runs connectors as independently retried tasks, aggregates LineItems into an
io.zzstoatzz.cost.snapshot record, and upserts it at rkey=YYYY-MM-DD so re-runs
on the same day overwrite cleanly. A snapshot is published only when every
provider succeeds; an unavailable provider must never masquerade as a $0 bill.

Provider tokens are injected as env vars from Secret blocks via prefect.yaml;
connector code reads os.environ directly. The evergreen site renders the result
straight from the public PDS (com.atproto.repo.listRecords).

ad hoc:  uv run python flows/costs.py            # dry-run, prints JSON
         uv run python flows/costs.py --write     # collect + write to PDS
"""

from __future__ import annotations

import datetime as dt
import json

from atproto import AsyncClient
from pydantic import BaseModel, Field

from prefect import flow, task
from prefect.artifacts import create_table_artifact
from prefect.blocks.system import Secret
from prefect.cache_policies import NONE

from pdsx._internal.auth import login

from mps.costs import Period, Snapshot
from mps.costs.connectors import (
    CloudflareConnector,
    FlyConnector,
    HetznerConnector,
    NeonConnector,
)
from mps.costs.types import Connector, LineItem
from mps.observability import configure_logfire

COLLECTION = "io.zzstoatzz.cost.snapshot"
HETZNER_TOKENS_BLOCK = "hetzner-tokens"
HETZNER_ROBOT_BLOCK = "hetzner-robot"


async def build_connectors() -> list[Connector]:
    """Assemble connectors. Hetzner Cloud tokens are project-scoped, so
    `hetzner-tokens` is a dict of {label: token}. `hetzner-robot` carries
    dedicated-server Webservice credentials plus exact contracted monthly USD
    prices, because auction inventory does not expose its winning price. The
    other providers read env injected from Secret blocks in prefect.yaml."""
    hetzner_tokens: list[str] | None = None
    robot_credentials: tuple[str, str] | None = None
    robot_monthly_usd: dict[str, float] = {}
    try:
        hetzner_tokens = list((await Secret.load(HETZNER_TOKENS_BLOCK)).get().values())
    except Exception as exc:
        print(
            f"  hetzner: no {HETZNER_TOKENS_BLOCK} block ({exc}); will fall back to env"
        )

    try:
        robot = (await Secret.load(HETZNER_ROBOT_BLOCK)).get()
        robot = json.loads(robot) if isinstance(robot, str) else robot
        robot_credentials = (robot["username"], robot["password"])
        robot_monthly_usd = robot.get("monthly_usd", {})
    except Exception as exc:
        print(f"  hetzner: no {HETZNER_ROBOT_BLOCK} block ({exc}); Robot inventory omitted")

    return [
        FlyConnector(),
        CloudflareConnector(),
        HetznerConnector(
            tokens=hetzner_tokens,
            robot_credentials=robot_credentials,
            robot_monthly_usd=robot_monthly_usd,
        ),
        NeonConnector(),
    ]


class CostsConfig(BaseModel):
    dry_run: bool = Field(
        default=True,
        description="print the snapshot JSON instead of writing it to PDS",
        json_schema_extra=dict(position=0),
    )


@task(cache_policy=NONE, retries=3, retry_delay_seconds=[2, 10, 30])
async def collect_connector(connector: Connector, period: Period) -> list[LineItem]:
    """Collect one provider, retrying failures at the task level.

    Exceptions deliberately propagate. Returning [] on failure makes an
    unavailable provider indistinguishable from a real $0 bill and allows a
    partial snapshot to replace the last complete one.
    """
    items = await connector.collect(period)
    total = sum(i.amount for i in items) / 100
    print(f"  {connector.name}: {len(items)} line item(s), ${total:.2f}")
    return items


OPERATOR_CREDS_BLOCK = "operator-atproto-creds"


async def _operator_creds() -> tuple[str, str, str]:
    """Load the operator (zzstoatzz.io) handle, app password, and PDS host from
    the operator-atproto-creds Secret block — a JSON dict {handle, password, pds}.
    This is the main identity on the self-hosted pds.zzstoatzz.io, distinct from
    the phi agent creds used elsewhere."""
    raw = (await Secret.load(OPERATOR_CREDS_BLOCK)).get()
    if isinstance(raw, dict) and "handle" not in raw and "value" in raw:
        raw = raw["value"]  # unwrap prefect json-kind wrapper
    creds = json.loads(raw) if isinstance(raw, str) else raw
    return creds["handle"], creds["password"], creds["pds"]


@task(cache_policy=NONE)
async def write_snapshot(snapshot: Snapshot) -> str:
    """Upsert today's snapshot at rkey=YYYY-MM-DD."""
    handle, password, pds = await _operator_creds()

    # point the client at the operator's own PDS so the session token is issued
    # by it (else self-hosted accounts hit BadJwtSignature via the entryway).
    client = AsyncClient(base_url=pds)
    await login(client, handle, password, silent=True, required=True)

    rkey = snapshot.generated_at.astimezone(dt.UTC).strftime("%Y-%m-%d")
    resp = await client.com.atproto.repo.put_record(
        {
            "repo": client.me.did,
            "collection": COLLECTION,
            "rkey": rkey,
            "record": snapshot.to_record(),
        }
    )
    print(f"wrote {resp.uri}")
    return resp.uri


@flow(name="costs", log_prints=True, timeout_seconds=900)
async def costs(config: CostsConfig | None = None):
    """Collect infra costs from all connectors and snapshot them to PDS."""
    configure_logfire("prefect-flow-costs")
    config = config or CostsConfig()

    period = Period.trailing_month()
    print(f"collecting costs for {period.start.date()} .. {period.end.date()}")

    line_items: list[LineItem] = []
    failed_providers: list[str] = []
    for connector in await build_connectors():
        try:
            line_items.extend(await collect_connector(connector, period))
        except Exception as exc:
            failed_providers.append(connector.name)
            print(
                f"  {connector.name}: UNMEASURED after retries "
                f"({type(exc).__name__}: {exc})"
            )

    if failed_providers:
        failed = ", ".join(failed_providers)
        raise RuntimeError(
            "refusing to publish an incomplete cost snapshot; "
            f"failed providers: {failed}"
        )

    snapshot = Snapshot(
        generated_at=dt.datetime.now(dt.UTC),
        period=period,
        line_items=line_items,
    )
    print(f"snapshot total: ${snapshot.total / 100:.2f} across {len(line_items)} items")

    create_table_artifact(
        key="cost-snapshot",
        table=[
            {
                "provider": i.provider,
                "project": i.project,
                "service": i.service,
                "usd": round(i.amount / 100, 2),
                "estimated": i.estimated,
                "note": i.note or "",
            }
            for i in sorted(line_items, key=lambda i: -i.amount)
        ],
        description=f"infra costs {period.start.date()} .. {period.end.date()} — total ${snapshot.total / 100:.2f}",
    )

    if config.dry_run:
        print(json.dumps(snapshot.to_record(), indent=2))
        return snapshot.to_record()

    return await write_snapshot(snapshot)


if __name__ == "__main__":
    import asyncio
    import sys

    asyncio.run(costs(CostsConfig(dry_run="--write" not in sys.argv)))
