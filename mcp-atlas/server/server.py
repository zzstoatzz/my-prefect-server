"""mcp-atlas — search the MCP atlas (mcp.waow.tech) from inside an agent.

the atlas is a projection over tech.waow.mcp.server records that publishers
put on their own atproto PDSes. this server wraps the directory's search and
lookup APIs so an agent can discover MCP servers in english and get back
everything needed to connect or install.
"""

from typing import Any

import httpx
from fastmcp import FastMCP

ATLAS_URL = "https://mcp.waow.tech"

mcp = FastMCP(
    name="mcp-atlas",
    instructions=(
        "Directory of MCP servers self-published on the atproto network. "
        "Use search_servers to find servers by what you need in plain english, "
        "then get_server for connection/install details."
    ),
)


async def _atlas() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{ATLAS_URL}/api/atlas.json")
        resp.raise_for_status()
        return resp.json()


def _install_hint(s: dict[str, Any]) -> str | None:
    name = s.get("name", "server")
    if s.get("url"):
        auth = " (endpoint requires auth)" if s.get("authRequired") else ""
        return f"claude mcp add --transport http {name} {s['url']}{auth}"
    runners = {"pypi": "uvx", "npm": "bunx"}
    for pkg in s.get("packages") or []:
        runner = runners.get(pkg.get("registry"))
        if runner:
            return f"claude mcp add {name} -- {runner} {pkg['identifier']}"
    if s.get("repo"):
        return f"run from source: {s['repo']}"
    return None


def _detail(s: dict[str, Any]) -> dict[str, Any]:
    out = {
        k: s[k]
        for k in (
            "name",
            "description",
            "handle",
            "did",
            "uri",
            "transport",
            "url",
            "repo",
            "manifest",
            "language",
            "tools",
            "environment",
            "packages",
            "alive",
            "authRequired",
        )
        if s.get(k) not in (None, [], "")
    }
    hint = _install_hint(s)
    if hint:
        out["install"] = hint
    return out


@mcp.tool
async def search_servers(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Find MCP servers by describing what you need in plain english.

    Returns ranked matches with connection details and an install hint.
    """
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{ATLAS_URL}/api/search", params={"q": query}
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])[:limit]
    atlas = await _atlas()
    by_name = {s["name"]: s for s in atlas.get("servers", [])}
    out = []
    for hit in results:
        server = by_name.get(hit.get("name"))
        if server is None:
            continue
        entry = _detail(server)
        entry["score"] = round(hit.get("score", 0), 3)
        out.append(entry)
    return out


@mcp.tool
async def get_server(name: str) -> dict[str, Any]:
    """Full record for one server by name: tools, env vars, packages, install hint."""
    atlas = await _atlas()
    for s in atlas.get("servers", []):
        if s.get("name") == name:
            return _detail(s)
    known = sorted(s.get("name", "") for s in atlas.get("servers", []))
    raise ValueError(f"no server named {name!r}. known servers: {', '.join(known)}")


@mcp.tool
async def list_servers() -> list[dict[str, Any]]:
    """Every server in the atlas — name, publisher, one-line description, status."""
    atlas = await _atlas()
    return [
        {
            "name": s.get("name"),
            "publisher": s.get("handle"),
            "description": s.get("description"),
            "transport": s.get("transport"),
            "live": s.get("alive"),
        }
        for s in sorted(atlas.get("servers", []), key=lambda s: s.get("name", ""))
    ]


if __name__ == "__main__":
    mcp.run()
