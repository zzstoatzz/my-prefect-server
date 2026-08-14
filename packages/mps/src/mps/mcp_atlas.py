"""Pure helpers for the mcp-atlas crawl (flows/mcp_atlas.py).

The atlas is a view over ``tech.waow.mcp.server`` records that publishers
keep on their own PDSes. Nothing here talks to the network — normalization
is kept pure so it can be tested against record shapes we don't control.
"""

from __future__ import annotations

import math
from typing import Any

COLLECTION = "tech.waow.mcp.server"

MAX_TOOLS = 128
MAX_NAME = 64
MAX_TOOL_NAME = 128
MAX_TOOL_DESCRIPTION = 300
MAX_DESCRIPTION = 500
TRANSPORTS = ("http", "stdio")


def _http_url(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith(("https://", "http://")):
        return value
    return None


def normalize_record(
    did: str, handle: str | None, uri: str, value: dict[str, Any]
) -> dict[str, Any] | None:
    """Turn one PDS record into an atlas entry, or None if unusable.

    Records are arbitrary user data: anything beyond the lexicon's required
    ``name`` and ``description`` is optional, wrong types are dropped rather
    than failing the crawl, and string fields are clamped to the lexicon
    limits so one hostile record can't bloat the atlas.
    """
    name = value.get("name")
    description = value.get("description")
    if not isinstance(name, str) or not name.strip():
        return None
    if not isinstance(description, str) or not description.strip():
        return None

    tools_raw = value.get("tools")
    tools: list[dict[str, Any]] = []
    for item in tools_raw if isinstance(tools_raw, list) else []:
        # lexicon shape is #tool objects; tolerate bare strings from
        # pre-v1 records and hand-written ones
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            continue
        tool_name = item.get("name")
        if not isinstance(tool_name, str) or not tool_name.strip():
            continue
        tool_description = item.get("description")
        tools.append(
            {
                "name": tool_name.strip()[:MAX_TOOL_NAME],
                "description": tool_description.strip()[:MAX_TOOL_DESCRIPTION]
                if isinstance(tool_description, str) and tool_description.strip()
                else None,
            }
        )
        if len(tools) == MAX_TOOLS:
            break

    env_raw = value.get("environment")
    environment: list[dict[str, Any]] = []
    for item in (env_raw if isinstance(env_raw, list) else [])[:32]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        env_description = item.get("description")
        environment.append(
            {
                "name": item["name"].strip()[:MAX_NAME],
                "required": item.get("required") is True,
                "description": env_description.strip()[:MAX_TOOL_DESCRIPTION]
                if isinstance(env_description, str) and env_description.strip()
                else None,
            }
        )

    pkg_raw = value.get("packages")
    packages: list[dict[str, str]] = []
    for item in (pkg_raw if isinstance(pkg_raw, list) else [])[:8]:
        if not isinstance(item, dict):
            continue
        registry, identifier = item.get("registry"), item.get("identifier")
        if isinstance(registry, str) and isinstance(identifier, str):
            packages.append(
                {
                    "registry": registry.strip()[:32],
                    "identifier": identifier.strip()[:MAX_TOOL_NAME],
                }
            )

    transport = value.get("transport")
    framework = value.get("framework")
    return {
        "did": did,
        "handle": handle,
        "uri": uri,
        "name": name.strip()[:MAX_NAME],
        "description": description.strip()[:MAX_DESCRIPTION],
        "repo": _http_url(value.get("repo")),
        "url": _http_url(value.get("url")),
        "manifest": _http_url(value.get("manifest")),
        "framework": framework.strip() if isinstance(framework, str) else None,
        "transport": transport if transport in TRANSPORTS else None,
        "tools": tools,
        "environment": environment,
        "packages": packages,
        "createdAt": value.get("createdAt")
        if isinstance(value.get("createdAt"), str)
        else None,
    }


def handle_from_did_doc(doc: dict[str, Any]) -> str | None:
    """Extract the bare handle from a DID document's alsoKnownAs."""
    for aka in doc.get("alsoKnownAs", []):
        if isinstance(aka, str) and aka.startswith("at://"):
            return aka.removeprefix("at://")
    return None


def pds_from_did_doc(doc: dict[str, Any]) -> str | None:
    """Extract the PDS service endpoint from a DID document."""
    for svc in doc.get("service", []):
        if svc.get("id", "").endswith("#atproto_pds"):
            endpoint = svc.get("serviceEndpoint")
            return endpoint if isinstance(endpoint, str) else None
    return None


def _tokens(entry: dict[str, Any]) -> list[str]:
    text = " ".join(
        [entry["name"], entry["description"], entry.get("framework") or ""]
        + [t["name"] for t in entry["tools"]]
        + [t["description"] or "" for t in entry["tools"]]
    )
    return [
        t
        for t in "".join(c if c.isalnum() else " " for c in text.lower()).split()
        if len(t) > 2
    ]


def atlas_positions(entries: list[dict[str, Any]]) -> list[tuple[float, float]]:
    """2D semantic positions in [0,1]² from tf-idf + power-iteration PCA.

    Deliberately dependency-free: at directory scale (tens to hundreds of
    servers) a tf-idf doc-term matrix projected onto its top two principal
    components is enough for "similar servers sit near each other", and it
    keeps numpy/umap out of the flow's pod. Swap for real embeddings + UMAP
    when the corpus outgrows it. Deterministic for a given atlas.
    """
    n = len(entries)
    if n == 0:
        return []
    if n == 1:
        return [(0.5, 0.5)]

    docs = [_tokens(e) for e in entries]
    vocab: dict[str, int] = {}
    df: dict[str, int] = {}
    for doc in docs:
        for term in set(doc):
            vocab.setdefault(term, len(vocab))
            df[term] = df.get(term, 0) + 1

    rows: list[list[float]] = []
    for doc in docs:
        vec = [0.0] * len(vocab)
        for term in doc:
            vec[vocab[term]] += 1.0
        for term in set(doc):
            j = vocab[term]
            vec[j] = (vec[j] / len(doc)) * math.log((1 + n) / (1 + df[term]))
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        rows.append([v / norm for v in vec])

    dim = len(vocab)
    means = [sum(r[j] for r in rows) / n for j in range(dim)]
    centered = [[r[j] - means[j] for j in range(dim)] for r in rows]

    def project(component: list[float]) -> list[float]:
        return [sum(r[j] * component[j] for j in range(dim)) for r in centered]

    def power_iterate(deflate: list[float] | None) -> list[float]:
        # seeded deterministically; deflation removes the first component
        comp = [math.sin(j + 1.0) for j in range(dim)]
        for _ in range(60):
            if deflate is not None:
                dot = sum(a * b for a, b in zip(comp, deflate))
                comp = [a - dot * b for a, b in zip(comp, deflate)]
            scores = project(comp)
            comp = [
                sum(scores[i] * centered[i][j] for i in range(n)) for j in range(dim)
            ]
            norm = math.sqrt(sum(c * c for c in comp)) or 1.0
            comp = [c / norm for c in comp]
        return comp

    first = power_iterate(None)
    second = power_iterate(first)
    xs, ys = project(first), project(second)

    def rescale(vals: list[float]) -> list[float]:
        lo, hi = min(vals), max(vals)
        if hi - lo < 1e-12:
            return [0.5] * len(vals)
        return [0.1 + 0.8 * (v - lo) / (hi - lo) for v in vals]

    return list(zip(rescale(xs), rescale(ys)))
