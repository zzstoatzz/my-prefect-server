import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest
from mps.typeahead_sqlite_seed import checkpoint_fixture

SCHEMA = """CREATE TABLE actors(did TEXT PRIMARY KEY,handle TEXT,display_name TEXT,avatar_url TEXT,hidden INTEGER,labels TEXT,created_at TEXT,associated TEXT,pds TEXT,followers_count INTEGER,follows_count INTEGER,posts_count INTEGER,last_activity_at INTEGER,quality_score REAL,updated_at INTEGER);
CREATE TABLE tombstones(did TEXT PRIMARY KEY,deleted_at INTEGER);
CREATE TABLE mod_overrides(did TEXT PRIMARY KEY,action TEXT);
CREATE TABLE blocked_domains(domain TEXT PRIMARY KEY);"""


def source(tmp_path, count=32):
    path = tmp_path / "source.db"
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.executemany(
        "INSERT INTO actors VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            (
                f"did:fixture:{n:06}",
                f"person{n:06}.test",
                f"Person {n:06}",
                "",
                int(n == 3),
                "[]",
                "2026-10-28T00:00:00Z",
                "{}",
                "https://local.test",
                3,
                4,
                5,
                6,
                float(n),
                90,
            )
            for n in range(count)
        ),
    )
    db.execute("INSERT INTO tombstones VALUES(?,?)", ("did:fixture:000000", 101))
    db.execute("INSERT INTO tombstones VALUES(?,?)", ("did:fixture:orphan", 100))
    db.execute("INSERT INTO mod_overrides VALUES(?,?)", ("did:fixture:000003", "show"))
    db.execute("INSERT INTO blocked_domains VALUES(?)", ("blocked.test",))
    db.commit()
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA wal_autocheckpoint=0")
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.execute(
        "UPDATE actors SET followers_count=12345,quality_score=16777217,updated_at=99 WHERE did=?",
        ("did:fixture:000000",),
    )
    db.execute("UPDATE actors SET handle=? WHERE did=?", ("", "did:fixture:000004"))
    db.commit()
    return path, db


@pytest.fixture
def repository():
    configured = os.environ.get("TA_REPOSITORY")
    if not configured:
        pytest.skip("requires canonical Typeahead source and prebuilt backend")
    return Path(configured)


def test_real_sqlite_wal_full_fields_and_rust_import(tmp_path, repository):
    path, db = source(tmp_path)
    output = tmp_path / "seed.jsonl"
    try:
        main = tmp_path / "main-only.db"
        shutil.copyfile(path, main)
        with sqlite3.connect(main) as base:
            assert (
                base.execute(
                    "SELECT followers_count FROM actors WHERE did=?", ("did:fixture:000000",)
                ).fetchone()[0]
                == 3
            )
        receipt = checkpoint_fixture(
            path,
            output,
            repository,
            build_id="b1793236000-sqlite-fixture",
            generation=7,
            durable_frame_num=17,
        )
        records = [json.loads(line) for line in output.read_bytes().splitlines()]
        actors = {row["did"]: row for row in records[1:-1]}
        assert len(actors) == 33
        assert actors["did:fixture:000000"]["followers_count"] == 12345
        assert actors["did:fixture:000000"]["quality_score"] == 16777216
        assert actors["did:fixture:000000"]["deleted"] is False
        assert actors["did:fixture:000000"]["source_updated_at"] == 99
        assert actors["did:fixture:000004"]["deleted"] is True
        assert actors["did:fixture:orphan"]["deleted"] is True
        assert actors["did:fixture:000003"]["hidden"] is True
        assert (
            receipt["wal_sha256"]
            == hashlib.sha256(Path(str(path) + "-wal").read_bytes()).hexdigest()
        )
        assert (
            receipt["coverage"] == "checkpoint-only" and receipt["live_coverage_verified"] is False
        )
        assert (
            receipt["completed_live_round"] is False
            and receipt["policy_freshness_attested"] is False
        )
        policy = json.loads(output.with_suffix(".policy.json").read_bytes())
        assert "source_observed_at" not in policy
        assert policy["overrides"] == [{"did": "did:fixture:000003", "action": "show"}]
        assert records[0]["partial"] is True
        binary = Path(
            os.environ.get(
                "TA_BINARY", repository / "services/tantivy/target/release/typeahead-tantivy"
            )
        )
        if not binary.is_file():
            pytest.skip("prebuilt Rust backend unavailable")
        data = tmp_path / "rust"
        subprocess.run(
            [
                str(binary),
                "import",
                "--data",
                str(data),
                "--input",
                str(output),
                "--allow-partial",
                "true",
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        spec = importlib.util.spec_from_file_location(
            "sqlite_seed_smoke", repository / "services/tantivy/tests/smoke.py"
        )
        smoke = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(smoke)
        smoke.BIN = binary
        with (tmp_path / "rust.log").open("wb") as log:
            child, public, admin = smoke.start(data, log, "fixture-secret")
            try:
                status = smoke.request(public, "/index/status")[2]
                assert status["cutover_source_ready"] is False
                assert status["full_source"] is False
                matches = smoke.request(public, "/search?q=person000000")[2]["actors"]
                assert matches[0]["did"] == "did:fixture:000000"
                assert smoke.request(public, "/search?q=person000003")[2]["actors"] == []
                stored = smoke.request(
                    admin,
                    "/admin/source-state",
                    {"dids": ["did:fixture:000000", "did:fixture:orphan"]},
                    "fixture-secret",
                )
                assert stored[0] == 200 and all(
                    row["document_sha256"] for row in stored[2]["actors"]
                )
                round_receipt = checkpoint_round_fixture(
                    repository, path, output, data, public, admin, smoke
                )
                assert round_receipt["applied"] == 0
                assert round_receipt["replayed"] == 33
                assert round_receipt["source_policy"] == "sqlite-checkpoint-observations-v1"
                assert round_receipt["live_coverage_verified"] is False
                print("SQLITE_CHECKPOINT_ROUND " + json.dumps(round_receipt, sort_keys=True))
            finally:
                smoke.stop(child)
            child, public, _ = smoke.start(data, log, "fixture-secret")
            try:
                restarted = smoke.request(public, "/index/status")[2]
                assert (
                    restarted["source_cursor"]["source_policy"]
                    == "sqlite-checkpoint-observations-v1"
                )
                assert (
                    restarted["source_cursor"]["reconciliation"]["coverage"]["kind"]
                    == "checkpoint-only"
                )
                assert restarted["cutover_source_ready"] is False
            finally:
                smoke.stop(child)
    finally:
        db.close()


def test_limits_reject_before_emitting_output(tmp_path, repository):
    path, db = source(tmp_path)
    try:
        for kwargs in ({"max_actors": 2}, {"max_bytes": 1}):
            output = tmp_path / ("actors.jsonl" if "max_actors" in kwargs else "bytes.jsonl")
            with pytest.raises(ValueError, match="budget"):
                checkpoint_fixture(
                    path,
                    output,
                    repository,
                    build_id="fixture",
                    generation=7,
                    durable_frame_num=17,
                    **kwargs,
                )
            assert not output.exists()
            assert not output.with_suffix(".jsonl.partial").exists()
    finally:
        db.close()


@pytest.mark.skipif(
    os.environ.get("TA_SQLITE_SEED_SCALE") != "1", reason="explicit bounded scale pilot"
)
def test_100k_sqlite_seed_and_actual_rust_import(tmp_path, repository):
    path, db = source(tmp_path, 100000)
    try:
        output = tmp_path / "seed.jsonl"
        with pytest.raises(ValueError, match="actor budget"):
            checkpoint_fixture(
                path,
                output,
                repository,
                build_id="b1793236000-scale",
                generation=7,
                durable_frame_num=17,
            )
        # The input ceiling includes orphan tombstones, so use99,999 source actors.
        db.execute("DELETE FROM actors WHERE did=?", ("did:fixture:099999",))
        db.commit()
        receipt = checkpoint_fixture(
            path,
            output,
            repository,
            build_id="b1793236000-scale",
            generation=7,
            durable_frame_num=17,
        )
        binary = Path(
            os.environ.get(
                "TA_BINARY", repository / "services/tantivy/target/release/typeahead-tantivy"
            )
        )
        import_started = time.monotonic()
        result = subprocess.run(
            [
                str(binary),
                "import",
                "--data",
                str(tmp_path / "rust"),
                "--input",
                str(output),
                "--allow-partial",
                "true",
            ],
            capture_output=True,
            check=False,
            timeout=180,
        )
        assert result.returncode == 0, result.stderr[-1000:]
        assert receipt["input_documents"] == 100000
        receipt["import_wall_seconds"] = time.monotonic() - import_started
        receipt["binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
        receipt["committed_index_bytes"] = sum(
            p.stat().st_size for p in (tmp_path / "rust").rglob("*") if p.is_file()
        )
        print("SQLITE_SEED_PILOT " + json.dumps(receipt, sort_keys=True))
    finally:
        db.close()


def checkpoint_round_fixture(repository, database, seed, data, public, admin_url, smoke):
    modules = []
    for name in ("sync", "current_state"):
        spec = importlib.util.spec_from_file_location(
            "checkpoint_round_" + name, repository / "services/tantivy-input" / (name + ".py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    sync, current = modules
    current.SOURCE_POLICY = "sqlite-checkpoint-observations-v1"

    class LocalSource:
        def __init__(self):
            self.db = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
            self.db.row_factory = sqlite3.Row
            self.db.execute("BEGIN")
            self.clock = 111
            self.db.create_function("unixepoch", 0, lambda: self.clock)
            self.requests = 0
            self.bytes = 0

        def select(self, sql, args):
            assert sql in sync.SELECTS
            self.requests += 1
            return [dict(row) for row in self.db.execute(sql, args)]

        def now(self):
            return self.select(sync.CLOCK_SQL, [])[0]["source_now"]

    local = LocalSource()
    try:
        admin = sync.Admin(admin_url, "fixture-secret", public + "/index/status")
        dids = [
            row["did"]
            for row in local.db.execute(
                "SELECT did FROM actors UNION SELECT did FROM tombstones ORDER BY did"
            )
        ]
        before = smoke.request(admin_url, "/admin/source-state", {"dids": dids}, "fixture-secret")[
            2
        ]["actors"]
        status_before = smoke.request(public, "/index/status")[2]
        segments_before = json.loads((data / "index/meta.json").read_bytes())["segments"]
        started = time.monotonic()
        runner = current.ObservedSynchronizer(
            local,
            admin,
            sync,
            anti_entropy_pages=4,
            policy_mode="off",
            sweep_cooldown_seconds=0,
            page_size=500,
            max_pages=10,
            max_seconds=30,
            safety_lag=0,
            retries=1,
            backoff=0,
        )
        result = runner.run()
        assert result["reconciliation"]["last_completed_at"] == 111
        assert result["reconciliation"]["last_completed_started_at"] == 111
        status = admin.status()
        cursor = json.loads(json.dumps(status["source_cursor"]))
        checkpoint_hash = hashlib.sha256(seed.with_suffix(".receipt.json").read_bytes()).hexdigest()
        cursor["reconciliation"]["coverage"] = {
            "kind": "checkpoint-only",
            "bootstrap_receipt_sha256": checkpoint_hash,
            "live_coverage_verified": False,
        }
        writer = current.ObservationAdmin(admin, sync)
        state, revision = writer.state([])
        writer.reconcile([], [], revision, state["source_cursor"], cursor)
        status_after = smoke.request(public, "/index/status")[2]
        after = smoke.request(admin_url, "/admin/source-state", {"dids": dids}, "fixture-secret")[
            2
        ]["actors"]
        assert after == before
        assert status_after["indexed_memberships"] == status_before["indexed_memberships"]
        assert json.loads((data / "index/meta.json").read_bytes())["segments"] == segments_before
        assert status_after["cutover_source_ready"] is False
        with pytest.raises(sync.SafeError, match="policy differs"):
            sync.initial_cursor(status_after)
        assert (
            status_after["source_cursor"]["reconciliation"]["coverage"]["bootstrap_receipt_sha256"]
            == checkpoint_hash
        )
        return {
            "kind": "checkpoint-only",
            "source_policy": current.SOURCE_POLICY,
            "live_coverage_verified": False,
            "source_network_requests": 0,
            "local_sql_calls": local.requests,
            "applied": result["applied"],
            "replayed": result["replayed"],
            "documents_checked": len(dids),
            "document_hashes_unchanged": True,
            "segments_unchanged": True,
            "indexed_memberships_unchanged": True,
            "wall_seconds": time.monotonic() - started,
        }
    finally:
        local.db.close()
