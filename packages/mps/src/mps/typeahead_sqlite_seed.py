"""Bounded fixture-only import from a complete SQLite checkpoint and its WAL."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import sqlite3
import sys
import time
from pathlib import Path

from mps.typeahead_tantivy import durable_receipt


def encode(value):
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        + b"\n"
    )


def checkpoint_fixture(
    database: Path,
    output: Path,
    repository: Path,
    *,
    build_id: str,
    generation: int,
    durable_frame_num: int,
    max_actors=100000,
    max_bytes=128 * 1024**2,
    max_seconds=180,
):
    started = time.monotonic()
    if (
        not 1 <= max_actors <= 100000
        or not 1 <= max_bytes <= 128 * 1024**2
        or not 0 < max_seconds <= 180
    ):
        raise ValueError("Invalid bounded fixture budget")
    if (
        type(generation) is not int
        or generation < 0
        or type(durable_frame_num) is not int
        or durable_frame_num < 0
    ):
        raise ValueError("Explicit fixture checkpoint coordinates are required")
    database = Path(database)
    output = Path(output)
    if (
        database.is_symlink()
        or database.resolve() != database.absolute()
        or output.exists()
        or output.is_symlink()
    ):
        raise ValueError("Canonical source and new output paths are required")
    paths = [database] + (
        [Path(str(database) + "-wal")] if Path(str(database) + "-wal").exists() else []
    )
    hashes = {}
    witnesses = {}
    size = 0

    def check():
        if time.monotonic() - started > max_seconds:
            raise TimeoutError("SQLite fixture deadline exceeded")

    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError("Invalid checkpoint file")
        stat = path.stat()
        witnesses[path] = (
            stat.st_dev,
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_ctime_ns,
        )
        size += stat.st_size
        if size > max_bytes:
            raise ValueError("Checkpoint exceeds fixture byte budget")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                check()
                digest.update(chunk)
        hashes[path] = digest.hexdigest()
    spec = importlib.util.spec_from_file_location(
        "_mps_sqlite_seed_sync", repository / "services/tantivy-input/sync.py"
    )
    if spec is None or spec.loader is None:
        raise ValueError("Pinned canonical source adapter is unavailable")
    sync = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = sync
    spec.loader.exec_module(sync)
    part = output.with_suffix(output.suffix + ".partial")
    if part.exists() or part.is_symlink():
        raise ValueError("Fixture output is already in use")
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.set_progress_handler(lambda: int(time.monotonic() - started > max_seconds), 10000)
    owned = False
    try:
        connection.execute("BEGIN")
        actor_count = connection.execute("SELECT COUNT(*) FROM actors").fetchone()[0]
        orphan_count = connection.execute(
            "SELECT COUNT(*) FROM tombstones t WHERE NOT EXISTS(SELECT 1 FROM actors a WHERE a.did=t.did)"
        ).fetchone()[0]
        total = actor_count + orphan_count
        if not 0 < total <= max_actors:
            raise ValueError("Checkpoint exceeds fixture actor budget")
        floor = connection.execute(
            "SELECT MAX(value) FROM (SELECT COALESCE(MAX(updated_at),0) AS value FROM actors UNION ALL SELECT COALESCE(MAX(deleted_at),0) FROM tombstones)"
        ).fetchone()[0]
        if type(floor) is not int or floor < 0:
            raise ValueError("Invalid checkpoint logical watermark")
        overrides = [
            dict(row)
            for row in connection.execute(
                "SELECT did,action FROM mod_overrides ORDER BY did LIMIT 10001"
            )
        ]
        domains = [
            row[0]
            for row in connection.execute(
                "SELECT domain FROM blocked_domains ORDER BY domain LIMIT 10001"
            )
        ]
        if (
            len(overrides) > 10000
            or len(domains) > 10000
            or any(
                row["action"] not in ("show", "hide") or not isinstance(row["did"], str)
                for row in overrides
            )
            or any(not isinstance(domain, str) or not domain for domain in domains)
        ):
            raise ValueError("Checkpoint moderation policy is invalid or incomplete")
        policy = {
            "schema_version": 1,
            "complete": True,
            "coverage": "checkpoint-only",
            "overrides": overrides,
            "blocked_domains": domains,
        }
        policy_raw = encode(policy)
        bootstrap = {
            "schema_version": 1,
            "kind": "sqlite-checkpoint",
            "fixture": True,
            "generation": generation,
            "durable_frame_num": durable_frame_num,
            "db_sha256": hashes[database],
            "wal_sha256": hashes.get(Path(str(database) + "-wal")),
            "coverage": "checkpoint-only",
            "live_coverage_verified": False,
            "raw_actor_rows": actor_count,
            "orphan_tombstones": orphan_count,
            "input_documents": total,
            "policy_sha256": hashlib.sha256(policy_raw).hexdigest(),
        }
        header = {
            "type": "manifest",
            "schema_version": 1,
            "build_id": build_id,
            "source_watermark": floor,
            "actor_count": total,
            "source_actor_count": total,
            "partial": True,
            "normalize_version": "v2-cjk-bigram",
            "source_format": "sqlite-checkpoint-fixture-v1",
            "source_database_sha256": hashes[database],
            "source_bootstrap": bootstrap,
        }
        actors = iter(
            connection.execute(
                "SELECT " + ",".join(sync.ACTOR_COLUMNS) + " FROM actors ORDER BY did"
            )
        )
        tombs = iter(
            connection.execute(
                "SELECT t.did,t.deleted_at FROM tombstones t WHERE NOT EXISTS(SELECT 1 FROM actors a WHERE a.did=t.did) ORDER BY t.did"
            )
        )
        actor = next(actors, None)
        tomb = next(tombs, None)
        emitted = 0
        written = 0
        previous = ""
        actor_hash = hashlib.sha256()
        with part.open("xb") as stream:
            owned = True
            raw = encode(header)
            stream.write(raw)
            written += len(raw)
            while actor is not None or tomb is not None:
                check()
                if actor is not None and (tomb is None or actor["did"] < tomb["did"]):
                    row = dict(actor)
                    for key in sync.ACTOR_COLUMNS:
                        value = row[key]
                        if key == "quality_score":
                            if value is not None and (
                                type(value) not in (int, float) or not math.isfinite(value)
                            ):
                                raise ValueError("Invalid source score")
                        elif key in (
                            "hidden",
                            "followers_count",
                            "follows_count",
                            "posts_count",
                            "last_activity_at",
                            "updated_at",
                        ):
                            if value is not None and type(value) is not int:
                                raise ValueError("Invalid source integer")
                        elif value is not None and not isinstance(value, str):
                            raise ValueError("Invalid source text")
                    source_version = row["updated_at"]
                    record = sync.actor_record(row)
                    actor = next(actors, None)
                else:
                    assert tomb is not None
                    source_version = tomb["deleted_at"]
                    record = sync.deletion(tomb["did"], source_version)
                    tomb = next(tombs, None)
                if (
                    type(source_version) is not int
                    or source_version < 0
                    or not isinstance(record["did"], str)
                    or not record["did"].startswith("did:")
                    or record["did"] <= previous
                ):
                    raise ValueError("Invalid ordered checkpoint identity/version")
                previous = record["did"]
                record.update(
                    type="actor",
                    version=floor,
                    source_updated_at=source_version,
                    version_kind="snapshot_watermark",
                )
                raw = encode(record)
                written += len(raw)
                if written > max_bytes:
                    raise ValueError("Fixture stream exceeds byte budget")
                stream.write(raw)
                actor_hash.update(raw)
                emitted += 1
            if emitted != total:
                raise ValueError("Checkpoint actor count differs")
            raw = encode(
                {
                    "type": "end",
                    "build_id": build_id,
                    "actor_count": emitted,
                    "actors_sha256": actor_hash.hexdigest(),
                }
            )
            written += len(raw)
            if written > max_bytes:
                raise ValueError("Fixture stream exceeds byte budget")
            stream.write(raw)
            stream.flush()
            import os

            os.fsync(stream.fileno())
        for path, witness in witnesses.items():
            stat = path.stat()
            if (
                stat.st_dev,
                stat.st_ino,
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_ctime_ns,
            ) != witness:
                raise ValueError("Checkpoint changed while importing")
        part.replace(output)
        with output.open("rb") as stream:
            output_digest = hashlib.file_digest(stream, "sha256").hexdigest()
        receipt = {
            **bootstrap,
            "actors_sha256": actor_hash.hexdigest(),
            "output_sha256": output_digest,
            "output_bytes": written,
            "checkpoint_bytes": size,
            "wall_seconds": time.monotonic() - started,
            "completed_live_round": False,
            "policy_freshness_attested": False,
        }
        durable_receipt(output.with_suffix(".policy.json"), policy)
        durable_receipt(output.with_suffix(".receipt.json"), receipt)
        return receipt
    finally:
        connection.close()
        if owned:
            part.unlink(missing_ok=True)
