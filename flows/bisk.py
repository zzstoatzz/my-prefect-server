"""compute the authoritative bisk.social snapshot and publish it to R2.

bisk.social is a ball pit of bisks (posts) sized by likes. the live race must be
the same for everyone and must not depend on any one browser's session — so we
compute the current standings here and publish a static snapshot the static site
adopts (the pub-search builder pattern; see flows/typeahead_index.py for the
sibling R2-snapshot flow).

  - pool  = dave's follows ∪ dave's followers, under the Enhanced Grace Limit
            (the 7k Grace Limit, recalibrated EF-scale-style; see ENHANCED_GRACE_LIMIT)
  - live  = each pool member's own posts in the trailing 24h, sized by current
            likes. getAuthorFeed hydrates likeCount for free — one call per
            member, no per-post backlink lookups.
  - coop  = topchicken's crownings; each embeds the winning bisk (→ openable).

source repo: https://tangled.org/zzstoatzz.io/bisk

Secrets are injected into the environment by the deployment (see prefect.yaml);
flow code never touches the Secret API. Expected env (project-scoped — a token
minted for the `bisk` bucket only, NOT shared with other flows):

  - BISK_R2_ENDPOINT / _ACCESS_KEY_ID / _SECRET_ACCESS_KEY  (bucket-scoped S3 creds)
  - BISK_R2_BUCKET                                          (the bisk bucket)
  - BISK_OUT (optional)  — write to this local path instead of R2 (dev/CI)
"""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from prefect import flow, task
from prefect.tasks import exponential_backoff

DAVE = "did:plc:hovt6k22s64dq63jjmoyibk3"
TOPCHICKEN = "did:plc:bty3nc67lteylmmb7hvgxeu5"

# the Enhanced Grace Limit (EGL). much like the Fujita scale was retired in 2007
# for the Enhanced Fujita scale — after meteorologists conceded F5 didn't capture
# the full fury of a really committed tornado — the original 7k Grace Limit has
# been recalibrated. the legacy reading clocked Grace herself (7,136) just outside
# her own namesake cap, which is the kind of measurement error the EF revision
# exists to fix. instrumentation has improved; the eyewall is wider now.
ENHANCED_GRACE_LIMIT = 10_000
GRACE_LIMIT = ENHANCED_GRACE_LIMIT  # legacy alias; kept so the snapshot field reads true
# topchicken's round is one UTC calendar day of posts. trading on round D runs
# D 06:00 → D+1 06:00 UTC, and the winner is crowned ~13:05 UTC on D+1 (see the
# chicken market's "how a round works"). the live race shows the round whose
# trading is currently open: the posting day of the most recent 06:00 UTC
# boundary. anchoring to that discrete day — NOT a rolling trailing-24h window —
# is what resets the race each round instead of letting the just-crowned post
# linger as leader. both bounds matter: during the 00:00–06:00 overtime hours,
# fresh posts belong to the *next* round and must not leak into this one.
# (the all-time view uses topchicken's own crownings, so it's unaffected.)
LOCK_HOUR_UTC = 6


def round_bounds(now: datetime) -> tuple[datetime, datetime]:
    """[start, end) of the posting day for the round currently trading."""
    lock = now.replace(hour=LOCK_HOUR_UTC, minute=0, second=0, microsecond=0)
    day = (lock if now >= lock else lock - timedelta(days=1)).replace(hour=0)
    return day, day + timedelta(days=1)


CONSTELLATION = "https://constellation.microcosm.blue/xrpc"
SLINGSHOT = "https://slingshot.microcosm.blue/xrpc"
BSKY = "https://public.api.bsky.app/xrpc"
OBJECT_KEY = "bisk.json"


async def _get(client: httpx.AsyncClient, url: str, params: dict) -> dict:
    """GET + raise_for_status.

    No retry here on purpose: the tasks below carry prefect retries, so a
    dropped connection anywhere in a paginated walk re-runs that task rather
    than papering over the failure mid-walk with partial state. constellation
    intermittently closes connections without a response
    (httpx.RemoteProtocolError) and occasionally 502s — that took out 3 of 9
    bisk-snapshot runs on 2026-08-12 before the tasks had retries.
    """
    r = await client.get(url, params=params)
    r.raise_for_status()
    return r.json()


async def _backlink_dids(client, subject: str, source: str) -> list[str]:
    out, cursor = [], None
    while True:
        p = {"subject": subject, "source": source, "limit": 1000}
        if cursor:
            p["cursor"] = cursor
        body = await _get(client, f"{CONSTELLATION}/blue.microcosm.links.getBacklinks", p)
        out += [rec["did"] for rec in body.get("records", [])]
        cursor = body.get("cursor")
        if not cursor:
            return list(set(out))


async def _list_records(client, pds: str, repo: str, collection: str) -> list[dict]:
    out, cursor = [], None
    while True:
        p = {"repo": repo, "collection": collection, "limit": 100}
        if cursor:
            p["cursor"] = cursor
        body = await _get(client, f"{pds}/xrpc/com.atproto.repo.listRecords", p)
        out += body.get("records", [])
        cursor = body.get("cursor")
        if not cursor:
            return out


async def _profiles(client, dids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    sem = asyncio.Semaphore(8)

    async def one(chunk: list[str]):
        async with sem:
            try:
                body = await _get(client, f"{BSKY}/app.bsky.actor.getProfiles", {"actors": chunk})
                for pr in body["profiles"]:
                    out[pr["did"]] = pr
            except httpx.HTTPError:
                pass

    chunks = [dids[i : i + 25] for i in range(0, len(dids), 25)]
    await asyncio.gather(*(one(c) for c in chunks))
    return out


@task(
    log_prints=True,
    retries=3,
    retry_delay_seconds=exponential_backoff(backoff_factor=5),
    retry_jitter_factor=1,
)
def build_pool() -> dict[str, dict]:
    async def run() -> dict[str, dict]:
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": "bisk-snapshot (zzstoatzz.io)"},
        ) as client:
            mini = await _get(
                client,
                f"{SLINGSHOT}/blue.microcosm.identity.resolveMiniDoc",
                {"identifier": DAVE},
            )
            followers, follows_recs = await asyncio.gather(
                _backlink_dids(client, DAVE, "app.bsky.graph.follow:subject"),
                _list_records(client, mini["pds"], DAVE, "app.bsky.graph.follow"),
            )
            follows = [r["value"]["subject"] for r in follows_recs]
            union = list({*followers, *follows, DAVE})
            profs = await _profiles(client, union)
            pool = {
                did: pr
                for did, pr in profs.items()
                if pr.get("followersCount", 0) < ENHANCED_GRACE_LIMIT
            }
            print(
                f"pool: {len(pool)} under the Enhanced Grace Limit "
                f"(EGL, {ENHANCED_GRACE_LIMIT:,} followers) of {len(union)} in graph"
            )
            return pool

    return asyncio.run(run())


@task(
    log_prints=True,
    retries=3,
    retry_delay_seconds=exponential_backoff(backoff_factor=5),
    retry_jitter_factor=1,
)
def window_bisks(pool: dict[str, dict]) -> list[dict]:
    """the pool's bisks in topchicken's *current* daily round, with live likes.

    the round is one UTC calendar day of posts — the one whose trading is open
    (it locks at 06:00 UTC the next day). mirroring that discrete day (rather
    than a rolling 24h lookback) is what makes the live race reset each round
    instead of letting the just-crowned post linger as leader for another ~24h.
    """
    window_start, window_end = round_bounds(datetime.now(UTC))
    print(
        f"round {window_start:%Y-%m-%d}: posts from that UTC day (locks {window_end:%m-%d} 06:00 UTC)"
    )

    async def run() -> list[dict]:
        bisks: list[dict] = []
        sem = asyncio.Semaphore(40)
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": "bisk-snapshot (zzstoatzz.io)"},
        ) as client:

            async def one(did: str, pr: dict):
                async with sem:
                    try:
                        body = await _get(
                            client,
                            f"{BSKY}/app.bsky.feed.getAuthorFeed",
                            {"actor": did, "limit": 60, "filter": "posts_no_replies"},
                        )
                    except httpx.HTTPError:
                        return
                for item in body.get("feed", []):
                    post = item["post"]
                    if post["author"]["did"] != did:  # skip reposts
                        continue
                    created = post["record"].get("createdAt", "")
                    try:
                        when = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    if not (window_start <= when < window_end) or post.get("likeCount", 0) < 1:
                        continue
                    bisks.append(
                        {
                            "uri": post["uri"],
                            "did": did,
                            "handle": pr["handle"],
                            "avatar": pr.get("avatar"),
                            "likes": post["likeCount"],
                        }
                    )

            await asyncio.gather(*(one(d, p) for d, p in pool.items()))
        bisks.sort(key=lambda b: b["likes"], reverse=True)
        print(
            f"window: {len(bisks)} bisks (≥1 like) from {len({b['did'] for b in bisks})} chickens"
        )
        return bisks

    return asyncio.run(run())


@task(
    log_prints=True,
    retries=3,
    retry_delay_seconds=exponential_backoff(backoff_factor=5),
    retry_jitter_factor=1,
)
def all_time_coop() -> list[dict]:
    async def run() -> list[dict]:
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": "bisk-snapshot (zzstoatzz.io)"},
        ) as client:
            mini = await _get(
                client,
                f"{SLINGSHOT}/blue.microcosm.identity.resolveMiniDoc",
                {"identifier": TOPCHICKEN},
            )
            recs = await _list_records(client, mini["pds"], TOPCHICKEN, "app.bsky.feed.post")
            winners = []
            for r in recs:
                v = r["value"]
                text = v.get("text", "")
                if "New Top Chicken" not in text:
                    continue
                uri = (v.get("embed") or {}).get("record", {}).get("uri")
                raw = text.split("got")[-1].split("likes")[0].replace(",", "").strip()
                if not uri or not raw.isdigit():
                    continue
                winners.append(
                    {
                        "uri": uri,
                        "did": uri.split("/")[2],
                        "likes": int(raw),
                        "crownedAt": v.get("createdAt", ""),
                    }
                )
            profs = await _profiles(client, list({w["did"] for w in winners}))

            # resolve each winning bisk. getPosts returns the post's own
            # createdAt (for an accurate label) and its author — and, crucially,
            # OMITS posts that no longer exist (deleted, or the author
            # deactivated). we drop those: a winner you can't open or read is
            # worse than not showing it (an absent author also has no handle, so
            # it would render as a raw DID).
            uris = [w["uri"] for w in winners]
            live_posts: dict[str, dict] = {}
            for i in range(0, len(uris), 25):
                body = await _get(
                    client, f"{BSKY}/app.bsky.feed.getPosts", {"uris": uris[i : i + 25]}
                )
                for p in body.get("posts", []):
                    live_posts[p["uri"]] = p

            kept = []
            for w in winners:
                post = live_posts.get(w["uri"])
                if post is None:
                    continue  # post/author gone — skip rather than show a dead 404
                author = post.get("author", {})
                pr = profs.get(w["did"], {})
                w["handle"] = pr.get("handle") or author.get("handle") or w["did"]
                w["avatar"] = pr.get("avatar") or author.get("avatar")
                w["postedAt"] = post.get("record", {}).get("createdAt", w["crownedAt"])
                kept.append(w)

            dropped = len(winners) - len(kept)
            kept.sort(key=lambda w: w["crownedAt"], reverse=True)
            print(f"coop: {len(kept)} viewable crownings ({dropped} dropped: gone/deactivated)")
            return kept

    return asyncio.run(run())


@task(
    log_prints=True,
    retries=2,
    retry_delay_seconds=exponential_backoff(backoff_factor=5),
    retry_jitter_factor=1,
)
def publish(snapshot: dict) -> str:
    """publish to R2, or to a local file if BISK_OUT is set (dev/CI)."""
    body = json.dumps(snapshot).encode()

    if local := os.getenv("BISK_OUT"):
        Path(local).write_text(json.dumps(snapshot))
        print(f"wrote {local} ({len(body) / 1024:.0f} KB)")
        return local

    import boto3  # provided via the deployment command's --with boto3

    bucket = os.environ["BISK_R2_BUCKET"]
    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["BISK_R2_ENDPOINT"],
        aws_access_key_id=os.environ["BISK_R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["BISK_R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )
    s3.put_object(
        Bucket=bucket,
        Key=OBJECT_KEY,
        Body=body,
        ContentType="application/json",
        CacheControl="public, max-age=60",
    )
    print(f"published s3://{bucket}/{OBJECT_KEY} ({len(body) / 1024:.0f} KB)")
    return f"{bucket}/{OBJECT_KEY}"


@flow(name="bisk-snapshot", log_prints=True, timeout_seconds=540)
def bisk_snapshot() -> None:
    pool = build_pool()
    snapshot = {
        "generatedAt": datetime.now(UTC).isoformat(),
        "graceLimit": GRACE_LIMIT,
        # pool carries identities (not just dids) so the client can offer a
        # cmd-k search across the whole run, not only the windowed posters.
        "pool": [
            {"did": did, "handle": pr["handle"], "displayName": pr.get("displayName")}
            for did, pr in sorted(pool.items())
        ],
        "live": window_bisks(pool),
        "coop": all_time_coop(),
    }
    publish(snapshot)


if __name__ == "__main__":
    bisk_snapshot()
