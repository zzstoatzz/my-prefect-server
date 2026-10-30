"""Canonical complete-checkpoint JSONL streaming; never a live admission attestation."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import signal
import sqlite3
import stat
import sys
import tempfile
import time
from contextlib import nullcontext
from pathlib import Path

from mps.typeahead_sqlite_seed import encode

DIGEST = re.compile(r"[a-f0-9]{64}")
BUILD = re.compile(r"b[0-9]{10}-[A-Za-z0-9_-]+")
WITNESS_KEYS = {"device", "inode", "byte_size", "mtime_ns", "ctime_ns"}
TEXT_LIMITS = {"did": 2048, "labels": 4 * 1024**2, "associated": 4 * 1024**2}
TEXT_FIELDS = {
    "did",
    "handle",
    "display_name",
    "avatar_url",
    "labels",
    "created_at",
    "associated",
    "pds",
}


def file_witness(path):
    value = Path(path).stat(follow_symlinks=False)
    if not stat.S_ISREG(value.st_mode):
        raise ValueError("Checkpoint must be a regular file")
    return {
        "device": value.st_dev,
        "inode": value.st_ino,
        "byte_size": value.st_size,
        "mtime_ns": value.st_mtime_ns,
        "ctime_ns": value.st_ctime_ns,
    }


def integer(value, name, minimum=0):
    if type(value) is not int or not minimum <= value < 2**63:
        raise ValueError("Invalid " + name)
    return value


def digest(value):
    if not isinstance(value, str) or not DIGEST.fullmatch(value):
        raise ValueError("Invalid checkpoint checksum")
    return value


def bound_json(raw, expected):
    if (
        not isinstance(raw, bytes)
        or len(raw) > 1024**2
        or hashlib.sha256(raw).hexdigest() != digest(expected)
    ):
        raise ValueError("Exact provenance bytes differ")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Provenance must be an object")
    return value


def checkpoint_identity(raw, expected_sha256, expected_origin, allow_fixture):
    if len(raw) > 16384:
        raise ValueError("Checkpoint receipt exceeds public metadata bound")
    value = bound_json(raw, expected_sha256)
    known = {
        "schema_version",
        "kind",
        "fixture",
        "generation",
        "generation_after",
        "source_origin_sha256",
        "source_clock_before",
        "source_clock_after",
        "base_db_sha256",
        "base_db_bytes",
        "received_wal_frames",
        "committed_frame",
        "committed_provider_frames_sha256",
        "replayed_wal_sha256",
        "replayed_wal_bytes",
        "database_sha256",
        "database_bytes",
        "sqlite_replay_verified",
        "sqlite_verified_wal_frames",
        "boundary_kind",
        "received_source_bytes",
        "source_requests",
        "wall_seconds",
        "live_coverage_verified",
        "policy_freshness_attested",
    }
    if set(value) != known:
        raise ValueError("Checkpoint receipt must contain only bounded nonsecret proof fields")
    if (
        value.get("schema_version") != 1
        or value.get("kind") != "sqlite-committed-prefix"
        or type(value.get("fixture")) is not bool
    ):
        raise ValueError("Explicit checkpoint receipt required")
    if value["fixture"] and not allow_fixture:
        raise ValueError("Fixture checkpoint cannot seed production")
    if (
        value.get("sqlite_replay_verified") is not True
        or value.get("boundary_kind") != "successful-short-WAL-batch"
        or value.get("live_coverage_verified") is not False
        or value.get("policy_freshness_attested") is not False
    ):
        raise ValueError("Verified unqualified checkpoint prefix required")
    if digest(value.get("source_origin_sha256")) != digest(expected_origin):
        raise ValueError("Checkpoint source identity differs")
    for name in [
        "base_db_sha256",
        "committed_provider_frames_sha256",
        "replayed_wal_sha256",
        "database_sha256",
    ]:
        digest(value.get(name))
    generation = integer(value.get("generation"), "generation")
    if integer(value.get("generation_after"), "later generation") < generation:
        raise ValueError("Checkpoint generation regressed")
    before = integer(value.get("source_clock_before"), "source clock", 1)
    if integer(value.get("source_clock_after"), "later clock", 1) < before:
        raise ValueError("Checkpoint clock regressed")
    for name in ["base_db_bytes", "database_bytes"]:
        size = integer(value.get(name), name, 4096)
        if size % 4096 or size > 8 * 1024**3:
            raise ValueError("Invalid checkpoint size")
    committed = integer(value.get("committed_frame"), "committed frame")
    if (
        integer(value.get("received_wal_frames"), "received frames") < committed
        or integer(value.get("sqlite_verified_wal_frames"), "verified frames") != committed
        or integer(value.get("replayed_wal_bytes"), "WAL bytes") != 32 + committed * 4120
    ):
        raise ValueError("Checkpoint committed prefix differs")
    received = integer(value["received_wal_frames"], "received frames")
    if (
        received * 4120 > 512 * 1024**2
        or integer(value["received_source_bytes"], "source bytes")
        < value["base_db_bytes"] + received * 4120
    ):
        raise ValueError("Checkpoint transfer accounting differs")
    integer(value["source_requests"], "source request count", 1)
    if (
        type(value["wall_seconds"]) not in (int, float)
        or not math.isfinite(value["wall_seconds"])
        or not 0 <= value["wall_seconds"] <= 3600
    ):
        raise ValueError("Invalid checkpoint duration")
    return value


def rollback_anchor(raw, expected_sha256):
    value = bound_json(raw, expected_sha256)
    if (
        value.get("manifest_version") != 2
        or value.get("format_version") != 5
        or not isinstance(value.get("active_build"), str)
        or not BUILD.fullmatch(value["active_build"])
    ):
        raise ValueError("Independent FLATPX05 rollback manifest required")
    twin = value.get("legacy_twin", {})
    if (
        not isinstance(twin.get("build_id"), str)
        or not BUILD.fullmatch(twin["build_id"])
        or twin.get("source_watermark") != value.get("source_watermark")
    ):
        raise ValueError("Rollback native twin differs")
    return {
        "build_id": value["active_build"],
        "manifest_sha256": expected_sha256,
        "database_sha256": digest(value.get("sha256")),
        "prefix_sha256": digest(value.get("prefix_map", {}).get("sha256")),
        "actor_count": integer(value.get("actor_count"), "native rollback count", 1),
        "source_watermark": integer(value.get("source_watermark"), "native rollback watermark", 1),
        "legacy_build_id": twin["build_id"],
    }


def stream_checkpoint(
    database,
    output,
    repository,
    *,
    receipt_bytes,
    receipt_sha256,
    expected_source_origin_sha256,
    expected_file_witness,
    native_manifest_bytes,
    native_manifest_sha256,
    build_id,
    overlap_seconds=300,
    max_seconds=1800,
    max_input_bytes=8 * 1024**3,
    max_output_bytes=16 * 1024**3,
    max_documents=50_000_000,
    allow_fixture=False,
    pds_fallback_path=None,
    pds_fallback_sha256=None,
):
    started = time.monotonic()
    if (
        not isinstance(build_id, str)
        or not BUILD.fullmatch(build_id)
        or type(overlap_seconds) is not int
        or not 0 <= overlap_seconds <= 3600
        or not 0 < max_seconds <= 5400
    ):
        raise ValueError("Invalid streaming identity/deadline")
    if (
        not 4096 <= max_input_bytes <= 8 * 1024**3
        or not 1 <= max_output_bytes <= 32 * 1024**3
        or not 1 <= max_documents <= 50_000_000
    ):
        raise ValueError("Invalid streaming byte/document bounds")
    checkpoint = checkpoint_identity(
        receipt_bytes, receipt_sha256, expected_source_origin_sha256, allow_fixture
    )
    anchor = rollback_anchor(native_manifest_bytes, native_manifest_sha256)
    database, repository = Path(database), Path(repository)
    if (
        not database.is_absolute()
        or database.resolve() != database
        or database.is_symlink()
        or not isinstance(expected_file_witness, dict)
        or set(expected_file_witness) != WITNESS_KEYS
    ):
        raise ValueError("Canonical checkpoint and bound file identity required")
    for name in WITNESS_KEYS:
        integer(expected_file_witness[name], "file witness")

    def check():
        if time.monotonic() - started > max_seconds:
            raise TimeoutError("Checkpoint stream deadline exceeded")

    def unchanged():
        check()
        if file_witness(database) != expected_file_witness or any(
            Path(str(database) + suffix).exists() or Path(str(database) + suffix).is_symlink()
            for suffix in ["-wal", "-shm", "-journal"]
        ):
            raise ValueError("Bound standalone checkpoint changed")

    unchanged()
    if (
        expected_file_witness["byte_size"] != checkpoint["database_bytes"]
        or checkpoint["database_bytes"] > max_input_bytes
    ):
        raise ValueError("Checkpoint file size differs")
    source_hash = hashlib.sha256()
    with database.open("rb") as stream:
        if stream.read(20)[18:20] != b"\x01\x01":
            raise ValueError("Verified standalone DELETE-mode checkpoint required")
        stream.seek(0)
        while chunk := stream.read(1024 * 1024):
            check()
            source_hash.update(chunk)
    if source_hash.hexdigest() != checkpoint["database_sha256"]:
        raise ValueError("Materialized checkpoint checksum differs")
    unchanged()
    spec = importlib.util.spec_from_file_location(
        "_mps_checkpoint_stream_sync", repository / "services/tantivy-input/sync.py"
    )
    if spec is None or spec.loader is None:
        raise ValueError("Pinned canonical source adapter unavailable")
    sync = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = sync
    spec.loader.exec_module(sync)
    rules = json.loads((repository / "src/moderation-regexes.json").read_bytes())
    rules_sha = digest(rules.get("canonical_source_sha256"))
    if hashlib.sha256((repository / "src/moderation.ts").read_bytes()).hexdigest() != rules_sha:
        raise ValueError("Canonical expiry/moderation artifact differs")
    floor = max(0, checkpoint["source_clock_before"] - overlap_seconds)
    written = 0

    def write(raw):
        nonlocal written
        check()
        if len(raw) > 16 * 1024**2 or written + len(raw) > max_output_bytes:
            raise ValueError("Checkpoint stream byte bound exceeded")
        pending = memoryview(raw)
        while pending:
            check()
            count = output.write(pending)
            if count is None or count <= 0:
                raise OSError("Checkpoint stream did not advance")
            pending = pending[count:]
        written += len(raw)
        check()

    if bool(pds_fallback_path) != bool(pds_fallback_sha256):
        raise ValueError("Fallback path and digest required together")
    fallback_context = (
        sync.pds_fallback(pds_fallback_path, pds_fallback_sha256)
        if pds_fallback_path
        else nullcontext()
    )
    pds_coverage = {"source_known": 0, "source_missing": 0, "joined": 0, "remaining_missing": 0}
    sort_reserve = 2 * checkpoint["database_bytes"] + 64 * 1024**2
    sort_directory = os.environ.get("SQLITE_TMPDIR", tempfile.gettempdir())
    if shutil.disk_usage(sort_directory).free < sort_reserve:
        raise ValueError("Insufficient disk reserve for sequential checkpoint sorting")
    with (
        fallback_context as fallback,
        sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True) as connection,
    ):
        connection.row_factory = sqlite3.Row
        connection.set_progress_handler(
            lambda: int(time.monotonic() - started > max_seconds), 10000
        )
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA cache_size=-8192")
        connection.execute("PRAGMA temp_store=FILE")
        connection.execute("PRAGMA soft_heap_limit=67108864")
        connection.execute("BEGIN")
        for table in ["actors", "tombstones"]:
            columns = connection.execute(f"PRAGMA table_info({table})").fetchall()
            if [row["name"] for row in columns if row["pk"]] != ["did"]:
                raise ValueError("Canonical DID primary key required")
        raw_actors = connection.execute("SELECT COUNT(*) FROM actors").fetchone()[0]
        raw_tombs = connection.execute("SELECT COUNT(*) FROM tombstones").fetchone()[0]
        orphans = connection.execute(
            "SELECT COUNT(*) FROM tombstones t WHERE NOT EXISTS(SELECT 1 FROM actors a WHERE a.did=t.did)"
        ).fetchone()[0]
        live = connection.execute(
            "SELECT COUNT(*) FROM actors WHERE handle IS NOT NULL AND handle<>''"
        ).fetchone()[0]
        total = raw_actors + orphans
        if not 1 <= total <= max_documents:
            raise ValueError("Checkpoint document bound exceeded")
        counts = {
            "raw_actor_rows": raw_actors,
            "orphan_tombstones": orphans,
            "input_documents": total,
            "live_actor_documents": live,
            "deleted_actor_documents": total - live,
            "native_visible_actors": None,
        }
        overrides = [
            dict(row)
            for row in connection.execute(
                "SELECT CASE WHEN length(CAST(did AS BLOB))<=2048 THEN did ELSE zeroblob(0) END AS did, CASE WHEN length(CAST(action AS BLOB))<=4 THEN action ELSE zeroblob(0) END AS action FROM mod_overrides ORDER BY mod_overrides.did LIMIT 10001"
            )
        ]
        domains = [
            row[0]
            for row in connection.execute(
                "SELECT CASE WHEN length(CAST(domain AS BLOB))<=2048 THEN domain ELSE zeroblob(0) END FROM blocked_domains ORDER BY domain LIMIT 10001"
            )
        ]
        if (
            len(overrides) > 10000
            or len(domains) > 10000
            or any(
                not isinstance(row["did"], str) or row["action"] not in ["show", "hide"]
                for row in overrides
            )
            or any(not isinstance(domain, str) or not domain for domain in domains)
        ):
            raise ValueError("Complete checkpoint policy exceeds schema/bounds")
        policy = {
            "schema_version": 1,
            "complete": True,
            "coverage": "checkpoint-only",
            "overrides": overrides,
            "blocked_domains": domains,
        }
        bootstrap = {
            "schema_version": 1,
            "kind": "sqlite-committed-prefix",
            "fixture": checkpoint["fixture"],
            "coverage": "checkpoint-only",
            "live_coverage_verified": False,
            "policy_freshness_attested": False,
            "checkpoint_receipt_sha256": receipt_sha256,
            "checkpoint_receipt": checkpoint,
            "checkpoint_receipt_json": receipt_bytes.decode("utf8"),
            "replay_floor": floor,
            "replay_overlap_seconds": overlap_seconds,
            "counts": counts,
            "checkpoint_policy_counts": {
                "overrides": len(overrides),
                "blocked_domains": len(domains),
            },
            "checkpoint_policy_sha256": hashlib.sha256(encode(policy)).hexdigest(),
            "raw_tombstone_rows": raw_tombs,
        }
        header = {
            "type": "manifest",
            "schema_version": 1,
            "build_id": build_id,
            "source_watermark": floor,
            "actor_count": total,
            "source_actor_count": total,
            "partial": False,
            "normalize_version": "v2-cjk-bigram",
            "version_kind": "snapshot_watermark",
            "source_format": "sqlite-committed-prefix-v1",
            "source_kind": "sqlite-committed-prefix",
            "source_policy": "sqlite-checkpoint-observations-v1",
            "source_database_sha256": checkpoint["database_sha256"],
            "source_bootstrap": bootstrap,
            "rollback_native_anchor": anchor,
            "legacy_build_id": anchor["legacy_build_id"],
            "canonical_rules_source_sha256": rules_sha,
        }
        if fallback is not None:
            header["pds_fallback"] = fallback.identity()
        unchanged()
        write(encode(header))
        projections = []
        for column in sync.ACTOR_COLUMNS:
            if column in TEXT_FIELDS:
                expression = f"CASE WHEN {column} IS NULL OR length(CAST({column} AS BLOB))<={TEXT_LIMITS.get(column, 65535)} THEN {column} ELSE zeroblob(0) END"
            else:
                expression = f"CASE WHEN typeof({column}) IN ('integer','real','null') THEN {column} ELSE zeroblob(0) END"
            projections.append(expression + " AS " + column)
        actors = iter(
            connection.execute(
                "SELECT "
                + ",".join(projections)
                + " FROM actors AS a NOT INDEXED ORDER BY a.did COLLATE BINARY"
            )
        )
        tombs = iter(
            connection.execute(
                "SELECT CASE WHEN length(CAST(t.did AS BLOB))<=2048 THEN t.did ELSE zeroblob(0) END AS did, CASE WHEN typeof(t.deleted_at)='integer' THEN t.deleted_at ELSE zeroblob(0) END AS deleted_at FROM tombstones t WHERE NOT EXISTS(SELECT 1 FROM actors a WHERE a.did=t.did) ORDER BY t.did COLLATE BINARY"
            )
        )
        actor, tomb = next(actors, None), next(tombs, None)
        emitted = deleted = 0
        previous = ""
        actor_hash = hashlib.sha256()
        while actor is not None or tomb is not None:
            check()
            if any(row is not None and not isinstance(row["did"], str) for row in [actor, tomb]):
                raise ValueError("Invalid bounded source DID")
            if actor is not None and (tomb is None or actor["did"] < tomb["did"]):
                row = dict(actor)
                for column, value in row.items():
                    if value is None:
                        continue
                    if column in TEXT_FIELDS:
                        if not isinstance(value, str):
                            raise ValueError("Invalid bounded source text")
                    elif column == "quality_score":
                        if type(value) not in (int, float) or not math.isfinite(value):
                            raise ValueError("Invalid source score")
                    elif type(value) is not int:
                        raise ValueError("Invalid source integer")
                source_version = integer(row["updated_at"], "source actor version")
                try:
                    record = sync.actor_record(row)
                    if not record["deleted"]:
                        known = bool(row.get("pds"))
                        present = bool(record["pds"])
                        pds_coverage["source_known" if known else "source_missing"] += 1
                        pds_coverage["joined"] += int(not known and present)
                        pds_coverage["remaining_missing"] += int(not present)
                except sync.SafeError as error:
                    raise ValueError(str(error)) from error
                actor = next(actors, None)
            else:
                assert tomb is not None
                source_version = integer(tomb["deleted_at"], "source tombstone version")
                record = sync.deletion(tomb["did"], source_version)
                tomb = next(tombs, None)
            did = record["did"]
            if (
                not isinstance(did, str)
                or not did.startswith("did:")
                or len(did.encode()) > 2048
                or any(ord(char) <= 32 or char == "|" for char in did)
                or did <= previous
            ):
                raise ValueError("Invalid ordered checkpoint identity")
            previous = did
            record.update(
                type="actor",
                version=floor,
                source_updated_at=source_version,
                version_kind="snapshot_watermark",
            )
            raw = encode(record)
            write(raw)
            actor_hash.update(raw)
            emitted += 1
            deleted += int(record["deleted"])
        if emitted != total or deleted != counts["deleted_actor_documents"]:
            raise ValueError("Checkpoint count/projection differs")
        unchanged()
        footer = {
            "type": "end",
            "build_id": build_id,
            "actor_count": emitted,
            "actors_sha256": actor_hash.hexdigest(),
        }
        write(encode(footer))
        output.flush()
        check()
    return {
        "schema_version": 1,
        "kind": "sqlite-checkpoint-stream",
        "fixture": checkpoint["fixture"],
        "source_manifest": header,
        "footer": footer,
        "actors_sha256": actor_hash.hexdigest(),
        "input_documents": emitted,
        "pds_coverage": pds_coverage,
        "scan_strategy": "sequential-file-sort",
        "sort_disk_reserve_bytes": sort_reserve,
        "output_bytes": written,
        "source_bytes_hashed": expected_file_witness["byte_size"],
        "source_unchanged": True,
        "consumer_verified": False,
        "live_coverage_verified": False,
        "policy_freshness_attested": False,
        "wall_seconds": time.monotonic() - started,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--checkpoint-receipt", type=Path, required=True)
    parser.add_argument("--checkpoint-receipt-sha256", required=True)
    parser.add_argument("--source-origin-sha256", required=True)
    parser.add_argument("--file-witness", type=Path, required=True)
    parser.add_argument("--native-manifest", type=Path, required=True)
    parser.add_argument("--native-manifest-sha256", required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--overlap-seconds", type=int, default=300)
    parser.add_argument("--max-seconds", type=int, default=1800)
    parser.add_argument("--max-output-bytes", type=int, default=16 * 1024**3)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.max_seconds <= 5400 or args.receipt.exists() or args.receipt.is_symlink():
        raise ValueError("Bounded new streaming receipt required")

    def expired(*_):
        raise TimeoutError("Checkpoint stream hard process deadline")

    signal.signal(signal.SIGALRM, expired)
    signal.alarm(args.max_seconds)
    try:
        result = stream_checkpoint(
            args.database,
            sys.stdout.buffer,
            args.repository,
            receipt_bytes=args.checkpoint_receipt.read_bytes(),
            receipt_sha256=args.checkpoint_receipt_sha256,
            expected_source_origin_sha256=args.source_origin_sha256,
            expected_file_witness=json.loads(args.file_witness.read_bytes()),
            native_manifest_bytes=args.native_manifest.read_bytes(),
            native_manifest_sha256=args.native_manifest_sha256,
            build_id=args.build_id,
            overlap_seconds=args.overlap_seconds,
            max_seconds=args.max_seconds,
            max_output_bytes=args.max_output_bytes,
        )
        from mps.typeahead_tantivy import durable_receipt

        durable_receipt(args.receipt, result)
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            json.dumps({"error": "checkpoint_stream_failed", "error_class": type(error).__name__}),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
