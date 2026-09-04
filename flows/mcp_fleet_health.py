"""mcp fleet health — connect, discover, and route across the public MCP servers.

The fleet comes from the atlas registry: every no-auth server with a url. This
goes deeper than a 200-check — each server gets its own fastmcp client and must
complete protocol negotiation, then the survivors form a ClientGroup for
namespaced tool discovery. Connections are caller-managed so one dead server is
a finding, not a failed sweep.

Failure semantics mirror fleet_health.py: unhealthy servers are logged,
recorded in the markdown artifact, and emitted as `fleet-health.unhealthy`
(resource id `mcp-fleet-health`) so the existing "fleet unhealthy -> discord"
automation pages on them. The flow run fails only when the sweep itself cannot
run (the atlas registry is unreachable after retries).
"""

import asyncio
import time
from dataclasses import dataclass

import httpx
from fastmcp import Client
from fastmcp.client.group import ClientGroup
from prefect import flow, get_run_logger, task
from prefect.artifacts import acreate_markdown_artifact
from prefect.cache_policies import NO_CACHE
from prefect.events import emit_event

DEFAULT_ATLAS_URL = "https://mcp.waow.tech/api/atlas.json"


@dataclass
class ServerHealth:
    name: str
    healthy: bool
    detail: str


@task(retries=2, retry_delay_seconds=5)
async def fetch_fleet(atlas_url: str) -> dict[str, str]:
    async with httpx.AsyncClient(timeout=15) as http:
        response = await http.get(atlas_url)
        response.raise_for_status()
        atlas = response.json()
    return {
        server["name"]: server["url"]
        for server in atlas["servers"]
        if server.get("url") and not server.get("authRequired")
    }


@task(retries=1, retry_delay_seconds=10, cache_policy=NO_CACHE)
async def connect_server(name: str, client: Client, timeout_s: float) -> float:
    """Enter the client's context, returning connect latency in seconds.

    A raise (after retry) means the server is down — the flow records it as a
    finding rather than letting the task failure fail the sweep.
    """
    started = time.perf_counter()
    await asyncio.wait_for(client.__aenter__(), timeout=timeout_s)
    return time.perf_counter() - started


@task(cache_policy=NO_CACHE)
async def discover(group: ClientGroup) -> tuple[dict[str, int], dict[str, str | None]]:
    """Namespaced discovery across the connected fleet: per-server tool counts
    and negotiated protocol versions."""
    counts: dict[str, int] = {}
    for tool in await group.list_tools():
        route = await group.resolve_tool(tool.name)
        counts[route.server_name] = counts.get(route.server_name, 0) + 1
    return counts, group.protocol_versions


@flow(log_prints=True)
async def mcp_fleet_health(
    atlas_url: str = DEFAULT_ATLAS_URL,
    connect_timeout_s: float = 20,
) -> None:
    logger = get_run_logger()
    fleet = await fetch_fleet(atlas_url)

    clients = {name: Client(url, timeout=15) for name, url in fleet.items()}
    connects = await asyncio.gather(
        *(connect_server(name, client, connect_timeout_s) for name, client in clients.items()),
        return_exceptions=True,
    )

    results: list[ServerHealth] = []
    up: dict[str, Client] = {}
    for (name, client), outcome in zip(clients.items(), connects, strict=True):
        if isinstance(outcome, BaseException):
            results.append(ServerHealth(name, False, f"{type(outcome).__name__}: {outcome}"))
        else:
            up[name] = client
            results.append(ServerHealth(name, True, f"connect {outcome * 1000:.0f}ms"))

    try:
        if up:
            counts, versions = await discover(ClientGroup(up))
            for result in results:
                if result.healthy:
                    result.detail += (
                        f", protocol {versions.get(result.name)},"
                        f" {counts.get(result.name, 0)} tools"
                    )
    finally:
        await asyncio.gather(
            *(client.__aexit__(None, None, None) for client in up.values()),
            return_exceptions=True,
        )

    unhealthy = [f"{r.name}: {r.detail}" for r in results if not r.healthy]
    rows = ["| server | status | detail |", "| --- | --- | --- |"] + [
        f"| {r.name} | {'ok' if r.healthy else '**DOWN**'} | {r.detail} |"
        for r in sorted(results, key=lambda r: r.name)
    ]
    await acreate_markdown_artifact(
        key="mcp-fleet-health",
        markdown="\n".join(rows),
        description="latest mcp fleet health sweep",
    )

    logger.info("%d/%d servers healthy", len(up), len(fleet))
    if unhealthy:
        logger.warning("%d unhealthy: %s", len(unhealthy), " | ".join(unhealthy))
        emit_event(
            event="fleet-health.unhealthy",
            resource={"prefect.resource.id": "mcp-fleet-health"},
            payload={"unhealthy": unhealthy},
        )
