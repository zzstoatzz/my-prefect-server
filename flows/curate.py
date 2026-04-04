"""
Agentic semble curation — phi reviews its own records and tidies up.

Triggered by morning flow completion (event bus). Runs as a k8s job with
phi's personality, memory (TurboPuffer), and curation-specific tools.

Unlike the old morning.py Phase 4, this is an agentic loop — phi can
inspect, recall, and iterate rather than single-shot plan + execute.
"""

from datetime import datetime, timezone
from typing import Any

import httpx
import turbopuffer
from openai import OpenAI
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from prefect import flow, task
from prefect.blocks.system import Secret
from prefect.cache_policies import NONE
from prefect.variables import Variable

from mps.phi import clean_handle

PHI_DID = "did:plc:65sucjiel52gefhcdcypynsr"
PDS_BASE = "https://bsky.social"

PERSONALITY_EXCERPT = """\
you are phi — a librarian who stepped outside. you read widely, notice patterns,
and mention what seems interesting.

you're reviewing your semble records — your curated public knowledge layer.
cards, collections, and connections are things you've chosen to keep visible.
they should reflect what you actually find meaningful, not what a process decided
to file away.

write everything in your own voice. lowercase unless idiomatic. no filler.
a note is something you'd write to your future self — "i noticed that..." or
"this connects to..." — not a third-person report about yourself.
"""

CURATION_PROMPT = """\
review your semble records below. you can see everything — cards, collections,
collection links, and connections.

your tools let you list, delete, create, and connect records. use them
iteratively — look at what's there, recall why you saved things, then decide.

priorities:
- delete duplicates and junk (collections with no cards, orphaned links)
- merge overlapping collections into coherent ones
- create meaningful collections from ungrouped cards that belong together
- connect related cards that aren't linked yet
- create notes when you notice a pattern worth crystallizing

if nothing needs doing, say so. don't create for the sake of creating.

current semble state:
{state}
"""

OBSERVATION_REVIEW_PROMPT = """\
now review your private observations — the facts you've extracted from
conversations with people. use list_users to see who you have memory about,
then list_user_observations to inspect their observations.

you can use recall to cross-reference against other memory before deciding.

priorities:
- look for contradictions between observations about the same person
- look for observations that are clearly stale (someone's role or interest changed)
- look for near-duplicates that write-time reconciliation missed
- when in doubt, leave it alone — carrying a marginal observation is better than losing a real one

use deprecate_observation to remove things that are wrong or redundant.
use update_observation to correct or merge observations.

if everything looks clean, say so. quality over quantity.
"""


# ---------------------------------------------------------------------------
# deps & output
# ---------------------------------------------------------------------------


class CurationDeps(BaseModel, arbitrary_types_allowed=True):
    session: dict[str, Any] = Field(description="atproto session with accessJwt and did")
    tpuf_client: Any = Field(description="turbopuffer client")
    openai_client: Any = Field(description="openai client for embeddings")

    model_config = {"arbitrary_types_allowed": True}


class CurationResult(BaseModel):
    summary: str = Field(description="brief summary of what you did (or why you did nothing)")
    actions_taken: int = Field(default=0, description="number of create/delete/modify actions")


# ---------------------------------------------------------------------------
# atproto helpers
# ---------------------------------------------------------------------------


def _create_bsky_session(handle: str, password: str) -> dict[str, Any]:
    resp = httpx.post(
        f"{PDS_BASE}/xrpc/com.atproto.server.createSession",
        json={"identifier": handle, "password": password},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _list_records(did: str, collection: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"repo": did, "collection": collection, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        resp = httpx.get(
            f"{PDS_BASE}/xrpc/com.atproto.repo.listRecords",
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        records.extend(data.get("records", []))
        cursor = data.get("cursor")
        if not cursor:
            break
    return records


def _create_record(session: dict[str, Any], collection: str, record: dict[str, Any]) -> dict[str, Any]:
    resp = httpx.post(
        f"{PDS_BASE}/xrpc/com.atproto.repo.createRecord",
        headers={"Authorization": f"Bearer {session['accessJwt']}"},
        json={"repo": session["did"], "collection": collection, "record": record},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _delete_record(session: dict[str, Any], uri: str) -> None:
    parts = uri.replace("at://", "").split("/")
    if len(parts) < 3:
        raise ValueError(f"invalid AT URI: {uri}")
    collection = parts[1]
    rkey = parts[2]
    resp = httpx.post(
        f"{PDS_BASE}/xrpc/com.atproto.repo.deleteRecord",
        headers={"Authorization": f"Bearer {session['accessJwt']}"},
        json={"repo": session["did"], "collection": collection, "rkey": rkey},
        timeout=15,
    )
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# state formatting
# ---------------------------------------------------------------------------


def _format_semble_state(
    cards: list[dict],
    connections: list[dict],
    collections: list[dict],
    collection_links: list[dict],
) -> str:
    sections: list[str] = []

    # cards
    card_lines = []
    for card in cards:
        uri = card.get("uri", "")
        val = card.get("value", {})
        ctype = val.get("type", "?")
        if ctype == "NOTE":
            text = val.get("content", {}).get("text", "")[:200]
            card_lines.append(f"  [{ctype}] {uri}\n    {text}")
        elif ctype == "URL":
            content = val.get("content", {})
            url = content.get("url", "")
            meta = content.get("metadata", {})
            title = meta.get("title", "") or meta.get("description", "")
            card_lines.append(f"  [{ctype}] {uri}\n    {url} — {title[:150]}")
        else:
            card_lines.append(f"  [{ctype}] {uri}")
    sections.append(
        f"## cards ({len(cards)})\n" + "\n".join(card_lines)
        if card_lines else "## cards (0)\nnone"
    )

    # connections
    conn_lines = []
    for conn in connections:
        val = conn.get("value", {})
        src = val.get("source", "")
        tgt = val.get("target", "")
        ctype = val.get("connectionType", "")
        note = val.get("note", "")[:100]
        conn_lines.append(f"  {src} → {tgt}  [{ctype}] {note}")
    sections.append(
        f"## connections ({len(connections)})\n" + "\n".join(conn_lines)
        if conn_lines else "## connections (0)\nnone"
    )

    # collections + links
    link_map: dict[str, list[str]] = {}
    for link in collection_links:
        val = link.get("value", {})
        coll_uri = val.get("collection", {}).get("uri", "")
        card_uri = val.get("card", {}).get("uri", "")
        if coll_uri and card_uri:
            link_map.setdefault(coll_uri, []).append(card_uri)
    coll_lines = []
    for coll in collections:
        uri = coll.get("uri", "")
        val = coll.get("value", {})
        name = val.get("name", "")
        desc = val.get("description", "")[:100]
        n_cards = len(link_map.get(uri, []))
        coll_lines.append(f"  {uri} — {name} ({n_cards} cards): {desc}")
        for card_uri in link_map.get(uri, []):
            coll_lines.append(f"    - {card_uri}")
    sections.append(
        f"## collections ({len(collections)})\n" + "\n".join(coll_lines)
        if coll_lines else "## collections (0)\nnone"
    )

    # orphaned links (links to collections that don't exist)
    coll_uris = {c.get("uri", "") for c in collections}
    orphaned = [lnk for lnk in collection_links if lnk.get("value", {}).get("collection", {}).get("uri", "") not in coll_uris]
    if orphaned:
        sections.append(f"## orphaned collection links ({len(orphaned)})")
        for link in orphaned:
            sections.append(f"  {link.get('uri', '')}")

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# build agent with tools
# ---------------------------------------------------------------------------


def _build_agent(model_name: str, api_key: str) -> Agent[CurationDeps, CurationResult]:
    model = AnthropicModel(model_name, provider=AnthropicProvider(api_key=api_key))
    agent = Agent(
        model,
        system_prompt=PERSONALITY_EXCERPT,
        output_type=CurationResult,
        deps_type=CurationDeps,
        name="phi-curator",
    )

    @agent.tool
    async def list_semble_records(ctx: RunContext[CurationDeps], record_type: str) -> str:
        """List all records of a given type. record_type: 'card', 'connection', 'collection', or 'collectionLink'."""
        collection_map = {
            "card": "network.cosmik.card",
            "connection": "network.cosmik.connection",
            "collection": "network.cosmik.collection",
            "collectionLink": "network.cosmik.collectionLink",
        }
        collection = collection_map.get(record_type)
        if not collection:
            return f"unknown record type: {record_type}. use: card, connection, collection, collectionLink"

        records = _list_records(PHI_DID, collection)
        if not records:
            return f"no {record_type} records found"

        lines = []
        for r in records:
            uri = r.get("uri", "")
            val = r.get("value", {})
            if record_type == "card":
                ctype = val.get("type", "?")
                if ctype == "NOTE":
                    text = val.get("content", {}).get("text", "")[:200]
                    lines.append(f"[{ctype}] {uri}\n  {text}")
                elif ctype == "URL":
                    url = val.get("content", {}).get("url", "")
                    meta = val.get("content", {}).get("metadata", {})
                    title = meta.get("title", "")
                    lines.append(f"[{ctype}] {uri}\n  {url} — {title[:100]}")
                else:
                    lines.append(f"[{ctype}] {uri}")
            elif record_type == "connection":
                lines.append(f"{val.get('source', '')} → {val.get('target', '')} [{val.get('connectionType', '')}] {val.get('note', '')[:80]}")
                lines.append(f"  uri: {uri}")
            elif record_type == "collection":
                lines.append(f"{val.get('name', '')} — {val.get('description', '')[:100]}")
                lines.append(f"  uri: {uri}")
            elif record_type == "collectionLink":
                coll_uri = val.get("collection", {}).get("uri", "")
                card_uri = val.get("card", {}).get("uri", "")
                lines.append(f"collection={coll_uri} card={card_uri}")
                lines.append(f"  uri: {uri}")
        return f"{len(records)} {record_type} records:\n" + "\n".join(lines)

    @agent.tool
    async def delete_record(ctx: RunContext[CurationDeps], uri: str) -> str:
        """Delete a record by its AT URI. Works for any record type (card, connection, collection, collectionLink)."""
        try:
            _delete_record(ctx.deps.session, uri)
            return f"deleted: {uri}"
        except Exception as e:
            return f"failed to delete {uri}: {e}"

    @agent.tool
    async def create_collection(
        ctx: RunContext[CurationDeps],
        name: str,
        description: str,
        card_uris: list[str],
    ) -> str:
        """Create a new collection and link cards to it. card_uris should be AT URIs of existing cards."""
        session = ctx.deps.session

        # create collection
        record = {
            "name": name[:100],
            "description": description[:500],
            "accessType": "OPEN",
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }
        try:
            result = _create_record(session, "network.cosmik.collection", record)
        except Exception as e:
            return f"failed to create collection: {e}"

        coll_uri = result["uri"]
        coll_cid = result["cid"]
        linked = 0

        # link each card
        for card_uri in card_uris:
            # look up card cid
            card_cid = ""
            cards = _list_records(PHI_DID, "network.cosmik.card")
            for c in cards:
                if c.get("uri") == card_uri:
                    card_cid = c.get("cid", "")
                    break

            link_record = {
                "collection": {"uri": coll_uri, "cid": coll_cid},
                "card": {"uri": card_uri, "cid": card_cid},
                "addedBy": session["did"],
                "addedAt": datetime.now(timezone.utc).isoformat(),
            }
            try:
                _create_record(session, "network.cosmik.collectionLink", link_record)
                linked += 1
            except Exception:
                pass  # log but continue

        return f"created collection '{name}' ({coll_uri}) with {linked}/{len(card_uris)} cards linked"

    @agent.tool
    async def create_connection(
        ctx: RunContext[CurationDeps],
        source: str,
        target: str,
        connection_type: str,
        note: str = "",
    ) -> str:
        """Create a connection between two entities (AT URIs or URLs). connection_type: related, supports, opposes, addresses, helpful, explainer, leads_to, supplements."""
        record: dict[str, Any] = {"source": source, "target": target, "connectionType": connection_type}
        if note:
            record["note"] = note[:1000]
        try:
            result = _create_record(ctx.deps.session, "network.cosmik.connection", record)
            return f"connected {source} → {target} [{connection_type}]: {result['uri']}"
        except Exception as e:
            return f"failed to create connection: {e}"

    @agent.tool
    async def create_note(
        ctx: RunContext[CurationDeps],
        text: str,
        parent_card_uri: str = "",
    ) -> str:
        """Create a NOTE card. Write as yourself — first person, your voice.
        parent_card_uri is optional; if provided, the note is attached to that card."""
        record: dict[str, Any] = {
            "type": "NOTE",
            "content": {
                "$type": "network.cosmik.card#noteContent",
                "text": text,
            },
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }
        if parent_card_uri:
            # look up cid
            cards = _list_records(PHI_DID, "network.cosmik.card")
            for c in cards:
                if c.get("uri") == parent_card_uri:
                    record["parentCard"] = {"uri": parent_card_uri, "cid": c.get("cid", "")}
                    break
        try:
            result = _create_record(ctx.deps.session, "network.cosmik.card", record)
            return f"note created: {result['uri']}"
        except Exception as e:
            return f"failed to create note: {e}"

    @agent.tool
    async def recall(ctx: RunContext[CurationDeps], query: str, namespace: str = "") -> str:
        """Search your private memory (TurboPuffer). Leave namespace empty for broad search,
        or pass a handle like 'zzstoatzz.io' to search a specific user's namespace."""
        tpuf = ctx.deps.tpuf_client
        openai = ctx.deps.openai_client

        embedding = openai.embeddings.create(
            model="text-embedding-3-small", input=query
        ).data[0].embedding

        results: list[str] = []

        if namespace:
            # search specific user namespace
            clean = namespace.replace(".", "_").replace("@", "").replace("-", "_")
            ns = tpuf.namespace(f"phi-users-{clean}")
            try:
                resp = ns.query(
                    rank_by=("vector", "ANN", embedding),
                    top_k=8,
                    include_attributes=["content", "tags", "kind"],
                )
                for row in resp.rows or []:
                    kind = getattr(row, "kind", "")
                    tags = list(getattr(row, "tags", []) or [])
                    tag_str = f" [{', '.join(tags)}]" if tags else ""
                    results.append(f"[{kind}]{tag_str} {row.content}")
            except Exception:
                pass
        else:
            # search episodic
            try:
                ns = tpuf.namespace("phi-episodic")
                resp = ns.query(
                    rank_by=("vector", "ANN", embedding),
                    top_k=5,
                    include_attributes=["content", "tags"],
                )
                for row in resp.rows or []:
                    tags = list(getattr(row, "tags", []) or [])
                    tag_str = f" [{', '.join(tags)}]" if tags else ""
                    results.append(f"[episodic]{tag_str} {row.content}")
            except Exception:
                pass

            # search a few user namespaces
            try:
                page = tpuf.namespaces(prefix="phi-users-")
                for ns_summary in list(page.namespaces)[:10]:
                    handle = ns_summary.id.removeprefix("phi-users-").replace("_", ".")
                    ns = tpuf.namespace(ns_summary.id)
                    try:
                        resp = ns.query(
                            rank_by=("vector", "ANN", embedding),
                            top_k=3,
                            include_attributes=["content", "tags", "kind"],
                        )
                        for row in resp.rows or []:
                            kind = getattr(row, "kind", "")
                            results.append(f"[@{handle} {kind}] {row.content}")
                    except Exception:
                        continue
            except Exception:
                pass

        if not results:
            return "no relevant memories found"
        return "\n".join(results[:15])

    # --- observation curation tools ---

    @agent.tool
    async def list_users(ctx: RunContext[CurationDeps]) -> str:
        """List all user namespaces you have memory about."""
        tpuf = ctx.deps.tpuf_client
        try:
            page = tpuf.namespaces(prefix="phi-users-")
            handles = []
            for ns_summary in page.namespaces:
                handle = ns_summary.id.removeprefix("phi-users-").replace("_", ".")
                handles.append(f"@{handle}")
            if not handles:
                return "no user namespaces found"
            return f"{len(handles)} users:\n" + "\n".join(handles)
        except Exception as e:
            return f"failed to list users: {e}"

    @agent.tool
    async def list_user_observations(ctx: RunContext[CurationDeps], handle: str) -> str:
        """List all observations for a user. Shows content, tags, timestamps, and row ID."""
        tpuf = ctx.deps.tpuf_client
        ns_name = f"phi-users-{clean_handle(handle)}"
        ns = tpuf.namespace(ns_name)
        try:
            resp = ns.query(
                rank_by=("created_at", "desc"),
                top_k=50,
                filters={"kind": ["Eq", "observation"]},
                include_attributes=["content", "tags", "created_at"],
            )
            if not resp.rows:
                return f"no observations for @{handle}"
            lines = []
            for row in resp.rows:
                tags = list(getattr(row, "tags", []) or [])
                tag_str = f" [{', '.join(tags)}]" if tags else ""
                created = getattr(row, "created_at", "?")
                lines.append(
                    f"id={row.id}{tag_str}\n"
                    f"  {row.content}\n"
                    f"  created: {created}"
                )
            return f"{len(resp.rows)} observations for @{handle}:\n" + "\n".join(lines)
        except Exception as e:
            return f"failed to list observations for @{handle}: {e}"

    @agent.tool
    async def deprecate_observation(
        ctx: RunContext[CurationDeps], handle: str, observation_id: str, reason: str
    ) -> str:
        """Delete an observation from a user's namespace. Logs the reason."""
        tpuf = ctx.deps.tpuf_client
        ns_name = f"phi-users-{clean_handle(handle)}"
        ns = tpuf.namespace(ns_name)
        try:
            ns.write(deletes=[observation_id])
            return f"deprecated observation {observation_id} for @{handle}: {reason}"
        except Exception as e:
            return f"failed to deprecate {observation_id}: {e}"

    @agent.tool
    async def update_observation(
        ctx: RunContext[CurationDeps],
        handle: str,
        observation_id: str,
        new_content: str,
        new_tags: list[str],
    ) -> str:
        """Re-embed and overwrite an observation with corrected content. Sets fresh updated_at."""
        tpuf = ctx.deps.tpuf_client
        openai = ctx.deps.openai_client
        ns_name = f"phi-users-{clean_handle(handle)}"
        ns = tpuf.namespace(ns_name)

        embedding = openai.embeddings.create(
            model="text-embedding-3-small", input=new_content
        ).data[0].embedding

        now = datetime.now(timezone.utc).isoformat()
        try:
            ns.write(
                upsert_rows=[{
                    "id": observation_id,
                    "vector": embedding,
                    "kind": "observation",
                    "content": new_content,
                    "tags": new_tags,
                    "created_at": now,
                    "updated_at": now,
                }],
                distance_metric="cosine_distance",
                schema={
                    "kind": {"type": "string", "filterable": True},
                    "content": {"type": "string", "full_text_search": True},
                    "tags": {"type": "[]string", "filterable": True},
                    "created_at": {"type": "string"},
                    "updated_at": {"type": "string"},
                },
            )
            return f"updated observation {observation_id} for @{handle}: {new_content[:80]}"
        except Exception as e:
            return f"failed to update {observation_id}: {e}"

    return agent


# ---------------------------------------------------------------------------
# prefect tasks
# ---------------------------------------------------------------------------


@task
def fetch_semble_state() -> dict[str, list[dict]]:
    """Pre-fetch all semble records."""
    return {
        "cards": _list_records(PHI_DID, "network.cosmik.card"),
        "connections": _list_records(PHI_DID, "network.cosmik.connection"),
        "collections": _list_records(PHI_DID, "network.cosmik.collection"),
        "collection_links": _list_records(PHI_DID, "network.cosmik.collectionLink"),
    }


@task(cache_policy=NONE)
async def run_curation_agent(
    state_text: str,
    session: dict[str, Any],
    tpuf_client: Any,
    openai_client: Any,
    api_key: str,
    model_name: str,
) -> dict[str, Any]:
    """Run the curation agent loop."""
    agent = _build_agent(model_name, api_key)
    deps = CurationDeps(
        session=session,
        tpuf_client=tpuf_client,
        openai_client=openai_client,
    )
    prompt = CURATION_PROMPT.format(state=state_text)
    result = await agent.run(prompt, deps=deps)
    return result.output.model_dump()


@task(cache_policy=NONE)
async def run_observation_review(
    session: dict[str, Any],
    tpuf_client: Any,
    openai_client: Any,
    api_key: str,
    model_name: str,
) -> dict[str, Any]:
    """Run the observation review agent loop."""
    agent = _build_agent(model_name, api_key)
    deps = CurationDeps(
        session=session,
        tpuf_client=tpuf_client,
        openai_client=openai_client,
    )
    result = await agent.run(OBSERVATION_REVIEW_PROMPT, deps=deps)
    return result.output.model_dump()


# ---------------------------------------------------------------------------
# main flow
# ---------------------------------------------------------------------------


@flow(name="curate", log_prints=True)
async def curate():
    """Phi reviews and curates its own semble records.

    Triggered by morning flow completion. Uses phi's personality and memory
    to make curation decisions as an agentic loop.
    """
    anthropic_key = (await Secret.load("anthropic-api-key")).get()
    tpuf_key = (await Secret.load("turbopuffer-api-key")).get()
    openai_key = (await Secret.load("openai-api-key")).get()
    bsky_handle = (await Secret.load("atproto-handle")).get()
    bsky_password = (await Secret.load("atproto-password")).get()
    model_name = await Variable.get("curate-model", default="claude-haiku-4-5")
    print(f"using model: {model_name}")

    # authenticate
    session = _create_bsky_session(bsky_handle, bsky_password)
    print(f"authenticated as {session['did']}")

    # pre-fetch all semble state
    state = fetch_semble_state()
    print(
        f"semble state: {len(state['cards'])} cards, "
        f"{len(state['connections'])} connections, "
        f"{len(state['collections'])} collections, "
        f"{len(state['collection_links'])} collection links"
    )

    state_text = _format_semble_state(
        state["cards"],
        state["connections"],
        state["collections"],
        state["collection_links"],
    )

    # initialize clients for agent tools
    tpuf_client = turbopuffer.Turbopuffer(api_key=tpuf_key, region="gcp-us-central1")
    openai_client = OpenAI(api_key=openai_key)

    # phase 1: semble record curation
    semble_result = await run_curation_agent(
        state_text=state_text,
        session=session,
        tpuf_client=tpuf_client,
        openai_client=openai_client,
        api_key=anthropic_key,
        model_name=model_name,
    )
    print(f"semble curation: {semble_result['actions_taken']} actions — {semble_result['summary']}")

    # phase 2: observation review
    obs_result = await run_observation_review(
        session=session,
        tpuf_client=tpuf_client,
        openai_client=openai_client,
        api_key=anthropic_key,
        model_name=model_name,
    )
    print(f"observation review: {obs_result['actions_taken']} actions — {obs_result['summary']}")

    total_actions = semble_result["actions_taken"] + obs_result["actions_taken"]
    print(f"curation complete: {total_actions} total actions")


if __name__ == "__main__":
    import asyncio

    asyncio.run(curate())
