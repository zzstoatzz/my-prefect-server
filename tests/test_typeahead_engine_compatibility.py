"""Offline controller exercise using actual HTTP and owned filesystem objects."""

import hashlib
import http.server
import importlib.util
import io
import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from mps import typeahead_refresh as refresh
from mps.typeahead_tantivy import TantivyAdapter

NATIVE = {
    "origin": "https://typeahead-startup-20261024.fly.dev",
    "build_id": "b1792908655-native-px05",
    "source_watermark": 1792908655,
    "machine_id": "native-owned",
    "volume_id": "native-volume",
}
TANTIVY = {
    "engine": "tantivy",
    "origin": refresh.TANTIVY_ORIGIN,
    "build_id": "b1793000000-engine-px05",
    "format": refresh.TANTIVY_FORMAT,
    "admission_sha256": "a" * 64,
    "binary_sha256": "b" * 64,
    "retrieval_strategy": "global-merge",
    "source_watermark": 1792950000,
}
SOURCE = "b1792950000-source-px05"


class ConditionalWriteFailed(RuntimeError):
    pass


class DiskBucket:
    def __init__(self, root):
        self.root = root
        self.before_put = None
        self.before_get = None
        self.writes = []
        self.deletions = []

    def close(self):
        pass

    def path(self, key):
        path = self.root / key
        assert path.is_relative_to(self.root) and ".." not in Path(key).parts
        return path

    def seed(self, key, raw):
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def get_object(self, *, Bucket, Key):
        if self.before_get:
            self.before_get()
        raw = self.path(Key).read_bytes()
        return {"Body": io.BytesIO(raw), "ETag": hashlib.sha256(raw).hexdigest()}

    def head_object(self, *, Bucket, Key):
        return {"ContentLength": self.path(Key).stat().st_size}

    def put_object(self, *, Bucket, Key, Body, ContentType, IfMatch):
        if self.before_put:
            action, self.before_put = self.before_put, None
            action()
        if self.get_object(Bucket=Bucket, Key=Key)["ETag"] != IfMatch:
            raise ConditionalWriteFailed("filesystem ETag changed")
        self.seed(Key, Body)
        self.writes.append(json.loads(Body))
        return {"ETag": hashlib.sha256(Body).hexdigest()}

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self

    def paginate(self, *, Bucket, Prefix):
        yield {
            "Contents": [
                {"Key": str(path.relative_to(self.root)), "Size": path.stat().st_size}
                for path in self.root.rglob("*")
                if path.is_file() and str(path.relative_to(self.root)).startswith(Prefix)
            ]
        }

    def delete_objects(self, *, Bucket, Delete):
        for item in Delete["Objects"]:
            self.path(item["Key"]).unlink()
            self.deletions.append(item["Key"])
        return {}


class FixtureRefresh(refresh.Refresh):
    def get(self, base, path):
        assert base in {TANTIVY["origin"], refresh.PUBLIC}, "no other backend may be read"
        role = "public" if base == refresh.PUBLIC else "tantivy"
        return super().get(self.base, "/" + role + path)

    def fly(self, *args, **kwargs):
        raise AssertionError("No machine/volume request is allowed by this offline fixture")


@pytest.fixture
def controller(tmp_path):
    service = FixtureRefresh.__new__(FixtureRefresh)
    service.logger = logging.getLogger(__name__)
    service.http = httpx.Client(timeout=3)
    service.bucket = "fixture"
    service.s3 = DiskBucket(tmp_path / "bucket")
    service.s3.seed(
        refresh.ROUTE_KEY,
        json.dumps({"version": 1, "current": TANTIVY}).encode(),
    )
    service.public_fail = False
    service.public_failures = 0
    service.public_calls = 0
    service.public_stale = False
    service.public_race = None
    service.bad_header = None
    service.bad_index = None
    service.source_unknown = False

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            role, path = parsed.path[1:].split("/", 1)
            headers = {}
            status = 200
            if role == "public":
                service.public_calls += 1
                if service.public_race:
                    action, service.public_race = service.public_race, None
                    action()
                target = json.loads(service.s3.path(refresh.ROUTE_KEY).read_bytes())["current"]
                body = {
                    "ok": True,
                    "probe_ok": True,
                    "build_id": "wrong" if service.public_fail else target["build_id"],
                    "stale": service.public_stale,
                }
                if service.public_failures:
                    service.public_failures -= 1
                    status, body = 503, {"ok": False, "probe_ok": False, "stale": True}
                elif service.public_stale:
                    status = 503
            elif path == "health":
                body = {
                    "ready": True,
                    "sync_lag_s": 0,
                    "build_id": TANTIVY["build_id"],
                    "engine": "tantivy",
                }
            elif path == "index/status":
                body = {
                    **TANTIVY,
                    "ready": True,
                    "source_build_id": None if service.source_unknown else SOURCE,
                    "snapshot_watermark": 1792950000,
                    "source_manifest": {"build_id": SOURCE, "source_watermark": 1792950000},
                    "full_source": True,
                    "import_complete": True,
                }
                if service.bad_index:
                    body[service.bad_index] = "wrong"
            else:
                query = parse_qs(parsed.query)
                pds = query.get("pds", [None])[0]
                if pds == "rhizopogon.us-west.host.bsky.network":
                    status, body = 503, {"error": "PdsScopeUnavailable"}
                else:
                    body = {
                        "actors": []
                        if pds == "unknown.invalid"
                        else [{"did": "did:fixture:one", "handle": "zzstoatzz.io"}]
                    }
                    if pds:
                        headers["X-Typeahead-Pds"] = pds.removeprefix("https://")
            if role == "tantivy":
                headers.update(
                    {
                        "X-Typeahead-Engine": "tantivy",
                        "X-Typeahead-Build": TANTIVY["build_id"],
                        "X-Typeahead-Format": TANTIVY["format"],
                        "X-Typeahead-Binary-Sha256": TANTIVY["binary_sha256"],
                        "X-Typeahead-Retrieval-Strategy": TANTIVY["retrieval_strategy"],
                    }
                )
                if service.bad_header:
                    headers[service.bad_header] = "wrong"
            raw = json.dumps(body).encode()
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_):
            pass

    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        service.base = f"http://127.0.0.1:{server.server_port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield service
        finally:
            service.http.close()
            server.shutdown()
            thread.join()


def test_route_schema_accepts_only_tantivy_targets():
    assert refresh.route_target({"version": 1, "current": TANTIVY}) == TANTIVY
    for app in refresh.TANTIVY_SLOTS:
        target = {**TANTIVY, "origin": f"https://{app}.fly.dev"}
        assert refresh.route_target({"version": 1, "current": target}) == target
    for current in [NATIVE, {**NATIVE, "engine": "native"}]:
        with pytest.raises(ValueError, match="Tantivy route identity"):
            refresh.route_target({"version": 1, "current": current})
    for delta in [
        {"engine": "unknown"},
        {"engine": None},
        {"format": "FLATPX05"},
        {"binary_sha256": "wrong"},
        {"admission_sha256": ""},
        {"retrieval_strategy": "other"},
        {"origin": NATIVE["origin"]},
    ]:
        with pytest.raises(ValueError):
            refresh.route_target({"version": 1, "current": TANTIVY | delta})
    with pytest.raises(ValueError):
        refresh.route_target({"version": True, "current": TANTIVY})


def test_route_records_reject_native_targets_and_fields():
    refresh.validate_route_record({"version": 1, "current": TANTIVY, "previous": PREVIOUS})
    for record in [
        {"version": 1, "current": TANTIVY, "previous": NATIVE},
        {"version": 1, "current": TANTIVY, "native_rollback": NATIVE},
        {"version": 1, "current": TANTIVY, "rollback": True},
    ]:
        with pytest.raises(ValueError):
            refresh.validate_route_record(record)


def test_legacy_route_record_drops_native_entries_on_read(controller):
    legacy = {
        "version": 1,
        "current": TANTIVY,
        "previous": NATIVE,
        "native_rollback": NATIVE,
        "promoted_at": 1792950000,
        "rollback": True,
    }
    controller.s3.seed(refresh.ROUTE_KEY, json.dumps(legacy).encode())
    assert controller.route()[0] == {"version": 1, "current": TANTIVY}
    legacy["previous"] = PREVIOUS
    controller.s3.seed(refresh.ROUTE_KEY, json.dumps(legacy).encode())
    route, etag = controller.route()
    assert route == {"version": 1, "current": TANTIVY, "previous": PREVIOUS}
    controller._put_route(dict(route), etag)
    written = json.loads(controller.s3.path(refresh.ROUTE_KEY).read_bytes())
    assert written == {"version": 1, "current": TANTIVY, "previous": PREVIOUS}
    controller.s3.seed(refresh.ROUTE_KEY, json.dumps({**legacy, "current": NATIVE}).encode())
    with pytest.raises(ValueError, match="Tantivy route identity"):
        controller.route()


def test_real_http_tantivy_identity_rejects_wrong_body_or_transport(controller):
    assert controller.healthy(TANTIVY)["index"]["source_build_id"] == SOURCE
    for header in [
        "X-Typeahead-Engine",
        "X-Typeahead-Build",
        "X-Typeahead-Format",
        "X-Typeahead-Binary-Sha256",
        "X-Typeahead-Retrieval-Strategy",
    ]:
        controller.bad_header = header
        with pytest.raises(RuntimeError, match="identity mismatch"):
            controller.healthy(TANTIVY)
    controller.bad_header = None
    for field in ["engine", "build_id", "format", "binary_sha256", "retrieval_strategy"]:
        controller.bad_index = field
        with pytest.raises(RuntimeError):
            controller.healthy(TANTIVY)


class StubAdapter:
    """Accepts any manifest; the pinned installer's checks have their own tests."""

    def validate_manifest(self, manifest):
        return manifest

    def validate_source(self, manifest):
        return None


PREVIOUS = {**TANTIVY, "build_id": "b1792999999-previous-engine"}
LEGACY_PROVENANCE = {
    "legacy_build_id": "b1792908655-legacy",
    "rollback_native_anchor": {"build_id": "b1792908655-native-px05", "manifest_sha256": "9" * 64},
}


def seed_stub_publication(controller, target, *, payload=True, legacy=False):
    """Publish a checkpoint-shaped artifact; `legacy` adds retired provenance fields."""
    manifest = {
        "engine": "tantivy",
        "active_build": target["build_id"],
        "backend_format": target["format"],
        "binary_sha256": target["binary_sha256"],
        "retrieval_strategy": target["retrieval_strategy"],
        "source_kind": "sqlite-committed-prefix",
        "source_build_id": target["build_id"],
        "source_actor_count": 4,
        "object_key": f"releases/search-tantivy/{target['build_id']}/index.tar",
    }
    if legacy:
        manifest.update(LEGACY_PROVENANCE)
    controller.tantivy_adapter = StubAdapter
    raw = json.dumps(manifest).encode()
    controller.s3.seed(f"releases/search-tantivy/{target['build_id']}/manifest.json", raw)
    if payload:
        controller.s3.seed(manifest["object_key"], b"bundle")
    return manifest, raw


def bucket_keys(controller, prefix):
    return sorted(
        str(path.relative_to(controller.s3.root))
        for path in (controller.s3.root / prefix).rglob("*")
        if path.is_file()
    )


@pytest.mark.parametrize("legacy", [False, True])
def test_prune_deletes_only_unrouted_tantivy_generations(controller, legacy):
    route = {"version": 1, "current": TANTIVY, "previous": PREVIOUS}
    controller.s3.seed(refresh.ROUTE_KEY, json.dumps(route).encode())
    seed_stub_publication(controller, TANTIVY, legacy=legacy)
    seed_stub_publication(controller, PREVIOUS, legacy=legacy)
    seed_stub_publication(controller, {**TANTIVY, "build_id": "b1792800000-disposable"})
    assert bucket_keys(controller, "releases/search") == []
    assert controller.retained_build_ids() == {TANTIVY["build_id"], PREVIOUS["build_id"]}
    report = controller.prune_artifacts()
    assert report["deleted_objects"] == 2
    assert sorted(controller.s3.deletions) == [
        "releases/search-tantivy/b1792800000-disposable/index.tar",
        "releases/search-tantivy/b1792800000-disposable/manifest.json",
    ]


def test_prune_leaves_retired_native_objects_to_the_one_off_cleanup(controller):
    seed_stub_publication(controller, TANTIVY)
    retired = [
        "releases/search/b1792908655-native-px05/index.db",
        "releases/search/b1792908655-native-px05/manifest.json",
    ]
    for key in retired:
        controller.s3.seed(key, b"retired")
    assert controller.prune_artifacts()["deleted_objects"] == 0
    assert bucket_keys(controller, "releases/search") == retired


def test_missing_routed_publication_blocks_all_artifact_cleanup(controller):
    route = {"version": 1, "current": TANTIVY, "previous": PREVIOUS}
    controller.s3.seed(refresh.ROUTE_KEY, json.dumps(route).encode())
    seed_stub_publication(controller, TANTIVY)
    seed_stub_publication(controller, {**TANTIVY, "build_id": "b1792800000-disposable"})
    with pytest.raises(RuntimeError, match="publication is missing"):
        controller.prune_artifacts()
    assert controller.s3.deletions == []


@pytest.fixture
def pinned_adapter(monkeypatch):
    repository = os.environ.get("TA_REPOSITORY")
    if not repository:
        pytest.skip("requires the actual pinned Typeahead installer checkout")
    script = Path(repository) / "scripts/snapshot_refresh/tantivy_install.py"
    sha = os.environ.get("TA_ADAPTER_SHA256") or hashlib.sha256(script.read_bytes()).hexdigest()
    monkeypatch.setenv("INDEX_TANTIVY_ADAPTER_PATH", str(script))
    monkeypatch.setenv("INDEX_TANTIVY_ADAPTER_SHA256", sha)
    return TantivyAdapter(script, sha)


def seed_tantivy_publication(controller, target, source_build):
    manifest = {
        "engine": "tantivy",
        "manifest_version": 1,
        "artifact_format": "tantivy-directory-tar-v1",
        "backend_format": refresh.TANTIVY_FORMAT,
        "active_build": target["build_id"],
        "normalize_version": "v2-cjk-bigram",
        "plan_version": "complete-prefix-groups-v1",
        "source_policy": "turso-authoritative-observations-v2",
        "sha256": "a" * 64,
        "binary_sha256": target["binary_sha256"],
        "canonical_rules_source_sha256": "c" * 64,
        "retrieval_strategy": target["retrieval_strategy"],
        "byte_size": 10240,
        "source_actor_count": 1,
        "source_build_id": source_build,
        "source_manifest_sha256": "1" * 64,
        "source_watermark": 1792950000,
        "source_database_sha256": "2" * 64,
        "source_prefix_sha256": "3" * 64,
        "source_actors_sha256": "f" * 64,
        "object_key": f"releases/search-tantivy/{target['build_id']}/index.tar",
        "files": [{"path": "index/meta.json", "byte_size": 1, "sha256": "a" * 64}],
    }
    controller.s3.seed(
        f"releases/search-tantivy/{target['build_id']}/manifest.json", json.dumps(manifest).encode()
    )
    return manifest


def seed_checkpoint_publication(controller, target, *, legacy=False):
    """Publish the pinned installer's checkpoint fixture; `legacy` adds retired fields."""
    repository = Path(os.environ["TA_REPOSITORY"])
    spec = importlib.util.spec_from_file_location(
        "typeahead_checkpoint_retention_fixture",
        repository / "scripts/snapshot_refresh/test_tantivy_install.py",
    )
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    reference = fixtures.AdmissionTests()
    manifest, _, native_raw = reference.checkpoint_manifest()
    for field in (
        "provider_app",
        "image_digest",
        "guest",
        "volume_id",
        "volume_size_gib",
        "public_service_ports",
    ):
        manifest.pop(field, None)
    manifest.update(
        active_build=target["build_id"],
        binary_sha256=target["binary_sha256"],
        retrieval_strategy=target["retrieval_strategy"],
        object_key=f"releases/search-tantivy/{target['build_id']}/index.tar",
    )
    if legacy:
        manifest.update(reference.legacy_native_fields(native_raw))
    controller.s3.seed(manifest["object_key"], b"x" * manifest["byte_size"])
    controller.s3.seed(
        f"releases/search-tantivy/{manifest['active_build']}/manifest.json",
        json.dumps(manifest).encode(),
    )
    return manifest


@pytest.mark.parametrize("legacy", [False, True])
def test_checkpoint_publication_is_retained_and_pruned_without_native_objects(
    controller, pinned_adapter, legacy
):
    controller.tantivy_adapter = lambda: pinned_adapter
    manifest = seed_checkpoint_publication(controller, TANTIVY, legacy=legacy)
    assert ("rollback_native_anchor" in manifest) is legacy
    assert ("legacy_build_id" in manifest) is legacy
    assert bucket_keys(controller, "releases/search") == []
    assert controller.tantivy_publication(TANTIVY) == manifest
    assert controller.retained_build_ids() == {manifest["active_build"]}
    controller.s3.seed("releases/search-tantivy/b1791000000-disposable/index.tar", b"discard")
    assert controller.prune_artifacts()["deleted_objects"] == 1
    assert controller.s3.deletions == ["releases/search-tantivy/b1791000000-disposable/index.tar"]


def test_checkpoint_receipt_tamper_blocks_retention_before_deletion(controller, pinned_adapter):
    controller.tantivy_adapter = lambda: pinned_adapter
    manifest = seed_checkpoint_publication(controller, TANTIVY)
    manifest["source_bootstrap"]["checkpoint_receipt_json"] += " "
    controller.s3.seed(
        f"releases/search-tantivy/{manifest['active_build']}/manifest.json",
        json.dumps(manifest).encode(),
    )
    controller.s3.seed("releases/search-tantivy/b1791000000-disposable/index.tar", b"discard")
    with pytest.raises(ValueError, match="checkpoint receipt"):
        controller.prune_artifacts()
    assert controller.s3.deletions == []


def test_portable_retention_resolves_two_tantivy_generations_without_old_endpoint(
    controller, pinned_adapter
):
    route = {"version": 1, "current": TANTIVY, "previous": PREVIOUS}
    controller.s3.seed(refresh.ROUTE_KEY, json.dumps(route).encode())
    current = seed_tantivy_publication(controller, route["current"], SOURCE)
    previous = seed_tantivy_publication(
        controller, route["previous"], "b1792940000-previous-source-px05"
    )
    controller.source_unknown = True
    assert controller.retained_build_ids() == {current["active_build"], previous["active_build"]}
    for entry in (route["current"], route["previous"]):
        controller.s3.seed(f"releases/search-tantivy/{entry['build_id']}/index.tar", b"retained")
    controller.s3.seed("releases/search-tantivy/b1792000000-disposable/index.tar", b"disposable")
    assert controller.prune_artifacts()["deleted_objects"] == 1
    assert all(
        controller.s3.path(f"releases/search-tantivy/{entry['build_id']}/index.tar").exists()
        for entry in (route["current"], route["previous"])
    )
    assert bucket_keys(controller, "releases/search") == []


def test_installer_checksum_mismatch_is_rejected_before_source_execution(tmp_path, pinned_adapter):
    marker = tmp_path / "executed"
    script = tmp_path / "changed.py"
    script.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    with pytest.raises(ValueError, match="checksum mismatch"):
        TantivyAdapter(script, pinned_adapter.source_sha256)
    assert not marker.exists()


@pytest.mark.parametrize("automatic", [False, True])
def test_actual_bundle_install_restart_and_http_qualification_through_adapter(
    tmp_path, pinned_adapter, monkeypatch, controller, automatic
):
    repository = Path(os.environ["TA_REPOSITORY"])
    binary = Path(
        os.environ.get(
            "TA_BINARY", repository / "services/tantivy/target/release/typeahead-tantivy"
        )
    )
    if not binary.is_file():
        pytest.skip("requires the actual prebuilt Rust backend; no build performed")
    spec = importlib.util.spec_from_file_location(
        "typeahead_admission_fixtures",
        repository / "scripts/snapshot_refresh/test_tantivy_install.py",
    )
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    reference = fixtures.AdmissionTests()
    manifest = reference.manifest()
    actor = fixtures.smoke.actor(1, "real.test", "Real Actor")
    lines = json.dumps(actor, separators=(",", ":")).encode() + b"\n"
    manifest["source_actors_sha256"] = hashlib.sha256(lines).hexdigest()
    header = {
        "type": "manifest",
        "build_id": manifest["source_build_id"],
        "source_watermark": 100,
        "actor_count": 1,
        "source_actor_count": 1,
        "partial": False,
        "normalize_version": manifest["normalize_version"],
        "source_database_sha256": manifest["source_database_sha256"],
        "source_prefix_sha256": manifest["source_prefix_sha256"],
    }
    seed = tmp_path / "seed.jsonl"
    seed.write_bytes(
        json.dumps(header).encode()
        + b"\n"
        + lines
        + json.dumps(
            {
                "type": "end",
                "build_id": header["build_id"],
                "actor_count": 1,
                "actors_sha256": manifest["source_actors_sha256"],
            }
        ).encode()
        + b"\n"
    )
    data = tmp_path / "original"
    subprocess.run(
        [str(binary), "import", "--data", str(data), "--input", str(seed)],
        check=True,
        capture_output=True,
        timeout=30,
    )
    secret = "local-companion-adapter-fixture"
    now = int(time.time())
    with (tmp_path / "server.log").open("wb") as log:
        child, public, admin = fixtures.start_strict(binary, data, log, secret)
        try:
            state = fixtures.smoke.request(admin, "/admin/source-state", {"dids": []}, secret)[2]
            cursor = reference.status()["source_cursor"]
            cursor.update(
                through=now,
                actors={"updated_at": now, "did": ""},
                tombstones={"deleted_at": now, "did": ""},
                lookback_seconds=300,
            )
            cursor["reconciliation"].update(
                last_completed_at=now - 1, last_completed_started_at=now - 10
            )
            body = {
                "actors": [],
                "expected_documents": [],
                "expected_source_cursor": state["source_cursor"],
                "source_cursor": cursor,
                "expected_observation_revision": state["source_observation_revision"],
                "batch_id": hashlib.sha256(b"mps-adapter-fixture").hexdigest(),
                "watermark": now,
                "policy_snapshot": {
                    "schema_version": 1,
                    "source_observed_at": now,
                    "complete": True,
                    "overrides": [],
                    "blocked_domains": [],
                },
            }
            assert fixtures.smoke.request(admin, "/admin/source-reconcile", body, secret)[0] == 200
        finally:
            fixtures.smoke.stop(child)
    manifest["binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
    published = tmp_path / "publication"
    portable = pinned_adapter.freeze(data, published, manifest, binary)
    assert "volume_id" not in portable and "provider_app" not in portable
    bundle = published / "index.tar"
    manifest.update(portable)
    destination = tmp_path / "installed"
    receipt = pinned_adapter.install(bundle, destination, manifest, binary)
    assert receipt["passed"] and receipt["runtime_qualified"] is False
    with (tmp_path / "restart.log").open("wb") as log:
        child, public, _ = fixtures.start_strict(
            binary, destination, log, secret, fixture_identity=True
        )
        try:
            qualified = pinned_adapter.qualify(
                public,
                manifest,
                receipt,
                reference.provider(int(time.time())),
                allow_loopback=True,
            )
            assert qualified["admission"]["test_only"] is True
            assert qualified["admission"]["runtime_http_verified"] is False
            assert qualified["admission"]["provider_runtime_instance_bound"] is True
            assert (
                fixtures.smoke.request(public, "/search?q=real")[2]["actors"][0]["did"]
                == actor["did"]
            )
            bad = {**receipt, "manifest_sha256": "0" * 64}
            with pytest.raises(ValueError, match="immutable installation receipt"):
                pinned_adapter.qualify(
                    public,
                    manifest,
                    bad,
                    reference.provider(int(time.time())),
                    allow_loopback=True,
                )
        finally:
            fixtures.smoke.stop(child)
    assert (destination / "index/meta.json").is_file()
    assert not (destination / "index.db").exists()
    from tests.test_typeahead_tantivy_slots import exercise_real_rust_preparation

    exercise_real_rust_preparation(
        controller,
        tmp_path / "slot-lifecycle",
        pinned_adapter,
        portable,
        bundle,
        binary,
        fixtures,
        automatic=automatic,
    )


def test_automatic_flow_fences_before_any_build_or_cleanup(controller, monkeypatch):
    import flows.typeahead_index as publisher

    def forbidden(*args, **kwargs):
        raise AssertionError("Corpus preparation or cleanup must not start without release proof")

    monkeypatch.setattr(refresh, "Refresh", lambda logger: controller)
    monkeypatch.setattr(publisher, "get_run_logger", lambda: controller.logger)
    monkeypatch.delenv("INDEX_TANTIVY_RELEASE_RECEIPT_SHA256", raising=False)
    for operation in [
        "clone_repo",
        "prepare_checkpoint_generation",
        "publish_tantivy_generation",
        "retire_tantivy_generation",
    ]:
        monkeypatch.setattr(publisher, operation, forbidden)
    with pytest.raises(ValueError, match="immutable source and release proof"):
        publisher.typeahead_index.fn(automatic_refresh=True)
    assert controller.s3.writes == []
    assert controller.s3.deletions == []


def test_retirement_waits_on_the_pinned_adapters_poll_cycle_message(pinned_adapter):
    from mps.typeahead_tantivy_slots import POLL_CYCLE_INCOMPLETE

    source = Path(os.environ["INDEX_TANTIVY_ADAPTER_PATH"]).read_text()
    assert f"raise ValueError('{POLL_CYCLE_INCOMPLETE}')" in source
