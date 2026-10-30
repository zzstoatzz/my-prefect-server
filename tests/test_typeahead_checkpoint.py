import hashlib
import json
import sqlite3
import struct
import threading
import time
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from mps.typeahead_checkpoint import FRAME, CheckpointError, fetch_checkpoint


@pytest.fixture
def provider(tmp_path):
    path = tmp_path / "upstream.db"
    database = sqlite3.connect(path)
    database.execute("CREATE TABLE sample(key TEXT PRIMARY KEY, value INTEGER)")
    database.execute("INSERT INTO sample VALUES('old', 1)")
    database.commit()
    database.execute("PRAGMA journal_mode=WAL")
    database.execute("PRAGMA wal_autocheckpoint=0")
    database.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    base = path.read_bytes()
    database.execute("UPDATE sample SET value=42 WHERE key='old'")
    database.execute("INSERT INTO sample VALUES('new',99)")
    database.commit()
    frames = Path(str(path) + "-wal").read_bytes()[32:]
    assert len(frames) % FRAME == 0
    state = {
        "frames": frames,
        "base": base,
        "sync_error": None,
        "after_generation": 7,
        "info_calls": 0,
        "paths": [],
        "generation": 7,
        "redirect_export": False,
        "export_delay": 0,
        "clocks": [1793236000, 1793236001],
        "slow_body_prefix": None,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, body, status=200):
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            with suppress(BrokenPipeError, ConnectionResetError):
                if state["slow_body_prefix"] and self.path.startswith(state["slow_body_prefix"]):
                    until = time.monotonic() + 1.8
                    offset = 0
                    while offset < len(body) and time.monotonic() < until:
                        self.wfile.write(body[offset : offset + 1])
                        self.wfile.flush()
                        offset += 1
                        time.sleep(0.03)
                    self.wfile.write(body[offset:])
                else:
                    self.wfile.write(body)

        def do_POST(self):
            state["paths"].append(self.path)
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert body["requests"][0]["stmt"]["sql"] == "SELECT unixepoch() AS source_now"
            self.respond(
                json.dumps(
                    {
                        "results": [
                            {
                                "type": "ok",
                                "response": {
                                    "type": "execute",
                                    "result": {
                                        "rows": [
                                            [
                                                {
                                                    "type": "integer",
                                                    "value": str(state["clocks"].pop(0)),
                                                }
                                            ]
                                        ]
                                    },
                                },
                            }
                        ]
                    }
                ).encode()
            )

        def do_GET(self):
            state["paths"].append(self.path)
            if self.path == "/info":
                state["info_calls"] += 1
                generation = (
                    state["generation"] if state["info_calls"] == 1 else state["after_generation"]
                )
                self.respond(
                    json.dumps({"current_generation": generation, "db_type": "sqlite"}).encode()
                )
            elif self.path == "/export/7":
                if state["redirect_export"]:
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1:1/forbidden")
                    self.end_headers()
                    return
                time.sleep(state["export_delay"])
                self.respond(state["base"])
            elif self.path.startswith("/sync/7/"):
                if state["sync_error"]:
                    self.respond(b'{"error":"not a successful boundary"}', state["sync_error"])
                else:
                    _, _, _, low, high = self.path.split("/")
                    self.respond(state["frames"][(int(low) - 1) * FRAME : (int(high) - 1) * FRAME])
            else:
                self.respond(b"", 404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        database.close()


def fetch(provider, target, **kwargs):
    url, _ = provider
    return fetch_checkpoint(url, "fixture-token", target, allow_test_loopback=True, **kwargs)


def test_real_wal_replay_becomes_standalone_sqlite(provider, tmp_path):
    destination = tmp_path / "checkpoint.db"
    receipt = fetch(provider, destination)
    with sqlite3.connect(destination) as db:
        assert db.execute("SELECT key,value FROM sample ORDER BY key").fetchall() == [
            ("new", 99),
            ("old", 42),
        ]
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert receipt["committed_frame"] == len(provider[1]["frames"]) // FRAME
    assert receipt["sqlite_verified_wal_frames"] == receipt["committed_frame"]
    assert receipt["database_sha256"] == hashlib.sha256(destination.read_bytes()).hexdigest()
    assert receipt["base_db_sha256"] == hashlib.sha256(provider[1]["base"]).hexdigest()
    assert receipt["live_coverage_verified"] is False
    assert receipt["policy_freshness_attested"] is False
    assert provider[1]["paths"].count("/export/7") == 1
    assert not list(tmp_path.glob(".checkpoint-*"))


@pytest.mark.parametrize("status", [400, 500])
def test_sync_http_error_is_not_a_valid_empty_wal(provider, tmp_path, status):
    provider[1]["sync_error"] = status
    destination = tmp_path / "checkpoint.db"
    with pytest.raises(CheckpointError, match=f"Source HTTP {status}"):
        fetch(provider, destination)
    assert not destination.exists()
    assert not list(tmp_path.glob(".checkpoint-*"))


def test_trailing_uncommitted_frames_are_not_replayed(provider, tmp_path):
    frame = bytearray(provider[1]["frames"][-FRAME:])
    frame[4:8] = struct.pack(">I", 0)
    frame[24:] = b"x" * 4096
    provider[1]["frames"] += frame
    receipt = fetch(provider, tmp_path / "checkpoint.db")
    assert receipt["received_wal_frames"] == receipt["committed_frame"] + 1
    with sqlite3.connect(tmp_path / "checkpoint.db") as db:
        assert db.execute("SELECT value FROM sample WHERE key='old'").fetchone()[0] == 42


@pytest.mark.parametrize("failure", ["truncated", "oversized", "regressed"])
def test_invalid_or_over_budget_checkpoint_never_publishes(provider, tmp_path, failure):
    options = {}
    if failure == "truncated":
        provider[1]["frames"] = provider[1]["frames"][:-1]
    elif failure == "oversized":
        options["max_bytes"] = 4096
    else:
        provider[1]["after_generation"] = 6
    destination = tmp_path / "checkpoint.db"
    with pytest.raises(CheckpointError):
        fetch(provider, destination, **options)
    assert not destination.exists()
    assert not list(tmp_path.glob(".checkpoint-*"))


def test_existing_destination_and_partial_files_are_preserved(provider, tmp_path):
    destination = tmp_path / "checkpoint.db"
    destination.write_bytes(b"operator file")
    paths = list(provider[1]["paths"])
    with pytest.raises(CheckpointError, match="already exists"):
        fetch(provider, destination)
    assert destination.read_bytes() == b"operator file"
    assert provider[1]["paths"] == paths


def test_exact_full_batch_requires_successful_empty_followup(provider, tmp_path):
    frames = provider[1]["frames"]
    assert 128 % (len(frames) // FRAME) == 0
    provider[1]["frames"] *= 128 // (len(frames) // FRAME)
    receipt = fetch(provider, tmp_path / "checkpoint.db")
    assert receipt["committed_frame"] == 128
    assert [path for path in provider[1]["paths"] if path.startswith("/sync/")] == [
        "/sync/7/1/129",
        "/sync/7/129/257",
    ]
    with sqlite3.connect(tmp_path / "checkpoint.db") as db:
        assert db.execute("SELECT value FROM sample WHERE key='old'").fetchone()[0] == 42


def test_empty_successful_wal_keeps_the_base_checkpoint(provider, tmp_path):
    provider[1]["frames"] = b""
    receipt = fetch(provider, tmp_path / "checkpoint.db")
    assert receipt["committed_frame"] == 0
    assert receipt["sqlite_verified_wal_frames"] == 0
    with sqlite3.connect(tmp_path / "checkpoint.db") as db:
        assert db.execute("SELECT key,value FROM sample").fetchall() == [("old", 1)]


def test_generation_advance_remains_checkpoint_only(provider, tmp_path):
    provider[1]["after_generation"] = 8
    receipt = fetch(provider, tmp_path / "checkpoint.db")
    assert receipt["generation"] == 7
    assert receipt["generation_after"] == 8
    assert receipt["live_coverage_verified"] is False


def test_redirect_does_not_forward_credentials_or_publish(provider, tmp_path):
    provider[1]["redirect_export"] = True
    destination = tmp_path / "checkpoint.db"
    with pytest.raises(CheckpointError, match="redirect"):
        fetch(provider, destination)
    assert not destination.exists()
    assert not list(tmp_path.glob(".checkpoint-*"))


def test_stalled_export_hits_request_deadline_and_cleans_up(provider, tmp_path):
    provider[1]["export_delay"] = 1.2
    destination = tmp_path / "checkpoint.db"
    with pytest.raises(CheckpointError, match=r"transport|deadline"):
        fetch(provider, destination, max_seconds=1)
    assert not destination.exists()
    assert not list(tmp_path.glob(".checkpoint-*"))


def test_regressing_source_clock_cannot_publish(provider, tmp_path):
    provider[1]["clocks"] = [1793236000, 1793235999]
    with pytest.raises(CheckpointError, match="regressed"):
        fetch(provider, tmp_path / "checkpoint.db")
    assert not (tmp_path / "checkpoint.db").exists()


@pytest.mark.parametrize("prefix", ["/export/", "/sync/"])
def test_slow_response_body_cannot_hide_the_application_deadline(provider, tmp_path, prefix):
    provider[1]["slow_body_prefix"] = prefix
    destination = tmp_path / "checkpoint.db"
    started = time.monotonic()
    with pytest.raises(CheckpointError, match="deadline"):
        fetch(provider, destination, max_seconds=1)
    assert time.monotonic() - started < 1.5
    assert not destination.exists()
    assert not list(tmp_path.glob(".checkpoint-*"))


def test_progress_contains_only_bounded_transfer_metadata(provider, tmp_path):
    events = []
    receipt = fetch(provider, tmp_path / "reported.db", progress=events.append, max_seconds=900)
    assert [event["phase"] for event in events] == [
        "base-export",
        "wal-export",
        "sqlite-replay",
        "materialized-hash",
    ]
    assert all(
        set(event) == {"phase", "received_bytes", "requests", "elapsed_seconds"} for event in events
    )
    assert events[-1]["received_bytes"] == receipt["received_source_bytes"]
    assert events[-1]["requests"] == receipt["source_requests"]
    assert all(event["elapsed_seconds"] >= 0 for event in events)
