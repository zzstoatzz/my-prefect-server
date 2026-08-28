"""mcp fleet health — connect, discover, and route across the public MCP servers.

The fleet comes from the atlas registry (mcp.waow.tech/api/atlas.json): every
no-auth server with a url. This goes deeper than a 200-check — each server gets
its own fastmcp client and must complete protocol negotiation and namespaced
tool discovery through a ClientGroup. Connections are caller-managed so one
dead server is a finding, not a failed sweep; the survivors form the group.

Failure semantics mirror fleet_health.py: unhealthy servers are logged,
recorded in the markdown artifact, and emitted as `fleet-health.unhealthy`
(resource id `mcp-fleet-health`) so the existing "fleet unhealthy -> discord"
automation pages on them. The flow run fails only when the sweep itself cannot
run (the atlas registry is unreachable, or every connection check errors out
in a way that isn't a server being down).
"""

import asyncio
import time
from dataclasses import dataclass

import httpx
from fastmcp import Client
from fastmcp.client.group import ClientGroup
from prefect import flow, get_run_logger
from prefect.artifacts import acreate_markdown_artifact
from prefect.events import emit_event

ATLAS_URL = "https://mcp.waow.tech/api/atlas.json"
CONNECT_TIMEOUT_S = 20


@dataclass
class ServerHealth:
    name: str
    healthy: bool
    detail: str


async def _fetch_fleet() -> dict[str, str]:
    async with httpx.AsyncClient(timeout=15) as http:
        response = await http.get(ATLAS_URL)
        response.raise_for_status()
        atlas = response.json()
    return {
        server["name"]: server["url"]
        for server in atlas["servers"]
        if server.get("url") and not server.get("authRequired")
    }


async def _connect(name: str, client: Client) -> tuple[str, float | None, str]:
    started = time.perf_counter()
    try:
        await asyncio.wait_for(client.__aenter__(), timeout=CONNECT_TIMEOUT_S)
        return name, time.perf_counter() - started, ""
    except (Exception, asyncio.TimeoutError) as exc:
        return name, None, f"{type(exc).__name__}: {exc}"


@flow(log_prints=True)
async def mcp_fleet_health() -> None:
    logger = get_run_logger()
    fleet = await _fetch_fleet()
    logger.info("atlas fleet: %s", ", ".join(sorted(fleet)))

    clients = {name: Client(url, timeout=15) for name, url in fleet.items()}
    connects = await asyncio.gather(
        *(_connect(name, client) for name, client in clients.items())
    )
    up = {name: clients[name] for name, _, error in connects if not error}

    results: list[ServerHealth] = []
    try:
        counts: dict[str, int] = {}
        versions: dict[str, str | None] = {}
        if up:
            group = ClientGroup(up)
            tools = await group.list_tools()
            versions = group.protocol_versions
            for tool in tools:
                route = await group.resolve_tool(tool.name)
                counts[route.server_name] = counts.get(route.server_name, 0) + 1

        for name, connect_s, error in sorted(connects):
            if error:
                results.append(ServerHealth(name, False, error))
            else:
                detail = (
                    f"connect {connect_s * 1000:.0f}ms,"
                    f" protocol {versions.get(name)},"
                    f" {counts.get(name, 0)} tools"
                )
                results.append(ServerHealth(name, True, detail))
    finally:
        await asyncio.gather(
            *(client.__aexit__(None, None, None) for client in up.values()),
            return_exceptions=True,
        )

    rows = ["| server | status | detail |", "| --- | --- | --- |"]
    unhealthy: list[str] = []
    for result in results:
        mark = "ok" if result.healthy else "**DOWN**"
        rows.append(f"| {result.name} | {mark} | {result.detail} |")
        if not result.healthy:
            unhealthy.append(f"{result.name}: {result.detail}")

    await acreate_markdown_artifact(
        key="mcp-fleet-health",
        markdown="\n".join(rows),
        description="latest mcp fleet health sweep",
    )
    for line in rows[2:]:
        logger.info(line)

    if unhealthy:
        logger.warning("%d unhealthy: %s", len(unhealthy), " | ".join(unhealthy))
        emit_event(
            event="fleet-health.unhealthy",
            resource={"prefect.resource.id": "mcp-fleet-health"},
            payload={"unhealthy": unhealthy},
        )
