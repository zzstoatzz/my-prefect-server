"""strata — read the shape of the stream.waow.tech archive from outside.

Every sealed jss segment ends with a collection block index: one zstd frame
listing (nsid, event count) for the segment. The 256-byte header at offset 0
says where that index starts. Both are reachable with HTTP Range requests
against getSegment, so a segment's per-collection counts cost ~1.3 KB and two
requests instead of its ~270 MB body (docs/jss-format-v1.md in zat.dev/stream;
measured 2026-08-29 on segment 6000).

The flow is stateless and compaction-aware: each run lists every sealed
segment (a handful of requests), compares checksums with what the worker
already holds, and re-reads whatever is new or rewritten. Compaction rewrites
old segments continuously, so a segment's counts legitimately change after it
was first summarised; matching on checksum catches that. Runs on the home box;
this is a few KB per segment, not a firehose.

The archive key is rate-limited to 8 Mbps server-side; nothing here comes near
it. Concurrency is capped so a full first walk (≈7k segments) stays polite.
"""

import json
import os
import struct
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import zstandard
from prefect import flow, get_run_logger, task
from prefect.artifacts import create_markdown_artifact

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
    def __init__(self, url: str, token: str):
        self._url = url.rstrip("/")
        self._token = token

    def checksums(self) -> dict[int, str]:
        """What the worker already holds: segment index -> checksum it was summarised at."""
        with urllib.request.urlopen(
            request(f"{self._url}/api/checksums"), timeout=TIMEOUT_S
        ) as resp:
            return {int(s["idx"]): s["checksum"] for s in json.load(resp)["segments"]}

    def ingest(self, records: list[dict], archive_segments: int) -> None:
        req = request(
            f"{self._url}/ingest",
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
            data=json.dumps({"segments": records, "archiveSegments": archive_segments}).encode(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            ingested = json.load(resp)["ingested"]
        if ingested != len(records):
            raise RuntimeError(f"worker ingested {ingested} of {len(records)}")


def stale_or_new(listing: list[SegmentListing], known: dict[int, str]) -> list[SegmentListing]:
    """Segments to (re)read: not summarised yet, or summarised at a different checksum (compaction rewrote them)."""
    return [s for s in listing if known.get(s.idx) != s.checksum]


@task(retries=2, retry_delay_seconds=15, log_prints=True)
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


@flow(name="ingest-segment-collections", log_prints=True)
def ingest_segment_collections(max_segments: int = 2000) -> int:
    """Summarise every sealed segment the worker lacks or holds at a stale checksum; returns segments ingested."""
    log = get_run_logger()
    archive = Archive(os.environ["STREAM_ARCHIVE_KEY"])
    strata_api = Strata(os.environ["STRATA_URL"], os.environ["STRATA_INGEST_TOKEN"])
    listing = archive.list_all()
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

    create_markdown_artifact(
        key="strata",
        markdown=f"| sealed | held before | needed | ingested this run | skipped (mid-rewrite) |\n|---|---|---|---|---|\n| {len(listing)} | {len(known)} | {len(todo)} | {done} | {skipped} |",
        description="strata ingest progress",
    )
    return done
