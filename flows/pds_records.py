"""
General-purpose PDS record management — list, create, update, delete.

Manual-only deployment (no schedule, no trigger). Replaces ad-hoc scripts
for bulk PDS operations like cleaning up broken connection records.

Uses pdsx for auth and record operations instead of hand-rolled httpx.
"""

import re
from typing import Any, Literal

from atproto import AsyncClient
from pydantic import BaseModel, Field

from prefect import flow, task
from prefect.blocks.system import Secret

from pdsx._internal.auth import login
from pdsx._internal.operations import (
    create_record,
    delete_record,
    list_records,
    update_record,
)


class PdsRecordsConfig(BaseModel):
    action: Literal["list", "delete", "create", "update"]
    collection: str = Field(description="e.g. network.cosmik.connection")
    repo: str | None = Field(default=None, description="target repo (default: authenticated user)")
    record: dict[str, Any] | None = Field(default=None, description="for create: the record body")
    uri: str | None = Field(default=None, description="for update/delete: specific record AT-URI")
    rkey_filter: str | None = Field(default=None, description="for delete: regex filter on rkey (None = all)")
    dry_run: bool = Field(default=True, description="delete safety default")


async def _paginate_all(
    client: AsyncClient, collection: str, repo: str | None
) -> list[dict[str, Any]]:
    """Paginate through all records in a collection."""
    all_records: list[dict[str, Any]] = []
    cursor = None
    while True:
        resp = await list_records(client, collection, limit=100, repo=repo, cursor=cursor)
        for r in resp.records:
            all_records.append({"uri": r.uri, "cid": r.cid, "value": r.value})
        cursor = resp.cursor
        if not cursor:
            break
    return all_records


@task
async def list_pds_records(client: AsyncClient, config: PdsRecordsConfig) -> list[dict[str, Any]]:
    """List all records in a collection, print count + sample."""
    records = await _paginate_all(client, config.collection, config.repo)
    print(f"found {len(records)} records in {config.collection}")
    for r in records[:5]:
        print(f"  {r['uri']}: {r['value']}")
    if len(records) > 5:
        print(f"  ... and {len(records) - 5} more")
    return records


@task
async def delete_pds_records(client: AsyncClient, config: PdsRecordsConfig) -> int:
    """List → optional rkey filter → dry_run preview or delete each."""
    records = await _paginate_all(client, config.collection, config.repo)

    if config.rkey_filter:
        pattern = re.compile(config.rkey_filter)
        records = [r for r in records if pattern.search(r["uri"].split("/")[-1])]

    print(f"matched {len(records)} records for deletion")

    if config.dry_run:
        print("dry run — would delete:")
        for r in records:
            print(f"  {r['uri']}")
        return 0

    deleted = 0
    for r in records:
        await delete_record(client, r["uri"])
        deleted += 1
        print(f"  deleted {r['uri']}")
    print(f"deleted {deleted} records")
    return deleted


@task
async def create_pds_record(client: AsyncClient, config: PdsRecordsConfig) -> dict[str, str]:
    """Create a record, return URI + CID."""
    if not config.record:
        raise ValueError("config.record is required for create action")

    resp = await create_record(client, config.collection, config.record)
    print(f"created {resp.uri} (cid={resp.cid})")
    return {"uri": resp.uri, "cid": resp.cid}


@task
async def update_pds_record(client: AsyncClient, config: PdsRecordsConfig) -> dict[str, str]:
    """Update a record at the given URI."""
    if not config.uri:
        raise ValueError("config.uri is required for update action")
    if not config.record:
        raise ValueError("config.record is required for update action")

    resp = await update_record(client, config.uri, config.record)
    print(f"updated {resp.uri} (cid={resp.cid})")
    return {"uri": resp.uri, "cid": resp.cid}


@flow(name="pds-records", log_prints=True)
async def pds_records(config: PdsRecordsConfig):
    """General-purpose PDS record management.

    Parameterized by action, collection, and optional filters.
    Credentials from prefect secrets. pdsx handles auth + PDS discovery.
    """
    handle = (await Secret.load("atproto-handle")).get()
    password = (await Secret.load("atproto-password")).get()

    client = AsyncClient()
    await login(client, handle, password, silent=True, required=True)
    print(f"authenticated as {client.me.did}")

    match config.action:
        case "list":
            await list_pds_records(client, config)
        case "delete":
            await delete_pds_records(client, config)
        case "create":
            await create_pds_record(client, config)
        case "update":
            await update_pds_record(client, config)


if __name__ == "__main__":
    import asyncio

    asyncio.run(
        pds_records(
            PdsRecordsConfig(action="list", collection="network.cosmik.connection")
        )
    )
