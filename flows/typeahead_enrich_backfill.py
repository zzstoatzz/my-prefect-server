"""drain the typeahead profile-enrichment backlog (profile_checked_at=0).

THE OWNER of the multi-million-actor profile backlog, per typeahead's cron
contract (its CLAUDE.md): the worker's hourly Cloudflare cron is sized to
keep pace with arrivals (~2,900 new actors/hour) and explicitly does NOT
drain history — that is this flow's job. Scheduled daily; each run walks
the queue oldest-first → app.bsky.actor.getProfiles (25 DIDs/call) → the
same score formula and write guards the worker uses → paced turso batches.

The queue is self-consuming: selection is a keyset walk over typeahead's
partial index idx_actors_refresh_queue (ON actors(updated_at) WHERE
profile_checked_at = 0 AND handle != ''), and stamping profile_checked_at
removes a row from the index — so every run starts at the true queue head
with no cursor state carried between runs. The worker's hourly event queue
reads the same index newest-first; this flow reads oldest-first, so the two
meet in the middle and never fight over rows. handle='' actors are NOT this
flow's scope — they are the identity backlog (typeahead-plc-identity).

At the scheduled limit=250,000/day the ~6M backlog drains in under a month,
and once drained each run just clears whatever the hourly cron's headroom
left behind. Trials by hand:

    prefect deployment run 'typeahead-enrich-backfill/typeahead-enrich-backfill' \
        -p limit=100 -p dry_run=true            # look, no writes
    prefect deployment run ... -p limit=1000 -p dry_run=false

Faithfulness notes (drift here corrupts ranking, so ported exactly):
  - score: utils.ts computeAuthorityScore, including the hasAggregates guard —
    a countless profile (appview gap) must score 0 so the NULLIF(?,0) write
    guard preserves any prior real score instead of clobbering it.
  - writes: cron.ts refreshModeration's COALESCE/NULLIF UPDATE, plus
    profile_checked_at=unixepoch() so the row leaves the queue.
  - moderation: hide-only. An actor whose labels carry a bsky-mod hide value
    gets hidden=1; we NEVER set hidden=0 (no override/domain/slur logic here —
    that stays the worker's job, and unhiding is not this flow's business).
  - not-found DIDs still get profile_checked_at stamped (cron does the same)
    so dead actors cannot pin the queue.
"""

from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from prefect import flow, get_run_logger

APPVIEW = "https://public.api.bsky.app/xrpc/app.bsky.actor.getProfiles"
BSKY_MOD_DID = "did:plc:ar7c4by46qjdydhdevvrndac"
MOD_HIDE_VALS = {"!hide", "!takedown", "!suspend", "spam"}
GETPROFILES_MAX = 25
SELECT_PAGE = 500  # keyset page off idx_actors_refresh_queue


def _turso() -> tuple[str, dict[str, str]]:
    url = os.environ["TURSO_URL"].replace("libsql://", "https://").rstrip("/")
    token = os.environ["TURSO_AUTH_TOKEN"]
    return f"{url}/v2/pipeline", {"Authorization": f"Bearer {token}"}


def _tq(client: httpx.Client, stmts: list[dict[str, Any]]) -> list[Any]:
    """run statements through one hrana pipeline request; raise on any error."""
    url, headers = _turso()
    reqs = [{"type": "execute", "stmt": s} for s in stmts] + [{"type": "close"}]
    r = client.post(url, headers=headers, json={"requests": reqs}, timeout=60)
    r.raise_for_status()
    out = []
    for res in r.json()["results"]:
        if res.get("type") == "error":
            raise RuntimeError(f"turso: {res['error']}")
        if "result" in res.get("response", {}):
            out.append(res["response"]["result"])
    return out


def _arg(v: Any) -> dict[str, str]:
    return {"type": "text", "value": str(v)}


def compute_authority_score(
    followers: int, follows: int, posts: int, created_at: str | None, now_ms: float
) -> float:
    """port of utils.ts computeAuthorityScore — keep in lockstep with the worker."""
    f = max(0, followers or 0)
    fw = max(0, follows or 0)
    pc = max(0, posts or 0)
    score = math.log10(f + 1) * 100 + math.log10(pc + 1) * 20
    if f > 100 and fw > 0:
        ratio = f / fw
        if ratio > 10:
            score += 50
        if ratio > 100:
            score += 100
    if created_at:
        try:
            t = datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp() * 1000
            age_days = (now_ms - t) / 86400000
            if age_days < 1:
                score -= 100
            elif age_days < 3:
                score -= 50
            if age_days > 365:
                score += 30
            if age_days > 365 * 3:
                score += 30
        except ValueError:
            pass
    return round(max(0.0, score) * 100) / 100


def _mod_hidden(labels: list[dict[str, Any]] | None) -> bool:
    return any(
        not l.get("neg") and l.get("src") == BSKY_MOD_DID and l.get("val") in MOD_HIDE_VALS
        for l in (labels or [])
    )


def _avatar_cid(avatar_url: str) -> str:
    # worker stores the bare CID; appview URLs end /<did>/<cid>@jpeg
    if not avatar_url:
        return ""
    tail = avatar_url.rstrip("/").rsplit("/", 1)[-1]
    return tail.split("@", 1)[0]


@flow(name="typeahead-enrich-backfill", log_prints=True, timeout_seconds=14400)
def typeahead_enrich_backfill(
    limit: int = 100,
    dids_per_second: float = 50.0,
    write_batch: int = 50,
    dry_run: bool = True,
) -> dict[str, Any]:
    """
    limit:           actors to process this run (trial: 100; scheduled: 250k)
    dids_per_second: appview pacing. 50 = two getProfiles calls/s. measured
                     capacity ~154/s but the hourly cron shares the budget.
                     25 made the 250k run miss the 4h timeout once per-page
                     overhead (turso + write flushes) ate the pacing slack —
                     and with the queue tail ~94% not_found, real appview
                     load is far below the pace anyway.
    write_batch:     turso statements per pipeline request (paced 150ms apart —
                     turso is single-writer; never blast it)
    dry_run:         fetch + compute + report, write nothing (default!)
    """
    logger = get_run_logger()
    http = httpx.Client(timeout=30)
    now_ms = time.time() * 1000

    processed = found = not_found = hidden_set = 0
    pending_writes: list[dict[str, Any]] = []
    samples: list[str] = []
    # keyset over (updated_at, rowid) — the order idx_actors_refresh_queue
    # provides, so both the filter and the ORDER BY are index-served. The
    # guarded `>= ?1 AND (> ?1 OR ...)` shape is deliberate: the plain
    # OR-keyset degrades to a full index scan on Turso when the values arrive
    # as bound params (typeahead's Aug 2026 staleness incident; its smoke
    # suite EXPLAIN-guards the same shape on idx_actors_updated_at).
    cur_updated, cur_rowid = 0, 0
    call_interval = GETPROFILES_MAX / max(dids_per_second, 0.1)

    def flush_writes() -> None:
        nonlocal pending_writes
        while pending_writes:
            chunk, pending_writes = pending_writes[:write_batch], pending_writes[write_batch:]
            _tq(http, chunk)
            time.sleep(0.15)

    while processed < limit:
        page = _tq(http, [{
            "sql": "SELECT rowid, did, updated_at FROM actors "
                   "WHERE profile_checked_at = 0 AND handle != '' "
                   "AND updated_at >= ?1 AND (updated_at > ?1 OR rowid > ?2) "
                   "ORDER BY updated_at, rowid LIMIT ?3",
            "args": [_arg(cur_updated), _arg(cur_rowid),
                     _arg(min(SELECT_PAGE, limit - processed))],
        }])[0].get("rows", [])
        if not page:
            logger.info("queue drained at updated_at=%s rowid=%s", cur_updated, cur_rowid)
            break
        rows = [(int(r[0]["value"]), r[1]["value"]) for r in page]
        cur_updated, cur_rowid = int(page[-1][2]["value"]), rows[-1][0]

        for i in range(0, len(rows), GETPROFILES_MAX):
            batch = rows[i : i + GETPROFILES_MAX]
            dids = [d for _, d in batch]
            t0 = time.time()

            profiles: dict[str, Any] = {}
            for attempt in range(3):
                try:
                    r = http.get(APPVIEW, params=[("actors", d) for d in dids])
                    if r.status_code == 429:
                        logger.warning("appview 429 — backing off 30s (attempt %d)", attempt + 1)
                        time.sleep(30)
                        continue
                    r.raise_for_status()
                    profiles = {p["did"]: p for p in r.json().get("profiles", [])}
                    break
                except httpx.HTTPError as e:
                    logger.warning("getProfiles failed (%s), attempt %d", e, attempt + 1)
                    time.sleep(5 * (attempt + 1))

            for rowid, did in batch:
                processed += 1
                p = profiles.get(did)
                if not p:
                    not_found += 1
                    pending_writes.append({
                        "sql": "UPDATE actors SET profile_checked_at = unixepoch() WHERE did = ?1",
                        "args": [_arg(did)],
                    })
                    continue
                found += 1
                followers = int(p.get("followersCount") or 0)
                follows = int(p.get("followsCount") or 0)
                posts = int(p.get("postsCount") or 0)
                created = p.get("createdAt") or ""
                # utils.ts hasAggregates guard: a countless profile must emit
                # score 0 so NULLIF(?,0) preserves any prior real score.
                has_aggregates = any(
                    p.get(k) is not None for k in ("followersCount", "followsCount", "postsCount")
                )
                score = (
                    compute_authority_score(followers, follows, posts, created, now_ms)
                    if has_aggregates else 0.0
                )
                labels = p.get("labels") or []
                pending_writes.append({
                    "sql": (
                        "UPDATE actors SET "
                        "handle = COALESCE(NULLIF(?2, ''), handle), "
                        "display_name = COALESCE(NULLIF(?3, ''), display_name), "
                        "avatar_url = COALESCE(NULLIF(?4, ''), avatar_url), "
                        "labels = ?5, "
                        "created_at = COALESCE(NULLIF(?6, ''), created_at), "
                        "followers_count = COALESCE(NULLIF(?7, 0), followers_count), "
                        "follows_count = COALESCE(NULLIF(?8, 0), follows_count), "
                        "posts_count = COALESCE(NULLIF(?9, 0), posts_count), "
                        "quality_score = COALESCE(NULLIF(?10, 0), quality_score), "
                        "profile_checked_at = unixepoch(), updated_at = unixepoch() "
                        "WHERE did = ?1"
                    ),
                    "args": [
                        _arg(did), _arg(p.get("handle") or ""), _arg(p.get("displayName") or ""),
                        _arg(_avatar_cid(p.get("avatar") or "")), _arg(json.dumps(labels)),
                        _arg(created), _arg(followers), _arg(follows), _arg(posts), _arg(score),
                    ],
                })
                if _mod_hidden(labels):
                    hidden_set += 1
                    pending_writes.append({
                        "sql": "UPDATE actors SET hidden = 1 WHERE did = ?1",
                        "args": [_arg(did)],
                    })
                if len(samples) < 10:
                    samples.append(f"{p.get('handle') or did}: score={score} f={followers} p={posts}")

            if dry_run:
                pending_writes.clear()
            elif len(pending_writes) >= write_batch:
                flush_writes()

            elapsed = time.time() - t0
            if elapsed < call_interval:
                time.sleep(call_interval - elapsed)

        logger.info(
            "progress: processed=%d found=%d not_found=%d hidden=%d cursor=(%d,%d)",
            processed, found, not_found, hidden_set, cur_updated, cur_rowid,
        )

    if not dry_run:
        flush_writes()

    summary = {
        "dry_run": dry_run,
        "processed": processed,
        "found": found,
        "not_found": not_found,
        "hidden_set": hidden_set,
        "queue_cursor": {"updated_at": cur_updated, "rowid": cur_rowid},
        "samples": samples,
    }
    logger.info("done: %s", json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    typeahead_enrich_backfill()
