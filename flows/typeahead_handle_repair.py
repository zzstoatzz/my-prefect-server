"""typeahead-handle-repair: one-off repair of handles the old #identity path wrote stale.

Until 2026-09-25 18:00Z the typeahead ingester answered each firehose
`#identity` by asking slingshot (an identity cache) seconds after the PLC op,
and wrote back the OLD handle for ~20-26% of handle changes. No scheduled job
rewrites a non-empty handle, so those rows stayed stale. The ingester now
resolves from the DID authority (typeahead 6ba533a); this flow repairs the
backlog it left.

Candidates:
  - `stale_path`: the drift scan's output (JSONL of {did, ours, plc}), rows
    whose handle is an older PLC handle as of the weekly bundle cutoff.
  - every DID with a non-genesis PLC op in [tail_after, tail_until), read from
    plc.directory/export — the changes after the bundle cutoff and before the
    fix deployed.

  - with `include_invalid_rows`, every row storing the literal handle.invalid
    (the appview's verification-failure placeholder, copied in by enrichment),
    so ones whose handle verifies again get it back.

For each candidate whose stored handle differs from the PLC claim, the DID
document is fetched live from plc.directory and its handle verified back to the
DID (/.well-known/atproto-did, then DNS TXT over DoH). A verified handle is
written; an unverified one is re-checked after `recheck_delay_s` and, if it
still fails, recorded as handle.invalid, matching the ingester (typeahead
ingest.zig/identity.zig) and the appview. Writes only land if the row still
holds the handle we read (compare-and-set),
so a concurrent live ingester write is never clobbered. `updated_at` is bumped
so the search overlay picks the row up; `profile_checked_at` is left alone.
Idempotent and re-runnable.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from prefect import flow, get_run_logger

from flows.typeahead_enrich_backfill import _arg, _ingestion_ready, _tq

PLC = "https://plc.directory"
DOH = "https://cloudflare-dns.com/dns-query"
UA = {"user-agent": "typeahead-handle-repair (zzstoatzz.io)"}
EXPORT_PAGE = 1000
EXPORT_INTERVAL_S = 0.6  # plc.directory export allows ~500 requests / 5 min
LOOKUP_PAGE = 500
WRITE_BATCH = 100
INVALID_HANDLE = "handle.invalid"

REPAIR_SQL = (
    "UPDATE actors SET handle = ?2, pds = COALESCE(NULLIF(?3, ''), pds), "
    "identity_checked_at = unixepoch(), updated_at = unixepoch() "
    "WHERE did = ?1 AND handle = ?4"
)


def handle_from_aka(aka: Iterable[str] | None) -> str | None:
    """the handle a DID claims: its first at:// alsoKnownAs entry, lowercased"""
    for entry in aka or []:
        if isinstance(entry, str) and entry.startswith("at://") and len(entry) > 5:
            return entry[5:].lower()
    return None


def pds_from_doc(doc: dict[str, Any]) -> str | None:
    for svc in doc.get("service") or []:
        if str(svc.get("id", "")).endswith("#atproto_pds"):
            return svc.get("serviceEndpoint")
    return None


def tail_claims(ops: Iterable[dict[str, Any]]) -> dict[str, str]:
    """latest claimed handle per DID across non-genesis, non-tombstone ops.

    genesis ops are new accounts, which the drift could not have made stale;
    later ops overwrite earlier ones because export is in createdAt order."""
    out: dict[str, str] = {}
    for o in ops:
        op = o.get("operation") or {}
        if o.get("nullified") or not op.get("prev") or op.get("type") == "plc_tombstone":
            continue
        h = handle_from_aka(op.get("alsoKnownAs"))
        if h is None and op.get("handle"):  # legacy create op shape
            h = str(op["handle"]).lower()
        if h:
            out[o["did"]] = h
    return out


def needs_check(ours: str | None, claimed: str) -> bool:
    """a row is a candidate when it exists and its handle is not the PLC claim"""
    return ours is not None and ours.lower() != claimed.lower()


def repair_statement(did: str, old: str, new: str, pds: str | None) -> dict[str, Any]:
    return {"sql": REPAIR_SQL, "args": [_arg(did), _arg(new), _arg(pds or ""), _arg(old)]}


def _load_stale(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    with path.open() as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                out[r["did"]] = str(r["plc"]).lower()
    return out


def _export_tail(http: httpx.Client, after: str, until: str, logger: Any) -> dict[str, str]:
    ops: list[dict[str, Any]] = []
    claims: dict[str, str] = {}
    cursor = after
    pages = 0
    while cursor < until:
        t0 = time.monotonic()
        for attempt in range(5):
            try:
                r = http.get(f"{PLC}/export", params={"after": cursor, "count": EXPORT_PAGE})
                if r.status_code == 429:
                    time.sleep(30 * (attempt + 1))
                    continue
                r.raise_for_status()
                break
            except httpx.HTTPError as e:
                logger.warning("export page failed (%s), attempt %d", e, attempt + 1)
                time.sleep(10 * (attempt + 1))
        else:
            raise RuntimeError(f"plc export failed repeatedly at {cursor}")
        ops = [json.loads(line) for line in r.text.splitlines() if line.strip()]
        if not ops:
            break
        claims.update(tail_claims(o for o in ops if o["createdAt"] < until))
        cursor = ops[-1]["createdAt"]
        pages += 1
        if pages % 100 == 0:
            logger.info("export: %d pages, at %s, %d tail DIDs", pages, cursor, len(claims))
        wait = EXPORT_INTERVAL_S - (time.monotonic() - t0)
        if wait > 0:
            time.sleep(wait)
    logger.info("export done: %d pages, %d tail DIDs", pages, len(claims))
    return claims


def _stored_handles(http: httpx.Client, dids: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for i in range(0, len(dids), LOOKUP_PAGE):
        page = dids[i : i + LOOKUP_PAGE]
        ph = ", ".join(f"?{n + 1}" for n in range(len(page)))
        res = _tq(
            http,
            [
                {
                    "sql": f"SELECT did, handle FROM actors WHERE did IN ({ph})",
                    "args": [_arg(d) for d in page],
                }
            ],
        )
        for row in res[0].get("rows", []):
            out[row[0]["value"]] = row[1]["value"] or ""
    return out


@dataclass(frozen=True)
class Resolved:
    """what the live DID document says, and whether its handle verified"""

    doc_found: bool
    claimed: str | None = None
    verified: bool = False
    pds: str | None = None


def _verify(http: httpx.Client, did: str) -> Resolved:
    r = http.get(f"{PLC}/{did}")
    if r.status_code != 200:
        return Resolved(doc_found=False)
    doc = r.json()
    handle = handle_from_aka(doc.get("alsoKnownAs"))
    pds = pds_from_doc(doc)
    if not handle:
        return Resolved(doc_found=True, pds=pds)
    try:
        wk = http.get(
            f"https://{handle}/.well-known/atproto-did", timeout=5, follow_redirects=False
        )
        if wk.status_code == 200 and wk.text.strip() == did:
            return Resolved(doc_found=True, claimed=handle, verified=True, pds=pds)
    except httpx.HTTPError:
        pass
    try:
        dns = http.get(
            DOH,
            params={"name": f"_atproto.{handle}", "type": "TXT"},
            headers={"accept": "application/dns-json"},
        )
        vals = [a.get("data", "").strip('"') for a in dns.json().get("Answer") or []]
        if [v for v in vals if v.startswith("did=")] == [f"did={did}"]:
            return Resolved(doc_found=True, claimed=handle, verified=True, pds=pds)
    except (httpx.HTTPError, ValueError):
        pass
    return Resolved(doc_found=True, claimed=handle, pds=pds)


def write_for(old: str, res: Resolved, final_pass: bool) -> str | None:
    """the handle to write for a row holding `old`, or None to leave it.

    A verified handle replaces anything different. An unverified claim is
    recorded as handle.invalid, but only on the final pass: a fresh domain can
    lag its DNS or well-known, so the first failure is re-checked later. A
    document with no handle, or no document, is left alone."""
    if res.claimed is None:
        return None
    if res.verified:
        return res.claimed if res.claimed != old.lower() else None
    if final_pass and old.lower() != INVALID_HANDLE:
        return INVALID_HANDLE
    return None


def _invalid_rows(http: httpx.Client) -> dict[str, str]:
    """DIDs whose row stores the literal handle.invalid (idx_actors_handle SEARCH)"""
    out: dict[str, str] = {}
    last = 0
    while True:
        res = _tq(
            http,
            [
                {
                    "sql": "SELECT rowid, did FROM actors WHERE handle = ?1 COLLATE NOCASE "
                    "AND rowid > ?2 ORDER BY rowid LIMIT ?3",
                    "args": [_arg(INVALID_HANDLE), _arg(last), _arg(LOOKUP_PAGE)],
                }
            ],
        )
        rows = res[0].get("rows", [])
        if not rows:
            return out
        for row in rows:
            out[row[1]["value"]] = ""
        last = int(rows[-1][0]["value"])


@flow(name="typeahead-handle-repair", log_prints=True, timeout_seconds=6 * 3600)
def typeahead_handle_repair(
    stale_path: str = "/home/stoat/drift-out2/stale.jsonl",
    tail_after: str = "2026-09-16T23:59:59.962Z",
    tail_until: str = "2026-09-25T18:01:00Z",
    include_invalid_rows: bool = True,
    recheck_delay_s: float = 900.0,
    workers: int = 8,
    dry_run: bool = False,
) -> dict[str, Any]:
    logger = get_run_logger()
    http = httpx.Client(timeout=15, headers=UA)

    claims = _load_stale(Path(stale_path))
    logger.info("stale scan: %d DIDs", len(claims))
    for did, h in _export_tail(http, tail_after, tail_until, logger).items():
        claims[did] = h
    if include_invalid_rows:
        invalid = _invalid_rows(http)
        logger.info("rows stored as %s: %d", INVALID_HANDLE, len(invalid))
        for did, h in invalid.items():
            claims.setdefault(did, h)
    logger.info("candidates before filtering: %d", len(claims))

    stored = _stored_handles(http, list(claims))
    todo = [(d, stored[d]) for d, h in claims.items() if needs_check(stored.get(d), h)]
    logger.info("rows whose handle differs from PLC or is invalid: %d", len(todo))

    counts = {"repaired": 0, "marked_invalid": 0, "changed": 0, "no_doc": 0, "no_handle": 0}
    pending: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal pending
        if pending and not dry_run:
            while not _ingestion_ready(http, time.time()):
                logger.info("ingester not ready; holding writes 60s")
                time.sleep(60)
            counts["changed"] += sum(
                int(res.get("affected_row_count", 0)) for res in _tq(http, pending)
            )
        pending = []

    def check(item: tuple[str, str]) -> tuple[str, str, Resolved]:
        did, old = item
        vclient = httpx.Client(timeout=10, headers=UA)
        try:
            return did, old, _verify(vclient, did)
        except httpx.HTTPError:
            return did, old, Resolved(doc_found=False)
        finally:
            vclient.close()

    def run_pass(items: list[tuple[str, str]], final_pass: bool) -> list[tuple[str, str]]:
        """write what this pass settles; return the unverified rows to re-check"""
        recheck: list[tuple[str, str]] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for n, (did, old, res) in enumerate(pool.map(check, items), 1):
                if not res.doc_found:
                    counts["no_doc"] += 1
                elif res.claimed is None:
                    counts["no_handle"] += 1
                elif not res.verified and not final_pass:
                    recheck.append((did, old))
                new = write_for(old, res, final_pass)
                if new is not None:
                    pending.append(repair_statement(did, old, new, res.pds))
                    counts["marked_invalid" if new == INVALID_HANDLE else "repaired"] += 1
                if len(pending) >= WRITE_BATCH:
                    flush()
                if n % 1000 == 0:
                    logger.info("progress (final=%s): %d/%d %s", final_pass, n, len(items), counts)
        flush()
        return recheck

    recheck = run_pass(todo, final_pass=False)
    logger.info("first pass done: %s; %d unverified to re-check", counts, len(recheck))
    if recheck:
        logger.info("waiting %.0fs before re-checking unverified handles", recheck_delay_s)
        time.sleep(recheck_delay_s)
        run_pass(recheck, final_pass=True)

    summary = {
        "dry_run": dry_run,
        "candidates": len(claims),
        "differing": len(todo),
        "rechecked": len(recheck),
        **counts,
    }
    logger.info("done: %s", summary)
    return summary


if __name__ == "__main__":
    typeahead_handle_repair(dry_run=True)
