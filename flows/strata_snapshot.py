"""HeavyPad's durable Strata summaries and content-addressed serving snapshots."""

import hashlib
import json
import sqlite3
import time
from collections import defaultdict
from pathlib import Path


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS segments (
          idx INTEGER PRIMARY KEY, name TEXT NOT NULL, size_bytes INTEGER NOT NULL,
          checksum TEXT NOT NULL, event_count INTEGER NOT NULL, block_count INTEGER NOT NULL,
          min_seq INTEGER NOT NULL, max_seq INTEGER NOT NULL, min_witnessed_at INTEGER NOT NULL,
          max_witnessed_at INTEGER NOT NULL, ingested_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS segment_collections (
          idx INTEGER NOT NULL, nsid TEXT NOT NULL, ns TEXT NOT NULL, count INTEGER NOT NULL,
          PRIMARY KEY (idx, nsid));
        CREATE INDEX IF NOT EXISTS segment_collections_ns ON segment_collections(ns, idx);
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL, updated_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS published_objects (digest TEXT PRIMARY KEY, last_used INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS publication (id INTEGER PRIMARY KEY CHECK(id=1), digest TEXT NOT NULL);
    """)
    return db


def ingest(db: sqlite3.Connection, records: list[dict], archive_segments: int) -> None:
    now = int(time.time() * 1000)
    with db:
        for s in records:
            db.execute(
                """INSERT INTO segments VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(idx) DO UPDATE SET name=excluded.name,size_bytes=excluded.size_bytes,
                checksum=excluded.checksum,event_count=excluded.event_count,block_count=excluded.block_count,
                min_seq=excluded.min_seq,max_seq=excluded.max_seq,min_witnessed_at=excluded.min_witnessed_at,
                max_witnessed_at=excluded.max_witnessed_at,ingested_at=excluded.ingested_at""",
                (
                    s["idx"],
                    s["name"],
                    s["sizeBytes"],
                    s["checksum"],
                    s["eventCount"],
                    s["blockCount"],
                    s["minSeq"],
                    s["maxSeq"],
                    s["minWitnessedAt"],
                    s["maxWitnessedAt"],
                    now,
                ),
            )
            previous = {
                r["nsid"]: r["count"]
                for r in db.execute(
                    "SELECT nsid,count FROM segment_collections WHERE idx=?", (s["idx"],)
                )
            }
            current = {c["nsid"]: c["count"] for c in s["collections"]}
            for nsid in previous.keys() - current.keys():
                db.execute(
                    "DELETE FROM segment_collections WHERE idx=? AND nsid=?", (s["idx"], nsid)
                )
            for nsid, count in current.items():
                if previous.get(nsid) != count:
                    db.execute(
                        """INSERT INTO segment_collections VALUES (?,?,?,?)
                        ON CONFLICT(idx,nsid) DO UPDATE SET count=excluded.count""",
                        (s["idx"], nsid, ".".join(nsid.split(".")[:2]), count),
                    )
        db.execute(
            """INSERT INTO meta VALUES ('archive_segments',?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
            (archive_segments, now),
        )


def encoded(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()


def build(db: sqlite3.Connection) -> tuple[dict, dict[str, bytes]]:
    objects = {}
    refs = {}

    def put(name, value):
        body = encoded(value)
        digest = hashlib.sha256(body).hexdigest()
        refs[name] = digest
        objects[digest] = body

    segments = [
        list(r)
        for r in db.execute("""SELECT idx,min_seq,max_seq,min_witnessed_at,
        max_witnessed_at,event_count FROM segments ORDER BY idx""")
    ]
    p = dict(
        db.execute("""SELECT COUNT(*) AS segments,MAX(idx) AS maxIdx,MAX(max_seq) AS maxSeq,
        SUM(event_count) AS events,MAX(ingested_at) AS updatedAt FROM segments""").fetchone()
    )
    p.update(
        dict(
            db.execute("""SELECT COUNT(DISTINCT nsid) AS collections,
        COUNT(DISTINCT ns) AS namespaces FROM segment_collections""").fetchone()
        )
    )
    archive = db.execute("SELECT value FROM meta WHERE key='archive_segments'").fetchone()
    p["archiveSegments"] = archive[0] if archive else None
    put("progress", p)
    put("segments", segments)
    put(
        "checksums",
        {
            "segments": [
                dict(r) for r in db.execute("SELECT idx,checksum FROM segments ORDER BY idx")
            ]
        },
    )
    collections = [
        dict(r)
        for r in db.execute("""SELECT nsid,ns,SUM(count) AS count,COUNT(*) AS segments
        FROM segment_collections GROUP BY nsid ORDER BY count DESC,nsid""")
    ]
    namespaces = [
        dict(r)
        for r in db.execute("""SELECT ns,SUM(count) AS count,COUNT(DISTINCT nsid) AS members
        FROM segment_collections GROUP BY ns ORDER BY count DESC,ns""")
    ]
    put("collections", {"collections": collections})
    put("namespaces", {"namespaces": namespaces})
    for group, keys, column in (
        (
            "namespaces",
            [
                {"key": r["ns"], "count": r["count"], "members": r["members"]}
                for r in namespaces[:60]
            ],
            "ns",
        ),
        (
            "collections",
            [{"key": r["nsid"], "count": r["count"], "members": 1} for r in collections[:60]],
            "nsid",
        ),
    ):
        ranks = {r["key"]: i for i, r in enumerate(keys)}
        cells = defaultdict(int)
        for idx, key, count in db.execute(f"SELECT idx,{column},count FROM segment_collections"):
            cells[(idx, ranks.get(key, len(keys)))] += count
        put(
            "heat/" + group,
            {"keys": keys, "cells": [[idx, k, n] for (idx, k), n in sorted(cells.items())]},
        )
    for ns in namespaces:
        keys = [
            {"key": r["nsid"], "count": r["count"], "members": 1}
            for r in collections
            if r["ns"] == ns["ns"]
        ]
        ranks = {r["key"]: i for i, r in enumerate(keys)}
        cells = [
            [r["idx"], ranks[r["nsid"]], r["count"]]
            for r in db.execute(
                "SELECT idx,nsid,count FROM segment_collections WHERE ns=? ORDER BY idx,nsid",
                (ns["ns"],),
            )
        ]
        put("ns/" + ns["ns"], {"keys": keys, "cells": cells})
    return {
        "version": 1,
        "updatedAt": p["updatedAt"],
        "objects": [{"name": name, "digest": digest} for name, digest in refs.items()],
    }, objects


def needs_publication(db: sqlite3.Connection, manifest: dict) -> bool:
    previous = db.execute("SELECT digest FROM publication WHERE id=1").fetchone()
    return previous is None or previous[0] != hashlib.sha256(encoded(manifest)).hexdigest()


def publish(db: sqlite3.Connection, manifest: dict, objects: dict[str, bytes], send) -> int:
    if not needs_publication(db, manifest):
        return 0
    known = {r[0] for r in db.execute("SELECT digest FROM published_objects")}
    now = int(time.time())
    uploads = 0
    pending = [
        {"digest": digest, "body": body.decode()}
        for digest, body in objects.items()
        if digest not in known
    ]
    for start in range(0, len(pending), 32):
        batch = pending[start : start + 32]
        send("PUT", "batch", encoded(batch))
        uploads += len(batch)
    send("PUT", "manifest", encoded(manifest))
    with db:
        db.execute(
            "INSERT INTO publication VALUES(1,?) ON CONFLICT(id) DO UPDATE SET digest=excluded.digest",
            (hashlib.sha256(encoded(manifest)).hexdigest(),),
        )
        db.executemany(
            """INSERT INTO published_objects VALUES (?,?)
            ON CONFLICT(digest) DO UPDATE SET last_used=excluded.last_used""",
            [(digest, now) for digest in objects],
        )
    old = [
        r[0]
        for r in db.execute(
            "SELECT digest FROM published_objects WHERE last_used < ?", (now - 7 * 86400,)
        )
    ]
    for digest in old:
        send("DELETE", "objects/" + digest, b"")
        with db:
            db.execute("DELETE FROM published_objects WHERE digest=?", (digest,))
    return uploads
