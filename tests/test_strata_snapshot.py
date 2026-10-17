import sqlite3

import pytest

from flows.strata_snapshot import build, connect, encoded, ingest, publish, quarantine_bad_counts


def record(checksum="a", counts=None):
    return {
        "idx": 5,
        "name": "seg",
        "sizeBytes": 10,
        "checksum": checksum,
        "eventCount": 8,
        "blockCount": 1,
        "minSeq": 1,
        "maxSeq": 8,
        "minWitnessedAt": 100,
        "maxWitnessedAt": 200,
        "collections": [
            {"nsid": k, "count": v}
            for k, v in (counts or {"fm.teal.track": 3, "app.bsky.feed.post": 5}).items()
        ],
    }


def test_compaction_reconciles_counts_and_removals_atomically(tmp_path):
    db = connect(tmp_path / "strata.sqlite")
    ingest(db, [record()], 1)
    ingest(db, [record("b", {"fm.teal.track": 2, "org.new.record": 4})], 1)
    assert [
        tuple(r) for r in db.execute("SELECT nsid,count FROM segment_collections ORDER BY nsid")
    ] == [("fm.teal.track", 2), ("org.new.record", 4)]
    assert db.execute("SELECT checksum FROM segments").fetchone()[0] == "b"
    broken = record("c")
    broken["sizeBytes"] = None
    with pytest.raises(sqlite3.IntegrityError):
        ingest(db, [record("d"), broken], 1)
    assert db.execute("SELECT checksum FROM segments").fetchone()[0] == "b"


def test_repeated_checksum_change_does_not_rewrite_unchanged_counts(tmp_path):
    db = connect(tmp_path / "strata.sqlite")
    ingest(db, [record()], 1)
    db.executescript(
        "CREATE TABLE changes(n); CREATE TRIGGER writes AFTER UPDATE ON segment_collections BEGIN INSERT INTO changes VALUES(1); END;"
    )
    before, _objects = build(db)
    ingest(db, [record("b")], 1)
    after, _new_objects = build(db)
    assert db.execute("SELECT COUNT(*) FROM changes").fetchone()[0] == 0
    a = {r["name"]: r["digest"] for r in before["objects"]}
    b = {r["name"]: r["digest"] for r in after["objects"]}
    assert a["heat/namespaces"] == b["heat/namespaces"]
    assert a["collections"] == b["collections"]
    assert a["checksums"] != b["checksums"]


def test_failed_publication_keeps_previous_manifest_and_retries(tmp_path):
    db = connect(tmp_path / "strata.sqlite")
    ingest(db, [record()], 1)
    manifest, objects = build(db)
    writes = []

    def reject(method, path, body):
        writes.append(path)
        raise OSError("interrupted upload")

    with pytest.raises(OSError):
        publish(db, manifest, objects, reject)
    assert "manifest" not in writes
    assert db.execute("SELECT COUNT(*) FROM published_objects").fetchone()[0] == 0
    successful = []
    publish(db, manifest, objects, lambda method, path, body: successful.append((path, body)))
    assert successful[-1] == ("manifest", encoded(manifest))
    successful.clear()
    assert (
        publish(db, manifest, objects, lambda method, path, body: successful.append((path, body)))
        == 0
    )
    assert successful == []


def test_ingest_names_columns_so_legacy_column_order_keeps_counts(tmp_path):
    path = tmp_path / "strata.sqlite"
    legacy = sqlite3.connect(path)
    legacy.executescript(
        """CREATE TABLE segment_collections (
          idx INTEGER NOT NULL, nsid TEXT NOT NULL, count INTEGER NOT NULL, ns TEXT,
          PRIMARY KEY (idx, nsid));"""
    )
    legacy.close()
    db = connect(path)
    ingest(db, [record()], 1)
    rows = [
        tuple(r)
        for r in db.execute(
            "SELECT nsid,ns,count,typeof(count) FROM segment_collections ORDER BY nsid"
        )
    ]
    assert rows == [
        ("app.bsky.feed.post", "app.bsky", 5, "integer"),
        ("fm.teal.track", "fm.teal", 3, "integer"),
    ]
    ingest(db, [record("b", {"fm.teal.track": 9, "app.bsky.feed.post": 5})], 1)
    assert (
        db.execute("SELECT count FROM segment_collections WHERE nsid='fm.teal.track'").fetchone()[0]
        == 9
    )


def test_quarantine_forgets_segments_with_text_counts(tmp_path):
    db = connect(tmp_path / "strata.sqlite")
    ingest(db, [record()], 1)
    other = record()
    other["idx"] = 6
    ingest(db, [other], 2)
    db.execute(
        "UPDATE segment_collections SET count='app.bsky' WHERE idx=5 AND nsid='app.bsky.feed.post'"
    )
    db.commit()
    assert quarantine_bad_counts(db) == [5]
    assert [r[0] for r in db.execute("SELECT idx FROM segments")] == [6]
    assert [r[0] for r in db.execute("SELECT DISTINCT idx FROM segment_collections")] == [6]
    assert quarantine_bad_counts(db) == []
    manifest, _objects = build(db)
    assert manifest["version"] == 1
