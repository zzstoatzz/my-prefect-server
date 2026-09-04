"""typeahead-identity-hourly: give newly discovered actors their handles.

The relay's firehose discovers ~2,900 accounts/hour whose row lands in Turso
with handle='' until something resolves the DID. This was the first phase of
typeahead's hourly Cloudflare cron (`enrichActors`), which sat behind an
8-minute moderation refresh inside a 15-minute wall limit and was killed
mid-run in 120 of 140 invocations (2026-08-27..09-03). Here it has the whole
hour, the retrying Turso client, and nothing in front of it.

- selector: `handle = '' AND identity_checked_at < now-3600`, oldest first,
  off idx_actors_enrich_identity (EXPLAIN-verified SEARCH, 2026-09-03).
- resolver: the public appview's getProfiles, 25 DIDs/call, ~96% hit rate.
  The ~4% it cannot see (self-hosted PDSes) belong to typeahead-plc-identity.
- every selected DID gets identity_checked_at stamped, resolved or not, so an
  unresolvable DID cannot pin the queue.
- no unhiding, no counts, no labels: handle only. Profile enrichment is
  typeahead-enrich-backfill's job and a fresh handle enters its queue.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from prefect import flow, get_run_logger

from flows.typeahead_enrich_backfill import APPVIEW, GETPROFILES_MAX, _arg, _tq

IDENTITY_LIMIT = 6000  # 2× arrivals: catches up after a missed hour
SELECT_SQL = (
    "SELECT did FROM actors WHERE handle = '' AND identity_checked_at < unixepoch() - 3600 "
    "ORDER BY identity_checked_at ASC LIMIT ?1"
)


def resolve_batch(profiles: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(did, handle) for every profile the appview could name; handle.invalid is not a name"""
    return [
        (p["did"], p["handle"])
        for p in profiles
        if p.get("did") and p.get("handle") and p["handle"] != "handle.invalid"
    ]


def statements_for(batch_dids: list[str], resolved: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """the writes for one getProfiles batch: handles first, then the stamp for everyone"""
    stmts: list[dict[str, Any]] = [
        {
            "sql": "UPDATE actors SET handle = ?2, identity_checked_at = unixepoch(), updated_at = unixepoch() WHERE did = ?1",
            "args": [_arg(did), _arg(handle)],
        }
        for did, handle in resolved
    ]
    stmts += [
        {
            "sql": "UPDATE actors SET identity_checked_at = unixepoch() WHERE did = ?1 AND handle = ''",
            "args": [_arg(did)],
        }
        for did in batch_dids
    ]
    return stmts


@flow(name="typeahead-identity-hourly", log_prints=True, timeout_seconds=3000)
def typeahead_identity_hourly(
    limit: int = IDENTITY_LIMIT,
    budget_seconds: float = 2400.0,
    dids_per_second: float = 50.0,
    dry_run: bool = False,
) -> dict[str, Any]:
    sleep = time.sleep
    logger = get_run_logger()
    http = httpx.Client(timeout=30)
    deadline = time.monotonic() + budget_seconds
    call_interval = GETPROFILES_MAX / max(dids_per_second, 0.1)

    rows = _tq(http, [{"sql": SELECT_SQL, "args": [_arg(limit)]}])[0].get("rows", [])
    dids = [r[0]["value"] for r in rows]
    checked = resolved_n = 0
    budget_spent = False

    for i in range(0, len(dids), GETPROFILES_MAX):
        if time.monotonic() >= deadline:
            budget_spent = True
            break
        batch = dids[i : i + GETPROFILES_MAX]
        t0 = time.time()
        profiles: list[dict[str, Any]] = []
        for attempt in range(3):
            try:
                r = http.get(APPVIEW, params=[("actors", d) for d in batch])
                if r.status_code == 429:
                    logger.warning("appview 429 — backing off 30s (attempt %d)", attempt + 1)
                    sleep(30)
                    continue
                r.raise_for_status()
                profiles = r.json().get("profiles") or []
                break
            except httpx.HTTPError as e:
                logger.warning("getProfiles failed (%s), attempt %d", e, attempt + 1)
                sleep(5 * (attempt + 1))
        resolved = resolve_batch(profiles)
        checked += len(batch)
        resolved_n += len(resolved)
        if not dry_run:
            _tq(http, statements_for(batch, resolved))
        if checked % 1000 == 0:
            logger.info("progress: checked=%d resolved=%d", checked, resolved_n)
        elapsed = time.time() - t0
        if elapsed < call_interval:
            sleep(call_interval - elapsed)

    summary = {
        "dry_run": dry_run,
        "selected": len(dids),
        "checked": checked,
        "resolved": resolved_n,
        "budget_spent": budget_spent,
    }
    logger.info("done: %s", summary)
    return summary
