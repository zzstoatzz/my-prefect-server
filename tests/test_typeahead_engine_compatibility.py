"""Offline controller exercise using actual HTTP and owned filesystem objects."""

import hashlib
import http.server
import importlib.util
import io
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from mps import typeahead_refresh as refresh
from mps.typeahead_tantivy import TantivyAdapter, prepare_generation

from flows.typeahead_index import builds_to_prune, prune_builds

NATIVE = {
    "origin": refresh.origin(refresh.SLOTS[0]),
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
        assert base in {NATIVE["origin"], TANTIVY["origin"], refresh.PUBLIC}
        role = (
            "public"
            if base == refresh.PUBLIC
            else "tantivy"
            if base == TANTIVY["origin"]
            else "native"
        )
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
        json.dumps({"version": 1, "current": TANTIVY, "previous": NATIVE}).encode(),
    )
    service.public_fail = False
    service.public_race = None
    service.bad_header = None
    service.bad_index = None
    service.source_unknown = False
    service.image = "fixture-never-installed"

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            role, path = parsed.path[1:].split("/", 1)
            headers = {}
            status = 200
            if role == "public":
                if service.public_race:
                    action, service.public_race = service.public_race, None
                    action()
                target = json.loads(service.s3.path(refresh.ROUTE_KEY).read_bytes())["current"]
                body = {
                    "ok": True,
                    "probe_ok": True,
                    "build_id": "wrong" if service.public_fail else target["build_id"],
                    "stale": False,
                }
            elif path == "health":
                target = TANTIVY if role == "tantivy" else NATIVE
                body = {"ready": True, "sync_lag_s": 0, "build_id": target["build_id"]}
                if role == "tantivy":
                    body["engine"] = "tantivy"
            elif path == "index/status":
                target = TANTIVY if role == "tantivy" else NATIVE
                body = {**target, "snapshot_format": "FLATPX05", "ready": True}
                if role == "tantivy":
                    body.update(
                        source_build_id=None if service.source_unknown else SOURCE,
                        snapshot_watermark=1792950000,
                        source_manifest={"build_id": SOURCE, "source_watermark": 1792950000},
                        full_source=True,
                        import_complete=True,
                    )
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


def test_route_union_matches_worker_and_rejects_allowlist_only_tantivy():
    assert refresh.route_target({"version": 1, "current": NATIVE}) == NATIVE
    assert refresh.route_target({"version": 1, "current": TANTIVY}) == TANTIVY
    for app in refresh.TANTIVY_SLOTS:
        target = {**TANTIVY, "origin": f"https://{app}.fly.dev"}
        assert refresh.route_target({"version": 1, "current": target}) == target
    assert (
        refresh.route_target({"version": 1, "current": {**NATIVE, "engine": "native"}})["engine"]
        == "native"
    )
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
        refresh.route_target({"version": True, "current": NATIVE})


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


def test_tantivy_serving_fences_all_native_install_entrypoints_before_mutation(
    controller, tmp_path
):
    manifest = {
        "manifest_version": 2,
        "format_version": 5,
        "source_watermark": int(__import__("time").time()),
        "active_build": TANTIVY["build_id"],
    }
    generation = tmp_path / "generation"
    generation.mkdir()
    (generation / "manifest.json").write_text(json.dumps(manifest))
    controller.s3.seed("control/builds/latest.json", json.dumps(manifest).encode())
    route, etag = controller.route()
    for action in [
        lambda: controller.apply(generation),
        lambda: controller.resume_published(),
        lambda: controller.release_is_current(TANTIVY, manifest),
        lambda: controller.discard_inactive(refresh.SLOTS[0], route, etag),
        lambda: controller.serving_guard(route, etag, refresh.SLOTS[0]),
        lambda: controller.replace_route(route, etag),
    ]:
        with pytest.raises(RuntimeError, match="installation is fenced"):
            action()
    assert controller.s3.writes == []
    assert controller.s3.deletions == []


def test_real_http_and_disk_native_rollback_from_tantivy(controller):
    prior, _ = controller.route()
    result = controller.rollback_to_native()
    assert result["passed"] and result["machine_or_volume_changed"] is False
    assert controller.route()[0]["current"] == NATIVE
    assert controller.route()[0]["previous"] == prior["current"]
    assert len(controller.s3.writes) == 1


def test_failed_native_rollback_restores_exact_prior_tantivy_route(controller):
    prior, _ = controller.route()
    controller.public_fail = True
    with pytest.raises(RuntimeError, match="public verification failed"):
        controller.rollback_to_native()
    assert controller.route()[0] == prior
    assert len(controller.s3.writes) == 2


def test_rollback_compare_and_swap_race_never_clobbers_new_route(controller):
    prior, _ = controller.route()
    raced = {**prior, "operator_race": True}
    controller.s3.before_put = lambda: controller.s3.seed(
        refresh.ROUTE_KEY, json.dumps(raced).encode()
    )
    with pytest.raises(ConditionalWriteFailed):
        controller.rollback_to_native()
    assert controller.route()[0] == raced
    assert controller.s3.writes == []


def test_failed_rollback_does_not_overwrite_concurrent_operator_route(controller):
    prior, _ = controller.route()
    raced = {**prior, "operator_race": "after-promotion"}
    controller.public_fail = True
    controller.public_race = lambda: controller.s3.seed(
        refresh.ROUTE_KEY, json.dumps(raced).encode()
    )
    with pytest.raises(RuntimeError, match="public verification failed"):
        controller.rollback_to_native()
    assert controller.route()[0] == raced
    assert len(controller.s3.writes) == 1


def test_native_to_native_rollback_preserves_existing_behavior(controller):
    other = NATIVE | {
        "origin": refresh.origin(refresh.SLOTS[1]),
        "build_id": "b1793000000-native-px05",
    }
    controller.s3.seed(
        refresh.ROUTE_KEY, json.dumps({"version": 1, "current": other, "previous": NATIVE}).encode()
    )
    assert controller.rollback_to_native()["passed"]
    assert controller.route()[0]["current"] == NATIVE


def test_retention_preserves_both_source_generations_and_twins_on_disk(
    controller, tmp_path, monkeypatch
):
    route, _ = controller.route()
    protected = controller.retained_build_ids(route)
    assert {
        NATIVE["build_id"],
        NATIVE["build_id"].removesuffix("-px05"),
        TANTIVY["build_id"],
        SOURCE,
        SOURCE.removesuffix("-px05"),
    } <= protected
    for bid in [*protected, "b1792800000-disposable"]:
        for file in ["index.db", "index.db.prefix", "manifest.json"]:
            controller.s3.seed(f"releases/search/{bid}/{file}", b"fixture")
    assert controller.prune_artifacts()["deleted_objects"] == 3
    assert all(controller.s3.path(f"releases/search/{bid}/index.db").exists() for bid in protected)
    archive = tmp_path / "archive"
    for bid in [*protected, "b1792000000-disposable", "b1793100000-new", "b1793200000-new"]:
        path = archive / ("build-" + bid)
        path.mkdir(parents=True)
        (path / "index.db").write_bytes(b"owned-fixture")
    monkeypatch.setattr(
        refresh, "protected_source_build_ids", lambda logger: controller.retained_build_ids()
    )
    prune_builds.fn(str(archive))
    assert all((archive / ("build-" + bid)).exists() for bid in protected)
    assert not (archive / "build-b1792000000-disposable").exists()
    assert builds_to_prune(list(archive.iterdir()), set()) == []


def test_unknown_source_blocks_all_artifact_and_local_cleanup(controller, tmp_path, monkeypatch):
    controller.source_unknown = True
    with pytest.raises(RuntimeError, match="source identity unknown"):
        controller.prune_artifacts()
    assert controller.s3.deletions == []
    for i in range(5):
        (tmp_path / ("build-b1792000000-" + str(i))).mkdir()
    monkeypatch.setattr(
        refresh, "protected_source_build_ids", lambda logger: controller.retained_build_ids()
    )
    with pytest.raises(RuntimeError, match="retention blocked"):
        prune_builds.fn(str(tmp_path))
    assert len(list(tmp_path.glob("build-*"))) == 5


def test_compensating_rollback_cas_race_never_overwrites_operator(controller):
    prior, _ = controller.route()
    raced = {**prior, "operator_race": "compensating-cas"}
    controller.public_fail = True

    def arm_race():
        controller.s3.before_put = lambda: controller.s3.seed(
            refresh.ROUTE_KEY, json.dumps(raced).encode()
        )

    controller.public_race = arm_race
    with pytest.raises(ConditionalWriteFailed):
        controller.rollback_to_native()
    assert controller.route()[0] == raced
    assert len(controller.s3.writes) == 1


def test_rollback_cannot_promote_tantivy_as_previous(controller):
    controller.s3.seed(
        refresh.ROUTE_KEY,
        json.dumps({"version": 1, "current": NATIVE, "previous": TANTIVY}).encode(),
    )
    with pytest.raises(RuntimeError, match="installation is fenced"):
        controller.rollback_to_native()
    assert controller.s3.writes == []


def anchored_route():
    return {
        "version": 1,
        "current": TANTIVY,
        "previous": {**TANTIVY, "build_id": "b1792999999-previous-engine-px05"},
        "native_rollback": NATIVE,
    }


def test_native_anchor_survives_successive_tantivy_route_and_rollback(controller):
    route = anchored_route()
    controller.s3.seed(refresh.ROUTE_KEY, json.dumps(route).encode())
    result = controller.rollback_to_native()
    assert result["passed"] and result["current"] == NATIVE
    actual, _ = controller.route()
    assert actual["native_rollback"] == NATIVE
    assert actual["previous"] == TANTIVY
    assert len(controller.s3.writes) == 1


def test_retention_protects_native_anchor_even_without_previous_native(controller):
    route = {**anchored_route(), "previous": TANTIVY}
    controller.s3.seed(refresh.ROUTE_KEY, json.dumps(route).encode())
    protected = controller.retained_build_ids()
    assert {NATIVE["build_id"], NATIVE["build_id"].removesuffix("-px05")} <= protected
    for bid in [*protected, "b1792800000-disposable"]:
        controller.s3.seed(f"releases/search/{bid}/index.db", b"fixture")
    assert controller.prune_artifacts()["deleted_objects"] == 1
    assert controller.s3.path(f"releases/search/{NATIVE['build_id']}/index.db").exists()


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
    native = {
        "manifest_version": 2,
        "format_version": 5,
        "active_build": source_build,
        "source_watermark": 1792950000,
        "actor_count": 1,
        "normalize_version": "v2-cjk-bigram",
        "sha256": "2" * 64,
        "prefix_map": {"sha256": "3" * 64},
        "legacy_twin": {
            "build_id": source_build.removesuffix("-px05"),
            "source_watermark": 1792950000,
        },
    }
    raw = json.dumps(native).encode()
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
        "legacy_build_id": native["legacy_twin"]["build_id"],
        "source_manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "source_watermark": native["source_watermark"],
        "source_database_sha256": native["sha256"],
        "source_prefix_sha256": native["prefix_map"]["sha256"],
        "source_actors_sha256": "f" * 64,
        "object_key": f"releases/search-tantivy/{target['build_id']}/index.tar",
        "files": [{"path": "index/meta.json", "byte_size": 1, "sha256": "a" * 64}],
    }
    controller.s3.seed(f"releases/search/{source_build}/manifest.json", raw)
    controller.s3.seed(
        f"releases/search-tantivy/{target['build_id']}/manifest.json", json.dumps(manifest).encode()
    )
    return manifest


def seed_checkpoint_publication(controller, target):
    repository = Path(os.environ["TA_REPOSITORY"])
    spec = importlib.util.spec_from_file_location(
        "typeahead_checkpoint_retention_fixture",
        repository / "scripts/snapshot_refresh/test_tantivy_install.py",
    )
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    manifest, _, native_raw = fixtures.AdmissionTests().checkpoint_manifest()
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
    native = json.loads(native_raw)
    native_build = native["active_build"]
    native.update(snapshot_url=f"releases/search/{native_build}/index.db", byte_size=16)
    native["prefix_map"].update(object_key=native["snapshot_url"] + ".prefix", byte_size=8)
    twin = {
        "active_build": manifest["legacy_build_id"],
        "source_watermark": native["source_watermark"],
        "snapshot_url": f"releases/search/{manifest['legacy_build_id']}/index.db",
        "byte_size": 4,
    }
    twin_raw = json.dumps(twin).encode()
    twin_key = f"releases/search/{twin['active_build']}/manifest.json"
    native["legacy_twin"]["manifest"] = {
        "object_key": twin_key,
        "byte_size": len(twin_raw),
        "sha256": hashlib.sha256(twin_raw).hexdigest(),
    }
    native_raw = json.dumps(native).encode()
    manifest["rollback_native_anchor"]["manifest_sha256"] = hashlib.sha256(native_raw).hexdigest()
    controller.s3.seed(f"releases/search/{native_build}/manifest.json", native_raw)
    controller.s3.seed(twin_key, twin_raw)
    for key, size in [
        (manifest["object_key"], manifest["byte_size"]),
        (native["snapshot_url"], native["byte_size"]),
        (native["prefix_map"]["object_key"], native["prefix_map"]["byte_size"]),
        (twin["snapshot_url"], twin["byte_size"]),
    ]:
        controller.s3.seed(key, b"x" * size)
    controller.s3.seed(
        f"releases/search-tantivy/{manifest['active_build']}/manifest.json",
        json.dumps(manifest).encode(),
    )
    return manifest, native_raw


def test_checkpoint_retention_preserves_independent_native_anchor_without_primary_native_artifact(
    controller, pinned_adapter
):
    controller.tantivy_adapter = lambda: pinned_adapter
    manifest, native_raw = seed_checkpoint_publication(controller, TANTIVY)
    native_build = manifest["rollback_native_anchor"]["build_id"]
    assert controller.tantivy_source_manifest(manifest) == native_raw
    assert not controller.s3.path(
        f"releases/search/{manifest['source_build_id']}/manifest.json"
    ).exists()
    retained = controller.retained_build_ids()
    assert {
        manifest["active_build"],
        manifest["source_build_id"],
        native_build,
        manifest["legacy_build_id"],
    } <= retained
    disposable = "releases/search/b1791000000-disposable/index.db"
    controller.s3.seed(disposable, b"discard")
    assert controller.prune_artifacts()["deleted_objects"] == 1
    assert controller.s3.path(f"releases/search/{native_build}/index.db").is_file()
    assert not controller.s3.path(disposable).exists()
    anchor_key = f"releases/search/{native_build}/manifest.json"
    controller.s3.seed(anchor_key, native_raw + b" ")
    with pytest.raises(ValueError, match="rollback-native manifest bytes"):
        controller.retained_build_ids()


def test_portable_retention_resolves_two_tantivy_generations_without_old_endpoint(
    controller, pinned_adapter
):
    route = anchored_route()
    controller.s3.seed(refresh.ROUTE_KEY, json.dumps(route).encode())
    current = seed_tantivy_publication(controller, route["current"], SOURCE)
    previous = seed_tantivy_publication(
        controller, route["previous"], "b1792940000-previous-source-px05"
    )
    controller.source_unknown = True
    retained = controller.retained_build_ids()
    assert {
        NATIVE["build_id"],
        current["active_build"],
        current["source_build_id"],
        current["legacy_build_id"],
        previous["active_build"],
        previous["source_build_id"],
        previous["legacy_build_id"],
    } <= retained
    for entry in (route["current"], route["previous"]):
        controller.s3.seed(f"releases/search-tantivy/{entry['build_id']}/index.tar", b"retained")
    controller.s3.seed("releases/search-tantivy/b1792000000-disposable/index.tar", b"disposable")
    assert controller.prune_artifacts()["deleted_objects"] == 1
    assert all(
        controller.s3.path(f"releases/search-tantivy/{entry['build_id']}/index.tar").exists()
        for entry in (route["current"], route["previous"])
    )


def test_source_manifest_byte_tamper_blocks_retention_before_deletion(controller, pinned_adapter):
    manifest = seed_tantivy_publication(controller, TANTIVY, SOURCE)
    key = f"releases/search/{SOURCE}/manifest.json"
    controller.s3.seed(key, controller.s3.path(key).read_bytes() + b" ")
    with pytest.raises(ValueError, match="byte identity"):
        controller.prune_artifacts()
    assert controller.s3.deletions == []
    assert manifest["source_build_id"] == SOURCE


def test_installer_checksum_mismatch_is_rejected_before_source_execution(tmp_path, pinned_adapter):
    marker = tmp_path / "executed"
    script = tmp_path / "changed.py"
    script.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    with pytest.raises(ValueError, match="checksum mismatch"):
        TantivyAdapter(script, pinned_adapter.source_sha256)
    assert not marker.exists()


def test_actual_bundle_install_restart_and_http_qualification_through_adapter(
    tmp_path, pinned_adapter, monkeypatch, controller
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
    native = {
        "manifest_version": 2,
        "format_version": 5,
        "active_build": manifest["source_build_id"],
        "snapshot_url": f"releases/search/{manifest['source_build_id']}/index.db",
        "actor_count": 1,
        "source_watermark": manifest["source_watermark"],
        "normalize_version": manifest["normalize_version"],
        "sha256": manifest["source_database_sha256"],
        "prefix_map": {"sha256": manifest["source_prefix_sha256"]},
        "legacy_twin": {
            "build_id": manifest["legacy_build_id"],
            "source_watermark": manifest["source_watermark"],
        },
    }
    source = json.dumps(native).encode()
    manifest["source_manifest_sha256"] = hashlib.sha256(source).hexdigest()
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
    portable = pinned_adapter.freeze(data, published, manifest, binary, source)
    assert "volume_id" not in portable and "provider_app" not in portable
    bundle = published / "index.tar"
    manifest.update(portable)
    destination = tmp_path / "installed"
    receipt = pinned_adapter.install(bundle, destination, manifest, binary, source)
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
                source,
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
                    source,
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
        source,
        bundle,
        binary,
        fixtures,
    )
    producer = tmp_path / "pinned-producer"
    script = producer / "scripts/snapshot_refresh/tantivy_install.py"
    script.parent.mkdir(parents=True)
    shutil.copyfile(repository / "scripts/snapshot_refresh/tantivy_install.py", script)
    for arguments in (
        ["init", "-q"],
        ["add", "."],
        [
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "user.name=offline-fixture",
            "-c",
            "user.email=offline@example.invalid",
            "commit",
            "-qm",
            "pinned fixture",
        ],
    ):
        subprocess.run(
            ["git", *arguments], cwd=producer, check=True, capture_output=True, timeout=10
        )
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=producer,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    source_dir = tmp_path / "native-source-manifest"
    source_dir.mkdir()
    (source_dir / "manifest.json").write_bytes(source)
    resumed = tmp_path / "tantivy-builds/build-b1793236000-pilot"
    resumed.mkdir(parents=True)
    data.rename(resumed / "data")
    identity = {
        "source_revision": revision,
        "adapter_sha256": pinned_adapter.source_sha256,
        "binary_sha256": manifest["binary_sha256"],
        "source_manifest_sha256": hashlib.sha256(source).hexdigest(),
        "max_source_bytes": 8 * 1024**3,
        "minimum_select_interval": 0.5,
    }
    (resumed / "owner").write_text("typeahead-index-tantivy-v1\n")
    (resumed / "identity.json").write_text(json.dumps(identity))
    (resumed / "export-receipt.json").write_text(
        json.dumps({"actors_sha256": manifest["source_actors_sha256"]})
    )
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "TURSO_URL": "https://127.0.0.1:1",
        "TURSO_AUTH_TOKEN": "offline-not-used",
        "INDEX_TANTIVY_SOURCE_REVISION": revision,
        "INDEX_TANTIVY_BINARY_SHA256": manifest["binary_sha256"],
        "INDEX_TANTIVY_ADAPTER_SHA256": pinned_adapter.source_sha256,
        "INDEX_TANTIVY_PREPARE_SECONDS": "30",
    }
    ready = prepare_generation(producer, source_dir, resumed, binary, environment, resume=True)
    assert (ready / "index.tar").is_file()
    assert not (producer / "services/tantivy-input/export.py").exists()
    assert not (resumed / "source-budget.json").exists()
    assert (resumed / "replay.log").stat().st_size == 0
    assert (
        prepare_generation(producer, source_dir, resumed, binary, environment, resume=True) == ready
    )
    impossible_source = tmp_path / "impossible-deadline-source"
    impossible_source.mkdir()
    (impossible_source / "manifest.json").write_text(
        json.dumps(native | {"actor_count": 12_860_000})
    )
    impossible_workspace = tmp_path / "impossible-builder"
    with pytest.raises(ValueError, match="lower bound"):
        prepare_generation(
            producer,
            impossible_source,
            impossible_workspace,
            binary,
            environment | {"INDEX_TANTIVY_PREPARE_SECONDS": "10800"},
        )
    assert not impossible_workspace.exists()
    with pytest.raises(ValueError, match="resume identity mismatch"):
        prepare_generation(
            producer,
            source_dir,
            resumed,
            binary,
            environment | {"INDEX_TANTIVY_MAX_SOURCE_BYTES": str(1024**3)},
            resume=True,
        )
    if shutil.which("rclone"):
        import flows.typeahead_index as publisher

        monkeypatch.setattr(publisher, "INDEXER_HOME", tmp_path)
        remote = tmp_path / "remote"
        remote_source = remote / f"releases/search/{manifest['source_build_id']}/manifest.json"
        remote_source.parent.mkdir(parents=True)
        remote_source.write_bytes(source)
        pointer = remote / "control/search-routing.json"
        pointer.parent.mkdir(parents=True)
        pointer.write_bytes(b"preserve-native-route")
        configuration = tmp_path / "rclone.conf"
        configuration.write_text("[r2]\ntype = local\n")
        monkeypatch.setenv("INDEX_R2_BUCKET", str(remote))
        assert (
            publisher.publish_tantivy_generation.fn(repository, ready, source_dir, configuration)
            == ready
        )
        assert (ready / "index.tar").is_file()
        assert (resumed / "data").is_dir()
        publisher.retire_tantivy_generation.fn(ready)
        assert not (ready / "index.tar").exists() and not (resumed / "data").exists()
        assert (
            prepare_generation(producer, source_dir, resumed, binary, environment, resume=True)
            == ready
        )
        assert (
            publisher.publish_tantivy_generation.fn(repository, ready, source_dir, configuration)
            == ready
        )
        assert pointer.read_bytes() == b"preserve-native-route"
        remote_bundle = remote / manifest["object_key"]
        with remote_bundle.open("ab") as changed:
            changed.write(b"corruption")
        with pytest.raises(publisher.ProcessExecutionError, match="remote verification failed"):
            publisher.publish_tantivy_generation.fn(repository, ready, source_dir, configuration)
        assert not (resumed / "data").exists()


def test_failed_anchored_rollback_restores_exact_entire_prior_route(controller):
    prior = anchored_route()
    controller.s3.seed(refresh.ROUTE_KEY, json.dumps(prior).encode())
    controller.public_fail = True
    with pytest.raises(RuntimeError, match="public verification"):
        controller.rollback_to_native()
    assert controller.route()[0] == prior
    assert len(controller.s3.writes) == 2


def test_native_anchor_cannot_be_non_native_or_override_native_previous(controller):
    route = {**anchored_route(), "native_rollback": TANTIVY}
    controller.s3.seed(refresh.ROUTE_KEY, json.dumps(route).encode())
    with pytest.raises(ValueError, match="anchor must be native"):
        controller.route()
    route = {"version": 1, "current": NATIVE, "previous": TANTIVY, "native_rollback": NATIVE}
    controller.s3.seed(refresh.ROUTE_KEY, json.dumps(route).encode())
    with pytest.raises(RuntimeError, match="installation is fenced"):
        controller.rollback_to_native()
    assert controller.s3.writes == []


def test_anchored_rollback_cas_race_preserves_operator_route(controller):
    route = anchored_route()
    controller.s3.seed(refresh.ROUTE_KEY, json.dumps(route).encode())
    raced = {**route, "operator_change": True}
    controller.s3.before_put = lambda: controller.s3.seed(
        refresh.ROUTE_KEY, json.dumps(raced).encode()
    )
    with pytest.raises(ConditionalWriteFailed):
        controller.rollback_to_native()
    assert controller.route()[0] == raced
    assert controller.s3.writes == []


def test_local_retention_rechecks_routed_sources_before_deletion(tmp_path, monkeypatch):
    for build in ["b1792000000-old", "b1792100000-old", "b1793100000-new", "b1793200000-new"]:
        path = tmp_path / ("build-" + build)
        path.mkdir()
        (path / "index.db").write_bytes(b"preserve-on-route-change")
    observations = iter([{NATIVE["build_id"]}, {SOURCE}])
    monkeypatch.setattr(refresh, "protected_source_build_ids", lambda logger: next(observations))
    with pytest.raises(RuntimeError, match="identities changed"):
        prune_builds.fn(str(tmp_path))
    assert len(list(tmp_path.glob("build-*"))) == 4


def test_automatic_flow_fences_before_any_build_or_cleanup(controller, monkeypatch):
    import flows.typeahead_index as publisher

    def forbidden(*args, **kwargs):
        raise AssertionError("Corpus preparation or cleanup must not start while Tantivy serves")

    monkeypatch.setattr(refresh, "Refresh", lambda logger: controller)
    monkeypatch.setattr(publisher, "get_run_logger", lambda: controller.logger)
    for operation in [
        "prune_builds",
        "require_build_space",
        "clone_repo",
        "build_binary",
        "run_indexer",
        "publish_compact",
        "publish_completed_generation",
        "archive_builds",
    ]:
        monkeypatch.setattr(publisher, operation, forbidden)
    with pytest.raises(RuntimeError, match="installation is fenced"):
        publisher.typeahead_index.fn(snapshot_format="compact", automatic_refresh=True)
    assert controller.s3.writes == []
    assert controller.s3.deletions == []
