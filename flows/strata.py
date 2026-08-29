"""strata — read the shape of the stream.waow.tech archive from outside.

Every sealed jss segment ends with a collection block index: one zstd frame
listing (nsid, event count) for the segment. The 256-byte header at offset 0
says where that index starts. Both are reachable with HTTP Range requests
against getSegment, so a segment's per-collection counts cost ~1.3 KB and two
requests instead of its ~270 MB body (docs/jss-format-v1.md in zat.dev/stream;
measured 2026-08-29 on segment 6000).

The flow is stateless: it asks the strata worker how far it has got, lists
segments past that via listSegments, decodes each new one, and POSTs batches.
Reruns are idempotent (the worker upserts). Runs on the home box; this is a
few KB per segment, not a firehose.

The archive key is rate-limited to 8 Mbps server-side; nothing here comes near
it. Concurrency is capped so a full first walk (≈7k segments) stays polite.
"""

import json
import os
import struct
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


def parse_header(raw: bytes) -> SegmentHeader:
    if len(raw) != HEADER_LEN or raw[:4] != JSS_MAGIC:
        raise ValueError("not a jss v1 header")
    checksum = struct.unpack_from("<Q", raw, 4)[0]
    version, block_count, event_count = struct.unpack_from("<HII", raw, 12)
    if version != 1:
        raise ValueError(f"unsupported jss version {version}")
    min_seq, max_seq = struct.unpack_from("<QQ", raw, 26)
    collection_index_offset = struct.unpack_from("<Q", raw, 82)[0]
    return SegmentHeader(checksum, block_count, event_count, min_seq, max_seq, collection_index_offset)


def parse_collection_index(raw: bytes) -> list[tuple[str, int]]:
    collection_count, _block_count, _bitmask_len, uncompressed_size = struct.unpack_from("<IIII", raw, 0)
    body = zstandard.ZstdDecompressor().decompressobj().decompress(raw[16:])
    if len(body) != uncompressed_size:
        raise ValueError(f"collection index decoded to {len(body)} bytes, header says {uncompressed_size}")
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
        req = urllib.request.Request(f"{ARCHIVE_URL}{path}", headers=headers)
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return resp.status, resp.read()

    def list_segments(self, after_idx: int) -> list[SegmentListing]:
        cursor = "" if after_idx < 0 else f"&cursor={after_idx}"
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

    def collections(self, seg: SegmentListing) -> list[tuple[str, int]]:
        path = f"/xrpc/network.bsky.jetstream.getSegment?name={seg.name}"
        status, raw = self._get(path, f"0-{HEADER_LEN - 1}")
        if status != 206:
            raise RuntimeError(f"{seg.name}: header request returned {status}, not 206")
        header = parse_header(raw)
        if header.checksum == 0:
            raise RuntimeError(f"{seg.name}: unsealed (checksum 0) despite being listed")
        if header.event_count != seg.event_count or header.min_seq != seg.min_seq:
            raise RuntimeError(f"{seg.name}: header disagrees with listSegments")
        status, raw = self._get(path, f"{header.collection_index_offset}-")
        if status != 206:
            raise RuntimeError(f"{seg.name}: collection index request returned {status}, not 206")
        return parse_collection_index(raw)


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

    def progress(self) -> int:
        with urllib.request.urlopen(f"{self._url}/api/progress", timeout=TIMEOUT_S) as resp:
            max_idx = json.load(resp)["maxIdx"]
        return -1 if max_idx is None else int(max_idx)

    def ingest(self, records: list[dict]) -> None:
        req = urllib.request.Request(
            f"{self._url}/ingest",
            data=json.dumps({"segments": records}).encode(),
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            ingested = json.load(resp)["ingested"]
        if ingested != len(records):
            raise RuntimeError(f"worker ingested {ingested} of {len(records)}")


@task(retries=2, retry_delay_seconds=15, log_prints=True)
def ingest_batch(archive: Archive, strata: Strata, segs: list[SegmentListing]) -> int:
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        records = [segment_record(s, c) for s, c in zip(segs, pool.map(archive.collections, segs))]
    strata.ingest(records)
    return len(records)


@flow(name="strata", log_prints=True)
def strata(max_segments: int = 2000) -> int:
    """Walk sealed segments past the worker's progress and ingest them; returns segments ingested."""
    log = get_run_logger()
    archive = Archive(os.environ["STREAM_ARCHIVE_KEY"])
    strata_api = Strata(os.environ["STRATA_URL"], os.environ["STRATA_INGEST_TOKEN"])
    start = strata_api.progress()
    log.info("worker progress: max idx %d", start)

    after = start
    done = 0
    while done < max_segments:
        page = archive.list_segments(after)
        if not page:
            break
        for i in range(0, len(page), BATCH):
            chunk = page[i : i + BATCH]
            done += ingest_batch(archive, strata_api, chunk)
            after = chunk[-1].idx
            if done >= max_segments:
                break
        log.info("ingested through idx %d (%d this run)", after, done)

    create_markdown_artifact(
        key="strata",
        markdown=f"| started after | ingested through | segments this run |\n|---|---|---|\n| {start} | {after} | {done} |",
        description="strata ingest progress",
    )
    return done
