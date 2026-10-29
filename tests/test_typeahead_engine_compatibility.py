"""Offline controller exercise using actual HTTP and owned filesystem objects."""

import hashlib
import http.server
import io
import json
import logging
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from mps import typeahead_refresh as refresh

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
