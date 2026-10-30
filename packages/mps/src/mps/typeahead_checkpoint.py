"""Bounded SQLite checkpoint export with committed WAL replay.

WAL encoding follows the official Turso CLI's ExportWAL implementation:
https://github.com/tursodatabase/turso-cli/blob/main/internal/turso/tursoServer.go
Unlike that helper, HTTP errors never stand in for a successful WAL boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import struct
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PAGE = 4096
FRAME = PAGE + 24
BATCH = 128


class CheckpointError(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise CheckpointError("Source redirect prohibited")


def checksum(data: bytes | bytearray, state=(0, 0)):
    first, second = state
    for left, right in struct.iter_unpack("<II", data):
        first = (first + left + second) & 0xFFFFFFFF
        second = (second + right + first) & 0xFFFFFFFF
    return first, second


class Export:
    def __init__(self, url, token, seconds, maximum, allow_test_loopback):
        parsed = urllib.parse.urlsplit(url.replace("libsql://", "https://"))
        test = (
            allow_test_loopback
            and parsed.scheme == "http"
            and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        )
        if (
            (parsed.scheme != "https" and not test)
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/", "/v2/pipeline"}
        ):
            raise CheckpointError("Invalid source origin")
        if not token or any(character in token for character in "\r\n\0"):
            raise CheckpointError("Invalid source credential")
        if not 1 <= seconds <= 300 or not PAGE <= maximum <= 16 * 1024**3:
            raise CheckpointError("Invalid checkpoint bounds")
        self.origin = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        self.token, self.maximum = token, maximum
        self.deadline = time.monotonic() + seconds
        self.received = self.requests = 0
        self.opener = urllib.request.build_opener(NoRedirect())

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise CheckpointError("Checkpoint deadline exceeded")
        return min(10, remaining)

    def open(self, path, body=None):
        self.requests += 1
        request = urllib.request.Request(
            self.origin + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"},
        )
        try:
            return self.opener.open(request, timeout=self.remaining())
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            raise CheckpointError("Source HTTP " + str(status)) from None
        except (OSError, urllib.error.URLError):
            raise CheckpointError("Source transport failed") from None

    def charge(self, data):
        self.remaining()
        self.received += len(data)
        if self.received > self.maximum:
            raise CheckpointError("Checkpoint byte budget exceeded")

    def read(self, path, maximum, body=None):
        data = bytearray()
        with self.open(path, body) as response:
            while True:
                self.remaining()
                chunk = response.read1(min(64 * 1024, maximum + 1 - len(data)))
                self.charge(chunk)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > maximum:
                    raise CheckpointError("Source response too large")
        return data

    def info(self):
        value = json.loads(self.read("/info", 65536))
        if not isinstance(value, dict):
            raise CheckpointError("Invalid source info")
        generation = value.get("current_generation")
        if type(generation) is not int or not 0 <= generation < 2**31:
            raise CheckpointError("Invalid source generation")
        if value.get("db_type", "sqlite") != "sqlite":
            raise CheckpointError("SQLite source required")
        return generation

    def clock(self):
        body = {
            "requests": [
                {
                    "type": "execute",
                    "stmt": {
                        "sql": "SELECT unixepoch() AS source_now",
                        "args": [],
                        "want_rows": True,
                    },
                },
                {"type": "close"},
            ]
        }
        value = json.loads(self.read("/v2/pipeline", 65536, body))
        try:
            result = value["results"][0]
            cell = result["response"]["result"]["rows"][0][0]
            if result["type"] != "ok" or cell["type"] != "integer":
                raise CheckpointError("Invalid source clock")
            value = cell["value"]
            if not isinstance(value, str) or not value.isdecimal() or int(value) <= 0:
                raise CheckpointError("Invalid source clock")
            return int(value)
        except (KeyError, IndexError, TypeError, ValueError):
            raise CheckpointError("Invalid source clock") from None


def file_sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fetch_checkpoint(
    url,
    token,
    destination,
    *,
    max_seconds=180,
    max_bytes=8 * 1024**3,
    max_wal_bytes=64 * 1024**2,
    allow_test_loopback=False,
):
    export = Export(url, token, max_seconds, max_bytes, allow_test_loopback)
    if not FRAME <= max_wal_bytes <= 512 * 1024**2:
        raise CheckpointError("Invalid WAL budget")
    destination = Path(destination)
    if not destination.is_absolute() or not destination.parent.is_dir():
        raise CheckpointError("Absolute checkpoint destination required")
    if destination.exists() or destination.is_symlink():
        raise CheckpointError("Checkpoint destination already exists")
    staging = Path(tempfile.mkdtemp(prefix=".checkpoint-", dir=destination.parent))
    working = staging / "source.db"
    wal = Path(str(working) + "-wal")
    started = time.monotonic()
    try:
        before_clock = export.clock()
        generation = export.info()
        base_hash = hashlib.sha256()
        with working.open("xb") as output, export.open(f"/export/{generation}") as response:
            while True:
                export.remaining()
                data = response.read1(64 * 1024)
                export.charge(data)
                if not data:
                    break
                output.write(data)
                base_hash.update(data)
            output.flush()
            os.fsync(output.fileno())
        base_size = working.stat().st_size
        with working.open("rb") as stream:
            header = stream.read(100)
        if (
            len(header) != 100
            or header[:16] != b"SQLite format 3\0"
            or struct.unpack(">H", header[16:18])[0] != PAGE
            or base_size % PAGE
        ):
            raise CheckpointError("Invalid SQLite checkpoint file")
        salt = os.urandom(8)
        wal_header = struct.pack(">IIII", 0x377F0682, 3007000, PAGE, 0) + salt
        state = checksum(wal_header)
        wal_header += struct.pack(">II", *state)
        raw_hash = hashlib.sha256()
        committed_raw_sha = raw_hash.hexdigest()
        next_frame, committed, received_frames, committed_pages = 1, 0, 0, 0
        with wal.open("xb") as output:
            output.write(wal_header)
            while True:
                raw = export.read(
                    f"/sync/{generation}/{next_frame}/{next_frame + BATCH}", BATCH * FRAME
                )
                if len(raw) % FRAME:
                    raise CheckpointError("Truncated source WAL frame")
                count = len(raw) // FRAME
                if (received_frames + count) * FRAME > max_wal_bytes:
                    raise CheckpointError("WAL byte budget exceeded")
                for offset in range(count):
                    frame = bytearray(raw[offset * FRAME : (offset + 1) * FRAME])
                    page, database_pages = struct.unpack(">II", frame[:8])
                    if not page or page * PAGE > max_bytes or database_pages * PAGE > max_bytes:
                        raise CheckpointError("Invalid source WAL page")
                    raw_hash.update(frame)
                    frame[8:16] = salt
                    state = checksum(frame[:8], state)
                    state = checksum(frame[24:], state)
                    frame[16:24] = struct.pack(">II", *state)
                    output.write(frame)
                    if database_pages:
                        committed = next_frame + offset
                        committed_pages = database_pages
                        committed_raw_sha = raw_hash.hexdigest()
                received_frames += count
                next_frame += count
                if count < BATCH:
                    break
            output.truncate(32 + committed * FRAME)
            output.flush()
            os.fsync(output.fileno())
        wal_sha = file_sha(wal)
        wal_size = wal.stat().st_size
        after_generation = export.info()
        after_clock = export.clock()
        if after_generation < generation or after_clock < before_clock:
            raise CheckpointError("Source generation or clock regressed")
        export.remaining()
        with sqlite3.connect(working, timeout=export.remaining()) as database:
            database.execute("PRAGMA schema_version").fetchone()
            applied = database.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
            if applied != (0, committed, committed):
                raise CheckpointError("SQLite did not verify the committed WAL prefix")
            pages = database.execute("PRAGMA page_count").fetchone()[0]
            if committed and pages != committed_pages:
                raise CheckpointError("SQLite committed page count mismatch")
            checkpoint = database.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint != (0, 0, 0):
                raise CheckpointError("SQLite WAL replay did not complete")
            if database.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                raise CheckpointError("SQLite checkpoint did not become standalone")
        export.remaining()
        final_sha = file_sha(working)
        export.remaining()
        receipt = {
            "schema_version": 1,
            "kind": "sqlite-committed-prefix",
            "fixture": allow_test_loopback,
            "generation": generation,
            "generation_after": after_generation,
            "source_origin_sha256": hashlib.sha256(export.origin.encode()).hexdigest(),
            "source_clock_before": before_clock,
            "source_clock_after": after_clock,
            "base_db_sha256": base_hash.hexdigest(),
            "base_db_bytes": base_size,
            "received_wal_frames": received_frames,
            "committed_frame": committed,
            "committed_provider_frames_sha256": committed_raw_sha,
            "replayed_wal_sha256": wal_sha,
            "replayed_wal_bytes": wal_size,
            "database_sha256": final_sha,
            "database_bytes": working.stat().st_size,
            "sqlite_replay_verified": True,
            "sqlite_verified_wal_frames": applied[2],
            "boundary_kind": "successful-short-WAL-batch",
            "received_source_bytes": export.received,
            "source_requests": export.requests,
            "wall_seconds": time.monotonic() - started,
            "live_coverage_verified": False,
            "policy_freshness_attested": False,
        }
        os.link(working, destination)
        working.unlink()
        directory = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return receipt
    finally:
        shutil.rmtree(staging)
