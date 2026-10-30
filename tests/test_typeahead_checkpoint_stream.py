import hashlib
import importlib.util
import io
import json
import os
import sqlite3
import subprocess
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from mps.typeahead_checkpoint import FRAME, fetch_checkpoint
from mps.typeahead_checkpoint_stream import file_witness, stream_checkpoint

_spec = importlib.util.spec_from_file_location(
    "seed_fixture", Path(__file__).with_name("test_typeahead_sqlite_seed.py")
)
assert _spec is not None and _spec.loader is not None
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
source = _fixture.source


@pytest.fixture
def repository():
    configured = os.environ.get("TA_REPOSITORY")
    if not configured:
        pytest.skip("requires canonical Typeahead repository and prebuilt D1 image")
    return Path(configured)


@contextmanager
def provider_checkpoint(tmp_path, mutation=None):
    path, database = source(tmp_path)
    database.execute(
        "UPDATE actors SET labels=?,associated=?,avatar_url=? WHERE did='did:fixture:000001'",
        (
            '[ {"src":"did:fixture:labeler","val":"test","exp":"2027-10-28T00:00:00Z"} ]',
            '{"chat":{"allowIncoming":"all"}}',
            "https://avatar.test/one",
        ),
    )
    database.commit()
    if mutation:
        mutation(database)
        database.commit()
    base = path.read_bytes()
    frames = Path(str(path) + "-wal").read_bytes()[32:]
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *_args):
            pass

        def reply(self, body):
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            requests.append(self.path)
            if self.path == "/info":
                self.reply(b'{"current_generation":7,"db_type":"sqlite"}')
            elif self.path == "/export/7":
                self.reply(base)
            elif self.path.startswith("/sync/7/"):
                low, high = map(int, self.path.split("/")[-2:])
                self.reply(frames[(low - 1) * FRAME : (high - 1) * FRAME])
            else:
                self.send_error(404)

        def do_POST(self):
            statement = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert statement["requests"][0]["stmt"]["sql"] == "SELECT unixepoch() AS source_now"
            requests.append(self.path)
            self.reply(
                json.dumps(
                    {
                        "results": [
                            {
                                "type": "ok",
                                "response": {
                                    "type": "execute",
                                    "result": {
                                        "rows": [[{"type": "integer", "value": "1793236000"}]]
                                    },
                                },
                            }
                        ]
                    }
                ).encode()
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    destination = tmp_path / "checkpoint.db"
    try:
        receipt = fetch_checkpoint(
            f"http://127.0.0.1:{server.server_port}",
            "fixture",
            destination,
            allow_test_loopback=True,
        )
        yield destination, receipt, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        database.close()


def arguments(path, receipt):
    raw = json.dumps(receipt, indent=2).encode() + b"\n"
    anchor = json.dumps(
        {
            "manifest_version": 2,
            "format_version": 5,
            "active_build": "b1793230000-old-native",
            "source_watermark": 1793230000,
            "actor_count": 123456,
            "sha256": "a" * 64,
            "prefix_map": {"sha256": "b" * 64},
            "legacy_twin": {"build_id": "b1793230000-old-legacy", "source_watermark": 1793230000},
        }
    ).encode()
    return {
        "receipt_bytes": raw,
        "receipt_sha256": hashlib.sha256(raw).hexdigest(),
        "expected_source_origin_sha256": receipt["source_origin_sha256"],
        "expected_file_witness": file_witness(path),
        "native_manifest_bytes": anchor,
        "native_manifest_sha256": hashlib.sha256(anchor).hexdigest(),
        "build_id": "b1793236000-checkpoint-stream",
        "allow_fixture": True,
    }


def records(output):
    return [json.loads(line) for line in output.getvalue().splitlines()]


def test_complete_wal_stream_fields_counts_policy_and_raw_provenance(tmp_path, repository):
    with provider_checkpoint(tmp_path) as (path, proof, requests):
        options = arguments(path, proof)
        output = io.BytesIO()
        result = stream_checkpoint(path, output, repository, **options)
        lines = records(output)
        header, footer = lines[0], lines[-1]
        actors = {row["did"]: row for row in lines[1:-1]}
        bootstrap = header["source_bootstrap"]
        assert requests.count("/info") == 2
        assert header["partial"] is False and header["source_actor_count"] == 33
        assert bootstrap["counts"] == {
            "raw_actor_rows": 32,
            "orphan_tombstones": 1,
            "input_documents": 33,
            "live_actor_documents": 31,
            "deleted_actor_documents": 2,
            "native_visible_actors": None,
        }
        assert bootstrap["fixture"] is True and proof["fixture"] is True
        assert bootstrap["checkpoint_receipt_json"].encode() == options["receipt_bytes"]
        assert bootstrap["checkpoint_receipt_sha256"] == options["receipt_sha256"]
        assert bootstrap["checkpoint_receipt"] == proof
        assert "actors_sha256" not in bootstrap
        assert header["source_watermark"] == 1793235700
        assert header["rollback_native_anchor"]["actor_count"] == 123456
        assert header["legacy_build_id"] == "b1793230000-old-legacy"
        assert header["source_kind"] == "sqlite-committed-prefix"
        assert header["source_policy"] == "sqlite-checkpoint-observations-v1"
        assert "source_prefix_sha256" not in header
        policy = bootstrap["checkpoint_policy"]
        assert policy["complete"] is True and "source_observed_at" not in policy
        assert policy["overrides"] == [{"did": "did:fixture:000003", "action": "show"}]
        assert policy["blocked_domains"] == ["blocked.test"]
        assert actors["did:fixture:000000"]["followers_count"] == 12345
        assert actors["did:fixture:000000"]["quality_score"] == 16777216
        assert actors["did:fixture:000000"]["source_updated_at"] == 99
        assert actors["did:fixture:000000"]["deleted"] is False
        assert actors["did:fixture:000004"]["deleted"] is True
        assert actors["did:fixture:orphan"]["deleted"] is True
        assert actors["did:fixture:000003"]["hidden"] is True
        assert actors["did:fixture:000000"]["labels"] == []
        assert actors["did:fixture:000000"]["associated"] == {}
        assert actors["did:fixture:000001"]["associated"] == {"chat": {"allowIncoming": "all"}}
        assert actors["did:fixture:000001"]["avatar_url"] == "https://avatar.test/one"
        assert actors["did:fixture:000001"]["labels"][0]["exp"] == "2027-10-28T00:00:00Z"
        assert all(row["version"] == header["source_watermark"] for row in actors.values())
        assert list(actors) == sorted(actors)
        assert (
            footer["actors_sha256"]
            == hashlib.sha256(
                b"".join(output.getvalue().splitlines(keepends=True)[1:-1])
            ).hexdigest()
        )
        assert result["actors_sha256"] == footer["actors_sha256"]
        assert result["consumer_verified"] is False
        assert result["live_coverage_verified"] is False
        assert result["policy_freshness_attested"] is False


@pytest.mark.parametrize(
    "failure",
    [
        "fixture",
        "receipt",
        "witness",
        "database",
        "sidecar",
        "anchor",
        "origin",
        "documents",
        "bytes",
        "deadline",
    ],
)
def test_rejects_unbound_or_exhausted_inputs_without_footer(tmp_path, repository, failure):
    with provider_checkpoint(tmp_path) as (path, proof, _):
        options = arguments(path, proof)
        if failure == "fixture":
            options["allow_fixture"] = False
        elif failure == "receipt":
            options["receipt_bytes"] += b" "
        elif failure == "witness":
            options["expected_file_witness"]["inode"] += 1
        elif failure == "database":
            with path.open("r+b") as file:
                file.seek(-1, 2)
                file.write(b"x")
            options["expected_file_witness"] = file_witness(path)
        elif failure == "sidecar":
            Path(str(path) + "-wal").touch()
        elif failure == "anchor":
            options["native_manifest_sha256"] = "0" * 64
        elif failure == "origin":
            options["expected_source_origin_sha256"] = "0" * 64
        elif failure == "documents":
            options["max_documents"] = 32
        elif failure == "bytes":
            options["max_output_bytes"] = 4096
        elif failure == "deadline":
            options["max_seconds"] = 0.00000001
        output = io.BytesIO()
        with pytest.raises((ValueError, TimeoutError)):
            stream_checkpoint(path, output, repository, **options)
        assert all(row.get("type") != "end" for row in records(output))


def test_source_changed_after_header_has_no_success_footer(tmp_path, repository):
    with provider_checkpoint(tmp_path) as (path, proof, _):
        options = arguments(path, proof)

        class MutatingOutput(io.BytesIO):
            def write(self, value):
                result = super().write(value)
                if self.tell() == len(value):
                    with sqlite3.connect(path) as db:
                        db.execute(
                            "UPDATE actors SET display_name='changed' WHERE did='did:fixture:000001'"
                        )
                return result

        output = MutatingOutput()
        with pytest.raises(ValueError, match="changed"):
            stream_checkpoint(path, output, repository, **options)
        assert records(output)[0]["type"] == "manifest"
        assert all(row.get("type") != "end" for row in records(output))


def test_actual_linux_d1_import_direct_pipe_retains_full_unqualified_checkpoint(
    tmp_path, repository
):
    image = os.environ.get("TA_LINUX_IMAGE")
    if not image:
        pytest.skip("requires frozen Linux D1 image")
    with provider_checkpoint(tmp_path) as (path, proof, _):
        data = tmp_path / "rust"
        data.mkdir()
        command = [
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
            "--network",
            "none",
            "--cpus",
            "2",
            "--memory",
            "1g",
            "-i",
            "-v",
            f"{data}:/data",
            image,
            "/app/typeahead-tantivy",
            "import",
            "--data",
            "/data/index",
            "--input",
            "-",
            "--writer-memory-mb",
            "32",
        ]
        child = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        try:
            result = stream_checkpoint(path, child.stdin, repository, **arguments(path, proof))
            assert child.stdin is not None
            child.stdin.close()
            child.stdin = None
            stdout, stderr = child.communicate(timeout=60)
            assert child.returncode == 0, stderr.decode()[-2000:]
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
        metadata = json.loads(json.loads((data / "index/index/meta.json").read_bytes())["payload"])
        assert metadata["full_source"] is True
        assert metadata["input_actors"] == 33
        assert metadata["source_manifest"] == result["source_manifest"]
        assert metadata["source_cursor"] is None
        assert metadata["source_observation_revision"] == 0
        expiry = metadata["expiry_coverage"]
        assert expiry["verified_input_actors_sha256"] == result["actors_sha256"]
        assert expiry["current"]["unsupported_expiry"] == 0
        assert expiry["current"]["supported_expiry"] == 1
        assert expiry["input_actors_checked"] == 33
        assert (
            expiry["canonical_rules_source_sha256"]
            == result["source_manifest"]["canonical_rules_source_sha256"]
        )
        assert stdout
        script = r"""import hashlib,json,subprocess,time,urllib.request
assert hashlib.sha256(open('/app/typeahead-tantivy','rb').read()).hexdigest()=='d1cc0366ed3b28b34daca15d23ee6f06aa1d08844e483c357b80cdb778d5cfa2'
for restart in range(2):
    child=subprocess.Popen(['/app/typeahead-tantivy','serve','--data','/data/index','--listen','127.0.0.1:18080','--admin-listen','127.0.0.1:18081','--allow-stale','true','--writer-memory-mb','32'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for attempt in range(100):
            try:
                status=json.load(urllib.request.urlopen('http://127.0.0.1:18080/index/status',timeout=1));break
            except OSError:
                if child.poll() is not None:raise RuntimeError('Rust startup failed')
                time.sleep(.05)
        else:raise RuntimeError('Rust startup timeout')
        assert status['full_source'] and not status['cutover_source_ready']
        assert status['source_manifest']['source_bootstrap']['fixture'] is True
        assert status['source_cursor'] is None
        actors=json.load(urllib.request.urlopen('http://127.0.0.1:18080/search?q=person000000',timeout=2))['actors']
        assert actors[0]['did']=='did:fixture:000000'
        assert json.load(urllib.request.urlopen('http://127.0.0.1:18080/search?q=person000003',timeout=2))['actors']==[]
        assert json.load(urllib.request.urlopen('http://127.0.0.1:18080/search?q=person000004',timeout=2))['actors']==[]
    finally:
        child.terminate();child.wait(timeout=10)
print(json.dumps({'restarts':2,'full_source':True,'cutover_source_ready':False,'binary_sha256':'d1cc0366ed3b28b34daca15d23ee6f06aa1d08844e483c357b80cdb778d5cfa2'}))
"""
        checked = subprocess.run(
            [*command[: command.index("/app/typeahead-tantivy")], "python3", "-c", script],
            capture_output=True,
            check=False,
            timeout=45,
        )
        assert checked.returncode == 0, checked.stderr.decode()[-2000:]
        assert json.loads(checked.stdout)["restarts"] == 2


@pytest.mark.parametrize("payload", ["not-json", "{}"])
def test_malformed_hydration_cannot_finish_stream(tmp_path, repository, payload):
    def mutation(database):
        database.execute("UPDATE actors SET labels=? WHERE did='did:fixture:000001'", (payload,))

    with provider_checkpoint(tmp_path, mutation) as (path, proof, _):
        output = io.BytesIO()
        with pytest.raises(ValueError, match="hydration"):
            stream_checkpoint(path, output, repository, **arguments(path, proof))
        assert records(output)[0]["type"] == "manifest"
        assert all(row.get("type") != "end" for row in records(output))


def test_complete_policy_cap_is_not_silently_truncated(tmp_path, repository):
    def mutation(database):
        database.executemany(
            "INSERT INTO blocked_domains VALUES(?)", ((f"domain{n}.test",) for n in range(10001))
        )

    with provider_checkpoint(tmp_path, mutation) as (path, proof, _):
        output = io.BytesIO()
        with pytest.raises(ValueError, match="policy"):
            stream_checkpoint(path, output, repository, **arguments(path, proof))
        assert output.getvalue() == b""
