"""index of the bufo bot's quote-posts, grouped by bufo, published to the
operator PDS so the bot stats page can open any bufo's posts in one request.

why an index: the bot has thousands of posts. the public appview's author
feed 500s a few hundred posts deep, and its own PDS needs ~60 pages to list
them — fine for a flow, hopeless for a click. `com.atproto.repo.listRecords`
on the bot's repo is the complete, reliable source; this module turns those
records into:

- `io.zzstoatzz.bufo.posts` — one record per bufo (rkey = the bufo name made
  rkey-safe): the bot's post rkeys with timestamps and what each one quoted
- `io.zzstoatzz.bufo.quoted` / `index` — one record mapping quoted DID →
  post rkeys, for the "which of my posts got a bufo" lookup

the page hydrates rkeys through slingshot (microcosm's record cache — no
bluesky appview) at view time: the quoted post record, and the author's
profile record whose self-labels carry `!no-unauthenticated`, so hidden
authors and deletions are honored live, not frozen.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from dataclasses import dataclass

POSTS_COLLECTION = "io.zzstoatzz.bufo.posts"
QUOTED_COLLECTION = "io.zzstoatzz.bufo.quoted"
QUOTED_RKEY = "index"

BOT_DID = "did:plc:rkpcbwi4rapfkm2huwrkspun"
BOT_POST_COLLECTION = "app.bsky.feed.post"

_RKEY_UNSAFE = re.compile(r"[^A-Za-z0-9._:~-]")


def bufo_name_from_alt(alt: str) -> str:
    """the bot writes alt text from the bufo name with dashes turned to spaces
    ("bufo-on-the-ceiling" → "bufo on the ceiling"); invert that."""
    return alt.strip().replace(" ", "-")


def bufo_rkey(name: str) -> str:
    """a record key from a bufo name: rkeys allow `A-Za-z0-9._:~-` only, and
    bufo names can carry apostrophes (`bufo's-a-gamer-girl`). the page uses
    the same function to address a bufo's record."""
    return _RKEY_UNSAFE.sub("-", name)[:512]


@dataclass(frozen=True)
class BotPost:
    rkey: str
    created_at: dt.datetime
    bufo: str
    quoted_uri: str | None

    @property
    def quoted_did(self) -> str | None:
        if not self.quoted_uri or not self.quoted_uri.startswith("at://"):
            return None
        return self.quoted_uri[len("at://") :].split("/", 1)[0]


def parse_record(uri: str, value: dict) -> BotPost | None:
    """one listRecords entry → BotPost, or None when it isn't a bufo quote-post
    (no media alt to name the bufo)."""
    embed = value.get("embed") or {}
    media = embed.get("media") or {}
    alt: str | None
    if media.get("$type") == "app.bsky.embed.images":
        images = media.get("images") or []
        alt = images[0].get("alt") if images else None
    else:
        alt = media.get("alt")
    if not alt:
        return None
    created = value.get("createdAt")
    try:
        created_at = dt.datetime.fromisoformat(str(created).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    quoted = (embed.get("record") or {}).get("record") or {}
    return BotPost(
        rkey=uri.rsplit("/", 1)[-1],
        created_at=created_at.astimezone(dt.UTC),
        bufo=bufo_name_from_alt(alt),
        quoted_uri=quoted.get("uri"),
    )


def group_by_bufo(posts: list[BotPost]) -> dict[str, list[BotPost]]:
    groups: dict[str, list[BotPost]] = defaultdict(list)
    for p in posts:
        groups[p.bufo].append(p)
    for g in groups.values():
        g.sort(key=lambda p: p.created_at, reverse=True)
    return dict(groups)


def bufo_record(name: str, posts: list[BotPost], generated_at: dt.datetime) -> dict:
    """compact: each post is [rkey, unix seconds, quoted uri | null], newest
    first. the busiest bufo has ~500 posts, so this stays well under any
    record size ceiling."""
    return {
        "$type": POSTS_COLLECTION,
        "name": name,
        "botDid": BOT_DID,
        "count": len(posts),
        "posts": [
            [p.rkey, int(p.created_at.timestamp()), p.quoted_uri]
            for p in posts
        ],
        "generatedAt": _iso(generated_at),
    }


def quoted_index_record(posts: list[BotPost], generated_at: dt.datetime) -> dict:
    by_did: dict[str, list[str]] = defaultdict(list)
    for p in sorted(posts, key=lambda p: p.created_at, reverse=True):
        if p.quoted_did:
            by_did[p.quoted_did].append(p.rkey)
    return {
        "$type": QUOTED_COLLECTION,
        "botDid": BOT_DID,
        "byDid": dict(sorted(by_did.items())),
        "generatedAt": _iso(generated_at),
    }


def record_fingerprint(record: dict) -> tuple:
    """what makes a bufo record worth rewriting: its post set. generatedAt is
    excluded so an unchanged bufo costs no write."""
    return (record.get("count"), tuple(tuple(p) for p in record.get("posts") or []))


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
