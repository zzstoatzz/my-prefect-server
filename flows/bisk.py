"""compute the authoritative bisk.social snapshot and publish it to R2.

bisk.social is a ball pit of bisks (posts) sized by likes. the live race must be
the same for everyone and must not depend on any one browser's session — so we
compute the current standings here and publish a static snapshot the static site
adopts (the leaflet-search builder pattern; see flows/typeahead_index.py for the
sibling R2-snapshot flow).

  - pool  = dave's follows ∪ dave's followers, minus the 7k "Grace Limit"
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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from prefect import flow, task

DAVE = "did:plc:hovt6k22s64dq63jjmoyibk3"
TOPCHICKEN = "did:plc:bty3nc67lteylmmb7hvgxeu5"
GRACE_LIMIT = 7000
WINDOW = timedelta(hours=24)

CONSTELLATION = "https://constellation.microcosm.blue/xrpc"
SLINGSHOT = "https://slingshot.microcosm.blue/xrpc"
BSKY = "https://public.api.bsky.app/xrpc"
OBJECT_KEY = "bisk.json"


async def _get(client: httpx.AsyncClient, url: str, params: dict) -> dict:
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
                body = await _get(
                    client, f"{BSKY}/app.bsky.actor.getProfiles", {"actors": chunk}
                )
                for pr in body["profiles"]:
                    out[pr["did"]] = pr
            except httpx.HTTPError:
                pass

    chunks = [dids[i : i + 25] for i in range(0, len(dids), 25)]
    await asyncio.gather(*(one(c) for c in chunks))
    return out


@task(log_prints=True)
def build_pool() -> dict[str, dict]:
    async def run() -> dict[str, dict]:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
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
                if pr.get("followersCount", 0) < GRACE_LIMIT
            }
            print(f"pool: {len(pool)} under the Grace Limit (of {len(union)} in graph)")
            return pool

    return asyncio.run(run())


@task(log_prints=True)
def window_bisks(pool: dict[str, dict]) -> list[dict]:
    """each pool member's own posts in the trailing 24h, with current likes."""
    cutoff = datetime.now(timezone.utc) - WINDOW

    async def run() -> list[dict]:
        bisks: list[dict] = []
        sem = asyncio.Semaphore(40)
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:

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
                    if when < cutoff or post.get("likeCount", 0) < 1:
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
        print(f"window: {len(bisks)} bisks (≥1 like) from {len({b['did'] for b in bisks})} chickens")
        return bisks

    return asyncio.run(run())


@task(log_prints=True)
def all_time_coop() -> list[dict]:
    async def run() -> list[dict]:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
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
            for w in winners:
                pr = profs.get(w["did"], {})
                w["handle"] = pr.get("handle", w["did"])
                w["avatar"] = pr.get("avatar")
            winners.sort(key=lambda w: w["crownedAt"], reverse=True)
            print(f"coop: {len(winners)} crownings on the account")
            return winners

    return asyncio.run(run())


@task(log_prints=True)
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


@flow(name="bisk-snapshot", log_prints=True)
def bisk_snapshot() -> None:
    pool = build_pool()
    snapshot = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "graceLimit": GRACE_LIMIT,
        "pool": sorted(pool.keys()),
        "live": window_bisks(pool),
        "coop": all_time_coop(),
    }
    publish(snapshot)


if __name__ == "__main__":
    bisk_snapshot()
