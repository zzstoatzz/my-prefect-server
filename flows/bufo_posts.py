"""Index the bufo bot's quote-posts by bufo and publish to the operator PDS.

Daily (the bot posts rarely now — opt-in only). Lists every post in the bot's
repo straight from its PDS (`com.atproto.repo.listRecords`, ~60 pages — the
appview's author feed 500s a few hundred deep, so it is not a source), groups
by the bufo named in the media alt text, and upserts:

- one `io.zzstoatzz.bufo.posts` record per bufo (only when its post set
  changed — a fingerprint of the existing record is compared first, so a
  steady state costs zero writes)
- one `io.zzstoatzz.bufo.quoted/index` record: quoted DID → post rkeys, for
  the stats page's per-user lookup

The bot stats page fetches a bufo's record, then hydrates its posts through
`app.bsky.feed.getPosts` so labels and deletions are honored at view time.

ad hoc:  uv run python flows/bufo_posts.py            # dry-run: prints a summary
         uv run python flows/bufo_posts.py --write
"""

from __future__ import annotations

import datetime as dt
import json
from urllib.parse import quote

import httpx
from atproto import AsyncClient
from pydantic import BaseModel, Field

from prefect import flow, task
from prefect.artifacts import create_table_artifact
from prefect.blocks.system import Secret
from prefect.cache_policies import NONE

from pdsx._internal.auth import login

from mps.bufo_posts import (
    BOT_DID,
    BOT_POST_COLLECTION,
    POSTS_COLLECTION,
    QUOTED_COLLECTION,
    QUOTED_RKEY,
    BotPost,
    bufo_record,
    bufo_rkey,
    group_by_bufo,
    parse_record,
    quoted_index_record,
    record_fingerprint,
)
from mps.observability import configure_logfire

OPERATOR_CREDS_BLOCK = "operator-atproto-creds"
UA = "my-prefect-server bufo-posts (+https://bot-stats.find-bufo.com)"


class PostsConfig(BaseModel):
    dry_run: bool = Field(default=True, description="summarize instead of writing to PDS", json_schema_extra=dict(position=0))


@task(cache_policy=NONE, retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
async def resolve_pds(did: str) -> str:
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": UA}) as client:
        doc = (await client.get(f"https://plc.directory/{did}")).raise_for_status().json()
    for svc in doc.get("service", []):
        if svc.get("id") == "#atproto_pds":
            return svc["serviceEndpoint"].rstrip("/")
    raise RuntimeError(f"{did} has no #atproto_pds service")


@task(cache_policy=NONE, retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
async def list_bot_posts(pds: str) -> list[BotPost]:
    posts: list[BotPost] = []
    cursor: str | None = None
    skipped = 0
    async with httpx.AsyncClient(timeout=60, headers={"User-Agent": UA}) as client:
        while True:
            url = f"{pds}/xrpc/com.atproto.repo.listRecords?repo={BOT_DID}&collection={BOT_POST_COLLECTION}&limit=100"
            if cursor:
                url += f"&cursor={quote(cursor)}"
            body = (await client.get(url)).raise_for_status().json()
            for rec in body.get("records", []):
                parsed = parse_record(rec["uri"], rec["value"])
                if parsed is None:
                    skipped += 1
                else:
                    posts.append(parsed)
            cursor = body.get("cursor")
            if not cursor or not body.get("records"):
                break
    print(f"  bot repo: {len(posts)} bufo posts ({skipped} without a bufo alt skipped)")
    return posts


async def _operator_creds() -> tuple[str, str, str]:
    raw = (await Secret.load(OPERATOR_CREDS_BLOCK)).get()
    if isinstance(raw, dict) and "handle" not in raw and "value" in raw:
        raw = raw["value"]
    creds = json.loads(raw) if isinstance(raw, str) else raw
    return creds["handle"], creds["password"], creds["pds"]


async def _existing_fingerprints(client: AsyncClient, did: str) -> dict[str, tuple]:
    out: dict[str, tuple] = {}
    cursor = None
    while True:
        resp = await client.com.atproto.repo.list_records(
            {"repo": did, "collection": POSTS_COLLECTION, "limit": 100, **({"cursor": cursor} if cursor else {})}
        )
        for rec in resp.records:
            out[rec.uri.rsplit("/", 1)[-1]] = record_fingerprint(rec.value)
        cursor = resp.cursor
        if not cursor or not resp.records:
            break
    return out


@task(cache_policy=NONE, retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
async def write_index(groups: dict[str, list[BotPost]], posts: list[BotPost], generated_at: dt.datetime) -> dict[str, int]:
    handle, password, pds = await _operator_creds()
    client = AsyncClient(base_url=pds)
    await login(client, handle, password, silent=True, required=True)
    did = client.me.did

    existing = await _existing_fingerprints(client, did)
    written = 0
    for name, bufo_posts in groups.items():
        record = bufo_record(name, bufo_posts, generated_at)
        rkey = bufo_rkey(name)
        if existing.get(rkey) == record_fingerprint(record):
            continue
        await client.com.atproto.repo.put_record(
            {"repo": did, "collection": POSTS_COLLECTION, "rkey": rkey, "record": record}
        )
        written += 1

    await client.com.atproto.repo.put_record(
        {"repo": did, "collection": QUOTED_COLLECTION, "rkey": QUOTED_RKEY, "record": quoted_index_record(posts, generated_at)}
    )
    print(f"  wrote {written} changed bufo record(s) of {len(groups)}, plus the quoted index")
    return {"bufos": len(groups), "written": written}


@flow(name="bufo-posts", log_prints=True, timeout_seconds=900)
async def bufo_posts(config: PostsConfig | None = None):
    """Rebuild the per-bufo index of the bot's quote-posts on the operator PDS."""
    configure_logfire("prefect-flow-bufo-posts")
    config = config or PostsConfig()

    pds = await resolve_pds(BOT_DID)
    posts = await list_bot_posts(pds)
    groups = group_by_bufo(posts)
    now = dt.datetime.now(dt.UTC)

    create_table_artifact(
        key="bufo-posts",
        table=[
            {"bufo": name, "posts": len(ps), "latest": ps[0].created_at.date().isoformat()}
            for name, ps in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:50]
        ],
        description=f"{len(posts)} bot posts across {len(groups)} bufos",
    )

    if config.dry_run:
        print(f"dry run: {len(posts)} posts, {len(groups)} bufos; top: "
              + ", ".join(f"{n} ({len(p)})" for n, p in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:5]))
        return {"bufos": len(groups), "posts": len(posts)}

    return await write_index(groups, posts, now)


if __name__ == "__main__":
    import asyncio
    import sys

    asyncio.run(bufo_posts(PostsConfig(dry_run="--write" not in sys.argv)))
