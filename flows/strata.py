"""strata — read the shape of the stream.waow.tech archive from outside.

Every sealed jss segment ends with a collection block index: one zstd frame
listing (nsid, event count) for the segment. The 256-byte header at offset 0
says where that index starts. Both are reachable with HTTP Range requests
against getSegment, so a segment's per-collection counts cost ~1.3 KB and two
requests instead of its ~270 MB body (docs/jss-format-v1.md in zat.dev/stream;
measured 2026-08-29 on segment 6000).

HeavyPad keeps a durable SQLite checkpoint. Each run lists sealed segments and
reads only changed footers, then publishes content-addressed serving summaries
to R2. Public requests never depend on HeavyPad availability. No record bodies
are collected and no D1 writes occur. A missing checkpoint requires an explicit
restore instead of silently crawling the archive again.
"""

import fcntl
import gzip
import json
import os
import sqlite3
import struct
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import zstandard
from prefect import flow, get_run_logger, task
from prefect.artifacts import create_markdown_artifact
from prefect.cache_policies import NO_CACHE

from flows import strata_snapshot

ARCHIVE_URL = "https://stream.waow.tech"
LIST_PAGE = 500
BATCH = 50
WORKERS = 4
TIMEOUT_S = 30
USER_AGENT = "strata-flow/1 (+https://tangled.org/zat.dev/strata)"
HEADER_LEN = 256
JSS_MAGIC = b"jss0"


@dataclass(frozen=True)
class SegmentListing:
    idx: int
    name: str
    size_bytes: int
    checksum: str
    event_count: int
    block_count: int
    min_seq: int
    max_seq: int
    min_witnessed_at: int
    max_witnessed_at: int


@dataclass(frozen=True)
class SegmentHeader:
    checksum: int
    block_count: int
    event_count: int
    min_seq: int
    max_seq: int
    collection_index_offset: int


def request(
    url: str, headers: dict[str, str] | None = None, data: bytes | None = None, method: str = "GET"
) -> urllib.request.Request:
    """Every outbound request carries our user-agent: Cloudflare's zone rules 403 the default Python-urllib one."""
    return urllib.request.Request(
        url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})}, method=method
    )


def parse_header(raw: bytes) -> SegmentHeader:
    if len(raw) != HEADER_LEN or raw[:4] != JSS_MAGIC:
        raise ValueError("not a jss v1 header")
    checksum = struct.unpack_from("<Q", raw, 4)[0]
    version, block_count, event_count = struct.unpack_from("<HII", raw, 12)
    if version != 1:
        raise ValueError(f"unsupported jss version {version}")
    min_seq, max_seq = struct.unpack_from("<QQ", raw, 26)
    collection_index_offset = struct.unpack_from("<Q", raw, 82)[0]
    return SegmentHeader(
        checksum, block_count, event_count, min_seq, max_seq, collection_index_offset
    )


def parse_collection_index(raw: bytes) -> list[tuple[str, int]]:
    collection_count, _block_count, _bitmask_len, uncompressed_size = struct.unpack_from(
        "<IIII", raw, 0
    )
    body = zstandard.ZstdDecompressor().decompressobj().decompress(raw[16:])
    if len(body) != uncompressed_size:
        raise ValueError(
            f"collection index decoded to {len(body)} bytes, header says {uncompressed_size}"
        )
    out: list[tuple[str, int]] = []
    pos = 0
    for _ in range(collection_count):
        length = body[pos]
        count = struct.unpack_from("<I", body, pos + 1)[0]
        nsid = body[pos + 5 : pos + 5 + length].decode()
        pos += 5 + length
        out.append((nsid, count))
    return out


class Archive:
    def __init__(self, key: str):
        self._headers = {"Authorization": f"Bearer {key}"}

    def _get(self, path: str, byte_range: str | None = None) -> tuple[int, bytes]:
        headers = dict(self._headers)
        if byte_range:
            headers["Range"] = f"bytes={byte_range}"
        with urllib.request.urlopen(
            request(f"{ARCHIVE_URL}{path}", headers), timeout=TIMEOUT_S
        ) as resp:
            return resp.status, resp.read()

    def list_all(self) -> list[SegmentListing]:
        """Every sealed segment the archive lists, in index order."""
        out: list[SegmentListing] = []
        cursor = ""
        while True:
            page = self._list_page(cursor)
            out.extend(page)
            if len(page) < LIST_PAGE:
                return out
            cursor = f"&cursor={page[-1].idx}"

    def _list_page(self, cursor: str) -> list[SegmentListing]:
        _, body = self._get(f"/xrpc/network.bsky.jetstream.listSegments?limit={LIST_PAGE}{cursor}")
        page = json.loads(body)
        return [
            SegmentListing(
                idx=s["index"],
                name=s["name"],
                size_bytes=s["sizeBytes"],
                checksum=s["checksum"],
                event_count=s["eventCount"],
                block_count=s["blockCount"],
                min_seq=s["minSeq"],
                max_seq=s["maxSeq"],
                min_witnessed_at=s["minWitnessedAt"],
                max_witnessed_at=s["maxWitnessedAt"],
            )
            for s in page["segments"]
        ]

    def collections(
        self, seg: SegmentListing
    ) -> tuple[SegmentListing, list[tuple[str, int]]] | None:
        """The segment's per-collection counts, with the listing they belong to. Compaction can rewrite a
        segment between listing and reading, and a rewrite of a live-era segment can take minutes; the
        header's checksum is the identity of the bytes, so when it differs from the listing's, re-list and
        read again a few times, then give the segment up for this run — the next run's checksum
        reconciliation picks it up."""
        for delay in (1, 3, 6):
            path = f"/xrpc/network.bsky.jetstream.getSegment?name={seg.name}"
            status, raw = self._get(path, f"0-{HEADER_LEN - 1}")
            if status != 206:
                raise RuntimeError(f"{seg.name}: header request returned {status}, not 206")
            header = parse_header(raw)
            if header.checksum == 0:
                raise RuntimeError(f"{seg.name}: unsealed (checksum 0) despite being listed")
            if f"{header.checksum:016x}" == seg.checksum:
                status, raw = self._get(path, f"{header.collection_index_offset}-")
                if status != 206:
                    raise RuntimeError(
                        f"{seg.name}: collection index request returned {status}, not 206"
                    )
                return seg, parse_collection_index(raw)
            time.sleep(delay)
            fresh = self._list_page(f"&cursor={seg.idx - 1}")
            found = next((f for f in fresh if f.name == seg.name), None)
            if found is None:
                return None
            seg = found
        return None


def segment_record(seg: SegmentListing, collections: list[tuple[str, int]]) -> dict:
    return {
        "idx": seg.idx,
        "name": seg.name,
        "sizeBytes": seg.size_bytes,
        "checksum": seg.checksum,
        "eventCount": seg.event_count,
        "blockCount": seg.block_count,
        "minSeq": seg.min_seq,
        "maxSeq": seg.max_seq,
        "minWitnessedAt": seg.min_witnessed_at,
        "maxWitnessedAt": seg.max_witnessed_at,
        "collections": [{"nsid": n, "count": c} for n, c in collections],
    }


class Strata:
    def __init__(self, path: Path, url: str, token: str):
        self._url = url.rstrip("/")
        self._token = token
        self.path = path
        if not path.exists():
            raise RuntimeError(
                "Strata checkpoint missing: restore the R2 checkpoint before running; refusing an implicit full crawl"
            )
        self.db = strata_snapshot.connect(path)

    def checksums(self) -> dict[int, str]:
        return {
            r["idx"]: r["checksum"] for r in self.db.execute("SELECT idx,checksum FROM segments")
        }

    def ingest(self, records: list[dict], archive_segments: int) -> None:
        strata_snapshot.ingest(self.db, records, archive_segments)

    def quarantine(self) -> list[int]:
        return strata_snapshot.quarantine_bad_counts(self.db)

    def send(self, method: str, path: str, body: bytes) -> None:
        req = request(
            f"{self._url}/snapshot/{path}",
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
            data=body,
            method=method,
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            resp.read()

    def publish(self) -> int:
        manifest, objects = strata_snapshot.build(self.db)
        if not strata_snapshot.needs_publication(self.db, manifest):
            return 0
        with tempfile.TemporaryDirectory(prefix="strata-backup-") as tmp:
            backup = Path(tmp) / "checkpoint.sqlite"
            with sqlite3.connect(backup) as target:
                self.db.backup(target)
            self.send("PUT", "checkpoint", gzip.compress(backup.read_bytes(), compresslevel=3))
        return strata_snapshot.publish(self.db, manifest, objects, self.send)


def stale_or_new(listing: list[SegmentListing], known: dict[int, str]) -> list[SegmentListing]:
    """Segments to (re)read: not summarised yet, or summarised at a different checksum (compaction rewrote them)."""
    return [s for s in listing if known.get(s.idx) != s.checksum]


@task(
    retries=3,
    retry_delay_seconds=[2, 5, 10],
    retry_jitter_factor=1,
    log_prints=True,
    cache_policy=NO_CACHE,
)
def ingest_batch(
    archive: Archive, strata: Strata, segs: list[SegmentListing], archive_segments: int
) -> tuple[int, int]:
    """Returns (ingested, skipped); skipped segments were mid-rewrite and are left for the next run."""
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        read = list(pool.map(archive.collections, segs))
    records = [segment_record(s, c) for r in read if r is not None for s, c in [r]]
    skipped = [s.name for s, r in zip(segs, read, strict=True) if r is None]
    if skipped:
        print(f"skipped {len(skipped)} segment(s) still being rewritten: {', '.join(skipped)}")
    if records:
        strata.ingest(records, archive_segments)
    return len(records), len(skipped)


def reconcile(max_segments: int, strata_api: Strata) -> int:
    """Summarise every sealed segment the worker lacks or holds at a stale checksum; returns segments ingested."""
    log = get_run_logger()
    archive = Archive(os.environ["STREAM_ARCHIVE_KEY"])
    listing = archive.list_all()
    quarantined = strata_api.quarantine()
    if quarantined:
        log.warning(
            "forgot %d segment(s) holding non-integer counts; re-reading them: %s",
            len(quarantined),
            quarantined[:20],
        )
    known = strata_api.checksums()
    todo = stale_or_new(listing, known)
    log.info(
        "archive lists %d sealed segments; worker holds %d; %d to read",
        len(listing),
        len(known),
        len(todo),
    )

    done = skipped = 0
    for i in range(0, min(len(todo), max_segments), BATCH):
        chunk = todo[i : i + BATCH]
        ingested, missed = ingest_batch(archive, strata_api, chunk, len(listing))
        done += ingested
        skipped += missed
        log.info(
            "ingested %d/%d (through idx %d), %d skipped",
            done,
            min(len(todo), max_segments),
            chunk[-1].idx,
            skipped,
        )

    strata_api.ingest([], len(listing))
    uploaded = publish_snapshot(strata_api)
    log.info("published snapshot: %d changed objects; no D1 writes", uploaded)
    create_markdown_artifact(
        key="strata",
        markdown=f"| sealed | held before | needed | ingested this run | skipped (mid-rewrite) |\n|---|---|---|---|---|\n| {len(listing)} | {len(known)} | {len(todo)} | {done} | {skipped} |",
        description="strata ingest progress",
    )
    return done


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1, cache_policy=NO_CACHE)
def publish_snapshot(strata: Strata) -> int:
    return strata.publish()


@flow(name="ingest-segment-collections", log_prints=True)
def ingest_segment_collections(max_segments: int = 2000, restore_checkpoint: bool = False) -> int:
    path = Path(os.environ["STRATA_STATE_PATH"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if restore_checkpoint:
            restore_snapshot_checkpoint(path)
        strata = Strata(path, os.environ["STRATA_URL"], os.environ["STRATA_INGEST_TOKEN"])
        try:
            return reconcile(max_segments, strata)
        finally:
            strata.db.close()


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1, cache_policy=NO_CACHE)
def restore_snapshot_checkpoint(path: Path) -> None:
    if path.exists():
        return
    req = request(
        os.environ["STRATA_URL"].rstrip("/") + "/snapshot/checkpoint",
        headers={"Authorization": "Bearer " + os.environ["STRATA_INGEST_TOKEN"]},
    )
    with tempfile.TemporaryDirectory(dir=path.parent, prefix="restore-") as tmp:
        restored = Path(tmp) / "checkpoint.sqlite"
        with (
            urllib.request.urlopen(req, timeout=120) as response,
            gzip.GzipFile(fileobj=response) as archive,
            restored.open("wb") as target,
        ):
            size = 0
            while chunk := archive.read(1 << 20):
                size += len(chunk)
                if size > 512 << 20:
                    raise ValueError("checkpoint exceeds restore limit")
                target.write(chunk)
        with sqlite3.connect(restored) as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("checkpoint failed integrity check")
            db.execute("DELETE FROM published_objects")
            db.execute("DROP TABLE IF EXISTS publication")
        os.replace(restored, path)
