import ast
import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from mps.typeahead_checkpoint import fetch_checkpoint
from mps.typeahead_checkpoint_build import build_checkpoint, import_diagnostics, module
from mps.typeahead_checkpoint_stream import file_witness


def release_manifest(imported, build, adapter, binary_sha):
    source = (
        Path(__file__).resolve().parents[1] / "packages/mps/src/mps/typeahead_checkpoint_release.py"
    )
    tree = ast.parse(source.read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "run_cloud_release"
    )
    index = next(
        index
        for index, node in enumerate(function.body)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "manifest" for target in node.targets)
    )
    nodes = function.body[index : index + 2]
    assert (
        isinstance(nodes[1], ast.Expr)
        and isinstance(nodes[1].value, ast.Call)
        and isinstance(nodes[1].value.func, ast.Attribute)
        and nodes[1].value.func.attr == "update"
    )
    namespace = {"imported": imported, "build": build, "adapter": adapter, "BINARY_SHA": binary_sha}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
    return namespace["manifest"]


@contextmanager
def checkpoint_source(root, repository, labels=None, missing_pds=False, clock_offset=0):
    spec = importlib.util.spec_from_file_location(
        "checkpoint_build_source_fixture", repository / "services/tantivy-input/test_sync.py"
    )
    assert spec is not None and spec.loader is not None
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    source = fixtures.Fixture(root, now=int(time.time()) + clock_offset)
    keeper = sqlite3.connect(source.path)
    try:
        for n in range(32):
            source.actor(f"did:fixture:{n:06}", source.now - 1, hidden=n == 3)
        source.tombstone("did:fixture:orphan", source.now - 1)
        keeper.execute("INSERT INTO mod_overrides(did,action) VALUES('did:fixture:000003','show')")
        keeper.execute("INSERT INTO blocked_domains(domain) VALUES('blocked.test')")
        keeper.commit()
        keeper.execute("PRAGMA journal_mode=WAL")
        keeper.execute("PRAGMA wal_autocheckpoint=0")
        keeper.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        base = source.path.read_bytes()
        keeper.execute(
            "UPDATE actors SET followers_count=12345,quality_score=16777217 WHERE did='did:fixture:000000'"
        )
        keeper.execute("UPDATE actors SET handle='' WHERE did='did:fixture:000004'")
        if missing_pds:
            keeper.execute("UPDATE actors SET pds='' WHERE did='did:fixture:000001'")
        if labels is not None:
            keeper.execute("UPDATE actors SET labels=? WHERE did='did:fixture:000000'", (labels,))
        keeper.commit()
        frames = Path(str(source.path) + "-wal").read_bytes()[32:]
        original = source.server.RequestHandlerClass

        class Provider(original):
            def do_GET(self):
                if self.headers.get("Authorization") != "Bearer " + source.source_secret:
                    self.send_error(401)
                    return
                if self.path == "/info":
                    body = b'{"current_generation":7}'
                elif self.path == "/export/7":
                    body = base
                elif self.path.startswith("/sync/7/"):
                    low, high = map(int, self.path.split("/")[-2:])
                    body = frames[(low - 1) * 4120 : (high - 1) * 4120]
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        source.server.RequestHandlerClass = Provider
        database = root / "checkpoint.db"
        receipt = fetch_checkpoint(
            source.url, source.source_secret, database, allow_test_loopback=True, max_seconds=10
        )
        raw = json.dumps(receipt, indent=2).encode() + b"\n"
        native = json.dumps(
            {
                "manifest_version": 2,
                "format_version": 5,
                "normalize_version": "v2-cjk-bigram",
                "active_build": "b1793230000-native",
                "actor_count": 123456,
                "source_watermark": 1793230000,
                "sha256": "a" * 64,
                "prefix_map": {"sha256": "b" * 64},
                "legacy_twin": {"build_id": "b1793230000-legacy", "source_watermark": 1793230000},
            }
        ).encode()
        yield (
            source,
            database,
            {
                "receipt_bytes": raw,
                "receipt_sha256": hashlib.sha256(raw).hexdigest(),
                "expected_source_origin_sha256": receipt["source_origin_sha256"],
                "expected_file_witness": file_witness(database),
                "native_manifest_bytes": native,
                "native_manifest_sha256": hashlib.sha256(native).hexdigest(),
                "build_id": f"b{source.now}-checkpoint-build",
                "environment": dict(
                    os.environ, TURSO_URL=source.url, TURSO_AUTH_TOKEN=source.source_secret
                ),
                "timeout": 30,
                "min_request_interval": 0,
                "allow_fixture": True,
            },
        )
    finally:
        keeper.close()
        source.close()


@unittest.skipUnless(
    os.environ.get("TA_REPOSITORY") and os.environ.get("TA_BINARY"),
    "requires canonical repository and prebuilt D1",
)
class Tests(unittest.TestCase):
    def setUp(self):
        self.repository = Path(os.environ["TA_REPOSITORY"])
        self.binary = Path(os.environ["TA_BINARY"])
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def test_verified_footer_crash_resumes_without_reimport(self):
        import shlex

        with checkpoint_source(self.root, self.repository) as (_, database, options):
            wrapper = self.root / "crash-import"
            wrapper.write_text(
                "#!/bin/sh\nulimit -c 0\n"
                'if [ "$1" = import ]; then export TYPEAHEAD_TEST_CRASH=import_after_footer; fi\n'
                + "exec "
                + shlex.quote(str(self.binary))
                + ' "$@"\n'
            )
            wrapper.chmod(0o700)
            previous = self.root / "interrupted"
            with self.assertRaises(ValueError):
                build_checkpoint(self.repository, previous, wrapper, database, **options)
            self.assertTrue((previous / "stream-receipt.json").is_file())
            result = build_checkpoint(
                self.repository,
                self.root / "resumed",
                self.binary,
                database,
                resume_import_from=previous,
                **options,
            )
            self.assertTrue(result["consumer_verified"] and result["live_handoff_verified"])
            self.assertFalse((previous / "data").exists())
            self.assertEqual(result["stream"]["input_documents"], 33)

    def test_real_pipe_import_cas_policy_handoff_without_second_identity_walk(self):
        with checkpoint_source(self.root, self.repository) as (source, database, options):
            result = build_checkpoint(
                self.repository, self.root / "build", self.binary, database, **options
            )
            fields = result["manifest_fields"]
            record = result["checkpoint_reconciliation"]
            handoff = result["checkpoint_handoff"]
            status = json.loads(
                subprocess.check_output(
                    [str(self.binary), "status", "--data", result["data_path"]], timeout=10
                )
            )
            cursor = status["source_cursor"]
            self.assertTrue(result["consumer_verified"] and result["live_handoff_verified"])
            self.assertFalse(result["provider_qualified"])
            self.assertEqual(record["kind"], "verified-checkpoint-import")
            self.assertEqual(record["verified_input_actors"], 33)
            self.assertEqual(record["initial_live_actor_documents"], 31)
            self.assertEqual(record["initial_deleted_actor_documents"], 2)
            self.assertNotIn("documents_checked", record)
            self.assertNotIn("source_done", record)
            self.assertEqual(handoff["prior_observation_revision"], 0)
            self.assertEqual(handoff["prior_cursor_sha256"], hashlib.sha256(b"null").hexdigest())
            self.assertEqual(cursor["checkpoint_reconciliation"], record)
            self.assertEqual(cursor["checkpoint_handoff"], handoff)
            self.assertEqual(cursor["source_policy"], "turso-authoritative-observations-v2")
            self.assertIsNone(cursor["cycle"])
            self.assertGreaterEqual(
                handoff["first_remote_through"],
                json.loads(options["receipt_bytes"])["source_clock_after"],
            )
            self.assertEqual(cursor["actors"], {"updated_at": cursor["through"], "did": ""})
            self.assertEqual(cursor["tombstones"], {"deleted_at": cursor["through"], "did": ""})
            self.assertNotIn("last_completed_at", cursor.get("reconciliation", {}))
            self.assertGreater(status["source_observation_revision"], 1)
            self.assertEqual(fields["source_build_id"], status["source_manifest"]["build_id"])
            self.assertEqual(fields["rollback_native_anchor"]["actor_count"], 123456)
            self.assertEqual(fields["source_manifest_sha256"], options["receipt_sha256"])
            self.assertEqual(
                status["expiry_coverage"]["verified_input_actors_sha256"],
                fields["source_actors_sha256"],
            )
            private_metadata = json.loads(
                json.loads((Path(result["data_path"]) / "index/meta.json").read_bytes())["payload"]
            )
            policy = private_metadata.get("policy_snapshot")
            self.assertTrue(policy["complete"])
            self.assertEqual(
                policy["overrides"],
                [{"did": "did:fixture:000003", "action": "show"}],
            )
            self.assertGreaterEqual(
                policy["source_observed_at"],
                json.loads(options["receipt_bytes"])["source_clock_after"],
            )
            self.assertFalse(
                any(
                    request["sql"] == "SELECT did FROM actors WHERE did > ?1 ORDER BY did LIMIT ?2"
                    for request in source.requests
                )
            )
            self.assertTrue(
                any("AS overrides_json" in request["sql"] for request in source.requests)
            )
            self.assertGreater(result["source_accounting"]["source_bytes"], 0)
            self.assertEqual(result["source_accounting"]["sweep_bytes"], 0)
            self.assertFalse(list((self.root / "build").glob("*.jsonl")))
            self.assertNotIn("blocked.test", json.dumps(fields))
            installer = module(
                "checkpoint_build_freeze",
                self.repository / "scripts/snapshot_refresh/tantivy_install.py",
            )
            with self.binary.open("rb") as binary_file:
                binary_sha = hashlib.file_digest(binary_file, "sha256").hexdigest()
            manifest = release_manifest(
                result, options["build_id"], SimpleNamespace(installer=installer), binary_sha
            )
            portable = installer.freeze_bundle(
                Path(result["data_path"]),
                self.root / "publication",
                manifest,
                options["native_manifest_bytes"],
                self.binary,
                allow_checkpoint_fixture=True,
            )
            self.assertEqual(portable["source_policy"], "sqlite-checkpoint-observations-v1")
            self.assertEqual(portable["source_build_id"], status["source_manifest"]["build_id"])
            self.assertEqual(
                portable["source_actors_sha256"],
                status["expiry_coverage"]["verified_input_actors_sha256"],
            )
            self.assertEqual(portable["source_manifest_sha256"], options["receipt_sha256"])
            self.assertEqual(
                portable["rollback_native_anchor"]["manifest_sha256"],
                options["native_manifest_sha256"],
            )
            self.assertTrue(portable["test_only"])
            installer.validate_artifact_manifest(portable, options["build_id"])
            installer.validate_checkpoint_provenance(
                portable, options["receipt_bytes"], options["native_manifest_bytes"]
            )
            with (self.root / "publication/index.tar").open("rb") as bundle:
                self.assertEqual(
                    portable["sha256"], hashlib.file_digest(bundle, "sha256").hexdigest()
                )

    def test_join_is_bound_through_checkpoint_catchup_and_portable_bundle(self):
        mapping = self.root / "fallback.sqlite"
        with sqlite3.connect(mapping) as db:
            db.executescript(
                "PRAGMA user_version=1; CREATE TABLE pds_fallback(did TEXT PRIMARY KEY,pds TEXT NOT NULL) WITHOUT ROWID;"
            )
            db.execute(
                "INSERT INTO pds_fallback VALUES (?,?)",
                ("did:fixture:000001", "https://joined.example"),
            )
        digest = hashlib.sha256(mapping.read_bytes()).hexdigest()
        with checkpoint_source(self.root, self.repository, missing_pds=True) as (
            _source,
            database,
            options,
        ):
            result = build_checkpoint(
                self.repository,
                self.root / "build",
                self.binary,
                database,
                **options,
                pds_fallback_path=mapping,
                pds_fallback_sha256=digest,
            )
            self.assertEqual(
                result["stream"]["pds_coverage"],
                {"source_known": 30, "source_missing": 1, "joined": 1, "remaining_missing": 0},
            )
            data = Path(result["data_path"])
            self.assertEqual(
                hashlib.sha256((data / "pds-fallback.sqlite").read_bytes()).hexdigest(), digest
            )
            installer = module(
                "fallback_freeze", self.repository / "scripts/snapshot_refresh/tantivy_install.py"
            )
            binary_sha = hashlib.sha256(self.binary.read_bytes()).hexdigest()
            manifest = release_manifest(
                result, options["build_id"], SimpleNamespace(installer=installer), binary_sha
            )
            portable = installer.freeze_bundle(
                data,
                self.root / "publication",
                manifest,
                options["native_manifest_bytes"],
                self.binary,
                allow_checkpoint_fixture=True,
            )
            installer.validate_artifact_manifest(portable, options["build_id"])
            self.assertEqual(portable["pds_fallback"]["sha256"], digest)
            self.assertIn("pds-fallback.sqlite", [r["path"] for r in portable["files"]])
            status = json.loads(
                subprocess.check_output([str(self.binary), "status", "--data", str(data)])
            )
            self.assertEqual(status["pds_missing_actor_count"], 0)
            self.assertEqual(status["source_manifest"]["pds_fallback"], portable["pds_fallback"])
            installed = self.root / "installed"
            provider_manifest = dict(
                portable,
                image_digest="registry.fly.io/tantivy-fixture@sha256:" + "e" * 64,
                guest=dict(installer.GUEST),
                volume_id="vol_fixture",
                volume_size_gib=20,
                public_service_ports=[],
                provider_app="tantivy-fixture",
            )
            receipt = installer.install_bundle(
                self.root / "publication/index.tar",
                installed,
                provider_manifest,
                self.binary,
                allow_checkpoint_fixture=True,
            )
            self.assertTrue(receipt["passed"])
            installer.validate_fallback_file(installed, portable)
            restarted = json.loads(
                subprocess.check_output([str(self.binary), "status", "--data", str(installed)])
            )
            self.assertEqual(restarted["pds_missing_actor_count"], 0)
            self.assertEqual(restarted["source_manifest"]["pds_fallback"], portable["pds_fallback"])
            with (installed / "pds-fallback.sqlite").open("r+b") as broken:
                broken.seek(-1, 2)
                broken.write(b"x")
            with self.assertRaisesRegex(ValueError, "checksum"):
                installer.validate_fallback_file(installed, portable)
            unbound = dict(portable)
            unbound.pop("pds_fallback")
            with self.assertRaisesRegex(ValueError, "Unbound fallback"):
                installer.validate_artifact_manifest(unbound, options["build_id"])
            (data / "pds-fallback.sqlite").unlink()
            with self.assertRaisesRegex(ValueError, "fallback file"):
                installer.validate_fallback_file(data, portable)

    def test_failed_producer_and_importer_preserve_safe_exit_diagnostics(self):
        with checkpoint_source(self.root, self.repository, labels="private-profile-marker") as (
            _,
            database,
            options,
        ):
            with self.assertRaisesRegex(ValueError, "producer_validation") as error:
                build_checkpoint(
                    self.repository, self.root / "build", self.binary, database, **options
                )
            receipt = json.loads((self.root / "build/import-failure.json").read_bytes())
            self.assertEqual(receipt["producer_exit"], 1)
            self.assertEqual(receipt["producer_error_class"], "ValueError")
            self.assertEqual(receipt["cause"], "producer_validation")
            self.assertGreater(receipt["stream_log_bytes"], 0)
            self.assertNotIn("private-profile-marker", str(error.exception))
            self.assertNotIn(options["environment"]["TURSO_AUTH_TOKEN"], str(error.exception))
            self.assertNotIn("http://", json.dumps(receipt))

    def test_unbuffered_producer_deadline_exits_when_consumer_does_not_read(self):
        with checkpoint_source(
            self.root, self.repository, labels=json.dumps([{"val": "x" * 131072}])
        ) as (_, database, options):
            request = {
                key: value
                for key, value in options.items()
                if key not in ("environment", "timeout", "min_request_interval")
            }
            request.update(database=str(database), repository=str(self.repository), max_seconds=1)
            request["receipt_bytes"] = request["receipt_bytes"].decode()
            request["native_manifest_bytes"] = request["native_manifest_bytes"].decode()
            path = self.root / "request.json"
            path.write_text(json.dumps(request))
            started = time.monotonic()
            with (self.root / "stream.log").open("wb") as log:
                child = subprocess.Popen(
                    [
                        sys.executable,
                        "-u",
                        "-m",
                        "mps.typeahead_checkpoint_build",
                        str(path),
                        str(self.root / "receipt.json"),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=log,
                )
                try:
                    child.wait(timeout=5)
                    output, _ = child.communicate(timeout=1)
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.wait(timeout=2)
            self.assertLess(time.monotonic() - started, 5)
            self.assertNotEqual(child.returncode, 0)
            receipt = import_diagnostics(
                self.root, child.returncode, None, time.monotonic() - started, 1
            )
            self.assertEqual(receipt["producer_error_class"], "TimeoutError")
            self.assertEqual(receipt["cause"], "producer_deadline")
            self.assertFalse((self.root / "receipt.json").exists())
            self.assertNotIn(b'"type":"end"', output)

    def test_default_fixture_rejection_never_yields_handoff(self):
        with checkpoint_source(self.root, self.repository) as (_, database, options):
            options["allow_fixture"] = False
            with self.assertRaises(ValueError):
                build_checkpoint(
                    self.repository, self.root / "build", self.binary, database, **options
                )
            self.assertFalse((self.root / "build/stream-receipt.json").exists())

    def test_remote_policy_failure_cannot_seal_first_through(self):
        with checkpoint_source(self.root, self.repository) as (_, database, options):
            source_database = self.root / "source.db"
            with sqlite3.connect(source_database) as db:
                db.execute("DROP TABLE mod_overrides")
            with self.assertRaisesRegex(ValueError, "source handoff failed"):
                build_checkpoint(
                    self.repository, self.root / "build", self.binary, database, **options
                )
            status = json.loads(
                subprocess.check_output(
                    [str(self.binary), "status", "--data", str(self.root / "build/data")],
                    timeout=10,
                )
            )
            self.assertEqual(status["source_observation_revision"], 1)
            self.assertIsNone(status["source_cursor"]["checkpoint_handoff"]["first_remote_through"])
            metadata = json.loads(
                json.loads((self.root / "build/data/index/meta.json").read_bytes())["payload"]
            )
            self.assertIsNone(metadata.get("policy_snapshot"))

    def test_remote_clock_before_cut_never_seals_and_deadline_stops_reader(self):
        with checkpoint_source(self.root, self.repository) as (source, database, options):
            source.now -= 1
            options["timeout"] = 3
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                build_checkpoint(
                    self.repository, self.root / "build", self.binary, database, **options
                )
            self.assertLess(time.monotonic() - started, 10)
            status = json.loads(
                subprocess.check_output(
                    [str(self.binary), "status", "--data", str(self.root / "build/data")],
                    timeout=10,
                )
            )
            self.assertIsNone(status["source_cursor"]["checkpoint_handoff"]["first_remote_through"])
            self.assertGreater(status["source_observation_revision"], 1)

    def test_completed_stale_cycle_requires_another_fresh_cycle(self):
        with checkpoint_source(self.root, self.repository, clock_offset=-130) as (
            source,
            database,
            options,
        ):
            original = source.server.RequestHandlerClass

            class AdvancingClock(original):
                def source(self, body):
                    super().source(body)
                    if "unixepoch()" in body["requests"][0]["stmt"]["sql"]:
                        source.now = int(time.time())

            source.server.RequestHandlerClass = AdvancingClock
            result = build_checkpoint(
                self.repository, self.root / "build", self.binary, database, **options
            )
            self.assertGreaterEqual(result["completed_remote_cycles"], 2)
            self.assertLessEqual(result["source_gates"]["poll_age_s"], 120)


def test_actual_linux_d1_checkpoint_build():
    image = os.environ.get("TA_LINUX_IMAGE")
    if not image or os.environ.get("CHECKPOINT_BUILD_IN_CONTAINER"):
        return
    repository = Path(os.environ["TA_REPOSITORY"])
    mps = Path(__file__).resolve().parents[1]
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
        "-v",
        f"{mps}:/mps:ro",
        "-v",
        f"{repository}:/typeahead:ro",
        "-e",
        "PYTHONPATH=/mps/packages/mps/src",
        "-e",
        "TA_REPOSITORY=/typeahead",
        "-e",
        "TA_BINARY=/app/typeahead-tantivy",
        "-e",
        "CHECKPOINT_BUILD_IN_CONTAINER=1",
        image,
        "python3",
        "-c",
        "import hashlib,os,pathlib,runpy,shutil,sys,tempfile; assert hashlib.sha256(pathlib.Path('/app/typeahead-tantivy').read_bytes()).hexdigest()=='ef2be085f7ad7b2bf4ea1f0e4976ee07e37b4041a95b5f34c31c562399f17a4d'; root=pathlib.Path(tempfile.mkdtemp()); package=root/'mps';package.mkdir();(package/'__init__.py').write_text(''); [shutil.copyfile(pathlib.Path('/mps/packages/mps/src/mps')/(name+'.py'),package/(name+'.py')) for name in ['typeahead_checkpoint','typeahead_checkpoint_build','typeahead_checkpoint_stream','typeahead_sqlite_seed','typeahead_tantivy','typeahead_source_budget']];os.environ['PYTHONPATH']=str(root);sys.path.insert(0,str(root));runpy.run_path('/mps/tests/test_typeahead_checkpoint_build.py',run_name='__main__')",
    ]
    result = subprocess.run(command, capture_output=True, check=False, timeout=120)
    assert result.returncode == 0, result.stderr.decode()[-5000:]


def test_cloud_release_resolves_real_task_context_before_cloud_guard(tmp_path, monkeypatch):
    import pytest
    from mps.typeahead_checkpoint_release import run_cloud_release
    from prefect import flow, task
    from prefect.cache_policies import NO_CACHE
    from prefect.context import TaskRunContext, get_run_context
    from prefect.testing.utilities import prefect_test_harness

    monkeypatch.setenv("INDEXER_HOME", str(tmp_path))

    @task(cache_policy=NO_CACHE)
    def attempt():
        context = get_run_context()
        assert isinstance(context, TaskRunContext)
        assert context.task_run.flow_run_id is not None
        with pytest.raises(ValueError, match="Bounded cloud-only release required"):
            run_cloud_release(tmp_path)
        return str(context.task_run.flow_run_id)

    @flow
    def trial():
        return attempt()

    with prefect_test_harness():
        assert trial()


if __name__ == "__main__":
    unittest.main()
