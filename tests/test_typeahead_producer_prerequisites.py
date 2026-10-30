import hashlib
import logging
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import boto3
import pytest
from botocore.config import Config
from mps.typeahead_tantivy import fetch_cloud_binary

from flows import typeahead_index as index


def test_code_download_checksum_cache_and_failure_cleanup(tmp_path):
    payload = bytearray(4096)
    payload[:6] = b"\x7fELF\x02\x01"
    payload[18:20] = b"\x3e\x00"
    payload = bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = boto3.client(
        "s3",
        endpoint_url=f"http://127.0.0.1:{server.server_port}",
        region_name="auto",
        aws_access_key_id="fixture",
        aws_secret_access_key="fixture",
        config=Config(retries={"max_attempts": 0}),
    )
    cache = tmp_path / "code"
    destination = cache / "typeahead-tantivy"
    try:
        assert (
            fetch_cloud_binary(destination, digest, cache, client=client, bucket="fixture")
            == destination
        )
        assert destination.read_bytes() == payload
        assert destination.stat().st_mode & 0o111 == 0o111
        fetch_cloud_binary(destination, digest, cache, client=client, bucket="fixture")
        assert len(requests) == 1
        assert requests[0] == f"/fixture/releases/code/tantivy/{digest}/typeahead-tantivy"
        destination.unlink()
        with pytest.raises(ValueError, match="checksum"):
            fetch_cloud_binary(destination, "0" * 64, cache, client=client, bucket="fixture")
        assert not destination.exists()
        assert not destination.with_suffix(".partial").exists()
        destination.with_suffix(".partial").write_text("unowned partial")
        with pytest.raises(FileExistsError):
            fetch_cloud_binary(destination, digest, cache, client=client, bucket="fixture")
        assert destination.with_suffix(".partial").read_text() == "unowned partial"
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join()


def test_isolated_exact_commit_checkouts_preserve_native_checkout(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(source), *args], text=True).strip()

    git("init", "--quiet")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.test")
    (source / "value").write_text("first")
    git("add", "value")
    git("commit", "--quiet", "-m", "first")
    first = git("rev-parse", "HEAD")
    (source / "value").write_text("second")
    git("commit", "--quiet", "-am", "second")
    native = tmp_path / "native"
    native.mkdir()
    (native / "dirty").write_text("preserve")
    monkeypatch.setattr(index, "INDEXER_HOME", tmp_path / "work")
    monkeypatch.setattr(index, "REPO_DIR", native)
    monkeypatch.setattr(index, "REPO_URL", str(tmp_path / "unavailable"))
    monkeypatch.setattr(index, "REPO_URL_FALLBACK", str(source))
    monkeypatch.setattr(index, "get_run_logger", lambda: logging.getLogger(__name__))
    a = index.clone_repo.fn(first, isolated=True)
    b = index.clone_repo.fn(first, isolated=True)
    assert a != b
    assert (a / "value").read_text() == (b / "value").read_text() == "first"
    assert (
        subprocess.check_output(["git", "-C", str(a), "rev-parse", "HEAD"], text=True).strip()
        == first
    )
    assert (native / "dirty").read_text() == "preserve"
