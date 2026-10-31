import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import MethodType

import httpx
import pytest
from mps import typeahead_refresh as refresh
from mps.typeahead_tantivy_slots import APPS, GATES, OWNER, Slots, raw_json, validate_release_proof

from tests.test_typeahead_engine_compatibility import (
    NATIVE,
    TANTIVY,
    ConditionalWriteFailed,
    DiskBucket,
    controller as controller,
    pinned_adapter as pinned_adapter,
    seed_checkpoint_publication,
)


class SlotBucket(DiskBucket):
    def put_object(self, *, Bucket, Key, Body, ContentType, IfMatch=None, IfNoneMatch=None):
        if IfNoneMatch:
            if self.path(Key).exists():
                raise ConditionalWriteFailed("already exists")
        elif self.get_object(Bucket=Bucket, Key=Key)["ETag"] != IfMatch:
            raise ConditionalWriteFailed("changed")
        self.seed(Key, Body)
        return {"ETag": hashlib.sha256(Body).hexdigest()}

    def delete_object(self, *, Bucket, Key, IfMatch):
        assert self.get_object(Bucket=Bucket, Key=Key)["ETag"] == IfMatch
        self.path(Key).unlink()


def slot_service(controller, tmp_path):
    controller.s3 = SlotBucket(controller.s3.root)
    controller.execution_identity = lambda: "execution-one"
    controller.execution_finished = lambda _: False
    return Slots(controller, tmp_path / "journals", timeout_seconds=2)


def test_lease_preserves_route_etag_race_and_native_compensation(controller, tmp_path):
    slots = slot_service(controller, tmp_path)
    original, _ = controller.route()
    state = {
        "request_id": "a" * 32,
        "owner": OWNER,
        "app": APPS[0],
        "route": original,
        "execution_id": "execution-one",
    }
    slots.acquire(state)
    leased, etag = controller.route()
    assert {key: value for key, value in leased.items() if key != "mutation"} == original
    with pytest.raises(RuntimeError, match="leased"):
        controller._put_route(
            {**leased, "current": {**TANTIVY, "origin": f"https://{APPS[0]}.fly.dev"}}, etag
        )
    # Existing native rollback preserves the lease; failed public verification
    # can compensate only the same exact route and ETag.
    failed = {"version": 1, "current": NATIVE, "previous": TANTIVY, "native_rollback": NATIVE}
    written = controller._put_route(failed, etag)
    assert failed["mutation"] == leased["mutation"]
    restored = controller._restore_previous_route(leased, failed, written)
    assert controller.route() == (leased, restored)
    controller.s3.seed(refresh.ROUTE_KEY, raw_json({**leased, "operator_change": True}))
    with pytest.raises(RuntimeError, match="Routing changed"):
        controller._restore_previous_route(leased, failed, written)
    assert controller.route()[0]["operator_change"] is True


def test_remote_journal_survives_local_loss_and_rejects_active_owner(controller, tmp_path):
    slots = slot_service(controller, tmp_path)
    state = {"request_id": "b" * 32, "owner": OWNER, "execution_id": "execution-one"}
    slots.save(state, "planned")
    (slots.root / ("b" * 32 + ".json")).unlink()
    recovered = Slots(controller, tmp_path / "other", timeout_seconds=2)
    assert recovered.journal("b" * 32)["phase"] == "planned"
    controller.execution_identity = lambda: "execution-two"
    with pytest.raises(RuntimeError, match="still active"):
        recovered.journal("b" * 32)
    controller.execution_finished = lambda _: True
    assert recovered.journal("b" * 32)["execution_id"] == "execution-two"


def test_fixture_release_proof_cannot_enable_production():
    proof = {
        "schema_version": 1,
        "engine": "tantivy",
        "passed": True,
        "test_only": True,
        "binding": {},
        "gates": {key: {"passed": True, "evidence_sha256": "a" * 64} for key in GATES},
    }
    raw = raw_json(proof)
    with pytest.raises(ValueError, match="non-test"):
        validate_release_proof(raw, hashlib.sha256(raw).hexdigest(), {})


@pytest.mark.parametrize("checkpoint", [False, True])
@pytest.mark.parametrize(
    "retired_state", [None, "pending_destroy", "destroyed", "deleted", "created", "attached"]
)
def test_actual_provider_http_lost_ack_resume_and_cached_restart(
    controller, tmp_path, pinned_adapter, monkeypatch, checkpoint, retired_state
):
    slots = slot_service(controller, tmp_path)
    controller.tantivy_adapter = lambda: pinned_adapter
    route, _ = controller.route()
    state = {
        "request_id": "c" * 32,
        "owner": OWNER,
        "execution_id": "execution-one",
        "app": APPS[0],
        "route": route,
        "build_id": "b1793236000-slot",
        "image_digest": "registry.fly.io/fixture@sha256:" + "d" * 64,
        "artifact_manifest_sha256": "e" * 64,
        "source_build_id": "b1793236000-source-px05",
        "legacy_build_id": "b1793236000-source",
        "region": "ewr",
        "machine_name": "candidate",
        "volume_name": "candidate",
        "pending_action": None,
    }
    slots.save(state, "planned")
    slots.acquire(state)
    calls = []
    statefile = tmp_path / "provider.json"
    retired = (
        []
        if retired_state is None
        else [
            {
                "id": "vol_retired",
                "name": "old_preparation",
                "state": "pending_destroy" if retired_state == "attached" else retired_state,
                "attached_machine_id": "other_machine" if retired_state == "attached" else None,
                "attached_alloc_id": None,
            }
        ]
    )
    statefile.write_text(json.dumps({"machines": [], "volumes": retired}))
    dropped = set()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.run_request("GET")

        def do_POST(self):
            self.run_request("POST")

        def run_request(self, method):
            assert self.headers["Authorization"] == "Bearer fixture-token"
            calls.append((method, self.path))
            pieces = self.path.strip("/").split("/")
            assert pieces[0] == APPS[0]
            path = "/".join(pieces[1:])
            if path.endswith("/stop-rejected"):
                self.send_response(403)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            data = json.loads(statefile.read_text())
            payload = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"null"
            )
            if method == "GET":
                values = data[pieces[1]]
                result = (
                    values
                    if len(pieces) == 2
                    else next(item for item in values if item["id"] == pieces[2])
                )
            elif path == "volumes":
                result = {**payload, "id": "vol_fixture", "state": "created"}
                data["volumes"].append(result)
            elif path == "machines":
                result = {
                    **payload,
                    "id": "0123456789abcd",
                    "state": "created",
                    "instance_id": "fixture-instance",
                }
                result["config"]["mounts"][0].update(
                    encrypted=True,
                    size_gb=20,
                    name=next(v["name"] for v in data["volumes"] if v["id"] == "vol_fixture"),
                )
                data["machines"].append(result)
            else:
                result = data["machines"][0]
                result["state"] = "started" if pieces[-1] == "start" else "stopped"
            statefile.write_text(json.dumps(data))
            if method == "POST" and path in ("volumes", "machines") and path not in dropped:
                dropped.add(path)
                self.close_connection = True
                return
            raw = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    controller.fly = MethodType(refresh.Refresh.fly, controller)
    controller._fly_url = lambda app, path: f"http://127.0.0.1:{server.server_port}/{app}/{path}"
    monkeypatch.setenv("FLY_TANTIVY_A_TOKEN", "fixture-token")
    manifest = {"source_build_id": state["source_build_id"]}
    if checkpoint:
        target = {**TANTIVY, "build_id": state["build_id"]}
        manifest, _ = seed_checkpoint_publication(controller, target)
    try:
        if retired_state in ("created", "attached"):
            with pytest.raises(RuntimeError, match="unrelated or retained volume"):
                slots.allocate(state, manifest)
            assert not any(method == "POST" for method, _ in calls)
            return
        bound = slots.allocate(state, manifest)
        first_config = json.loads(statefile.read_text())["machines"][0]["config"]
        assert bound["guest"] == {"cpu_kind": "shared", "cpus": 2, "memory_mb": 1024}
        assert bound["volume_size_gib"] == 20
        native_build = controller.tantivy_source_binding(manifest)[0]
        assert bound["source_manifest_key"] == f"releases/search/{native_build}/manifest.json"
        slots.start(state)
        slots.stop(state)
        slots.start(state)
        assert json.loads(statefile.read_text())["machines"][0]["config"] == first_config
        resumed = Slots(controller, tmp_path / "resumed", timeout_seconds=2)
        restored = resumed.journal(state["request_id"])
        assert resumed.allocate(restored, manifest) == bound
        assert calls.count(("POST", f"/{APPS[0]}/volumes")) == 1
        assert calls.count(("POST", f"/{APPS[0]}/machines")) == 1
        assert controller.route()[0]["current"] == route["current"]
        assert all(APPS[0] in path for _, path in calls)
        if not checkpoint and retired_state is None:
            resumed.action(restored, "stop", "POST", f"machines/{state['machine_id']}/stop-rejected")
            assert restored["pending_action"]["error"] == "HTTP 403"
            restored = resumed.journal(state["request_id"])
            assert restored["pending_action"]["error"] == "HTTP 403"
            previous_timeout = resumed.timeout
            resumed.timeout = 0
            with pytest.raises(
                RuntimeError,
                match="remains started; expected stopped; action stop; provider outcome HTTP 403",
            ):
                resumed.wait(restored, "stopped")
            resumed.timeout = previous_timeout
        original = json.loads(statefile.read_text())
        mount = original["machines"][0]["config"]["mounts"][0]
        assert mount["encrypted"] is True and mount["size_gb"] == 20
        for invalid in [
            [dict(mount, volume="vol_wrong")],
            [dict(mount, path="/other")],
            [mount, dict(mount)],
            [dict(mount, read_only=True)],
            [dict(mount, readonly=True)],
            [dict(mount, mode="ro")],
            [dict(mount, encrypted=False)],
            [dict(mount, size_gb=21)],
        ]:
            changed = json.loads(json.dumps(original))
            changed["machines"][0]["config"]["mounts"] = invalid
            statefile.write_text(json.dumps(changed))
            with pytest.raises(RuntimeError, match="configuration changed"):
                resumed.machine(restored, restored["machine_id"])
        changed = original
        changed["machines"][0]["config"]["guest"]["memory_mb"] = 2048
        statefile.write_text(json.dumps(changed))
        with pytest.raises(RuntimeError, match="configuration changed"):
            resumed.stop(restored)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_checkpoint_retained_slot_verifies_anchor_twin_at_its_own_watermark(
    controller, tmp_path, pinned_adapter
):
    controller.tantivy_adapter = lambda: pinned_adapter
    slots = slot_service(controller, tmp_path)
    target = {**TANTIVY, "origin": f"https://{APPS[0]}.fly.dev"}
    manifest, _ = seed_checkpoint_publication(controller, target)
    bound = {**manifest, "provider_app": APPS[0]}
    deployment_raw = raw_json(bound)
    deployment_key = "control/tantivy-slots/fixture-deployment.json"
    controller.s3.seed(deployment_key, deployment_raw)
    admission = {"bundle_sha256": manifest["sha256"]}
    digest = pinned_adapter.installer.digest_json(admission)
    target["admission_sha256"] = digest
    controller.s3.seed(
        f"control/tantivy-admissions/{digest}.json",
        raw_json(
            {
                "schema_version": 1,
                "admission": admission,
                "deployment_key": deployment_key,
                "deployment_sha256": hashlib.sha256(deployment_raw).hexdigest(),
            }
        ),
    )
    retained = slots.retained(target)
    assert retained["rollback_native_build_id"] == manifest["rollback_native_anchor"]["build_id"]
    assert retained["rollback_native_manifest_sha256"] != retained["source_manifest_sha256"]
    assert len(retained["objects"]) == 4
    assert manifest["source_watermark"] != manifest["rollback_native_anchor"]["source_watermark"]
    native = json.loads(controller.tantivy_source_manifest(manifest))
    twin_key = native["legacy_twin"]["manifest"]["object_key"]
    controller.s3.seed(twin_key, controller.s3.path(twin_key).read_bytes() + b" ")
    with pytest.raises(ValueError, match="twin manifest"):
        slots.retained(target)


def exercise_real_rust_preparation(
    controller, tmp_path, adapter, portable, source, bundle, binary, fixtures
):
    import os

    slots = slot_service(controller, tmp_path)
    controller.tantivy_adapter = lambda: adapter
    controller.s3.seed(
        f"releases/search-tantivy/{portable['active_build']}/manifest.json", raw_json(portable)
    )
    controller.s3.seed(f"releases/search/{portable['source_build_id']}/manifest.json", source)
    records = {"machines": [], "volumes": []}
    process = None
    public = None
    installation = None
    starts = 0
    installs = 0
    calls = []
    log = (tmp_path / "slot-rust.log").open("wb")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.handle_request("GET")

        def do_POST(self):
            self.handle_request("POST")

        def handle_request(self, method):
            nonlocal process, public, installation, starts, installs
            parts = self.path.strip("/").split("/")
            assert parts[0] == APPS[0]
            calls.append((method, self.path))
            body = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"null"
            )
            if method == "GET":
                result = (
                    records[parts[1]]
                    if len(parts) == 2
                    else next(row for row in records[parts[1]] if row["id"] == parts[2])
                )
            elif parts[1:] == ["volumes"]:
                result = dict(body, id="vol_fixture", state="created")
                records["volumes"].append(result)
            elif parts[1:] == ["machines"]:
                result = dict(
                    body, id="abcdef12345678", instance_id="fixture-instance", state="created"
                )
                records["machines"].append(result)
            elif parts[-1] == "start":
                config = records["machines"][0]["config"]
                env = config["env"]
                bound = json.loads(controller.s3.path(env["TANTIVY_DEPLOYMENT_KEY"]).read_bytes())
                if installation is None:
                    receipt = adapter.install(
                        bundle, tmp_path / "slot-cache", bound, binary, source
                    )
                    installation = dict(
                        receipt,
                        deployment_sha256=env["EXPECTED_DEPLOYMENT_SHA256"],
                        artifact_manifest_sha256=bound["artifact_manifest_sha256"],
                    )
                    installs += 1
                process, public, _ = fixtures.start_strict(
                    binary,
                    tmp_path / "slot-cache",
                    log,
                    "slot-fixture-secret",
                    fixture_identity=True,
                )
                starts += 1
                records["machines"][0]["state"] = "started"
                result = records["machines"][0]
            elif parts[-1] == "stop":
                fixtures.smoke.stop(process)
                process = None
                records["machines"][0]["state"] = "stopped"
                result = records["machines"][0]
            elif parts[-1] == "exec":
                assert body["command"][0] == "python3"
                assert "/data/tantivy/installation.json" in body["command"][2]
                result = {"exit_code": 0, "exit_signal": 0, "stdout": json.dumps(installation)}
            else:
                raise AssertionError((method, self.path))
            raw = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    controller.fly = MethodType(refresh.Refresh.fly, controller)
    controller._fly_url = lambda app, path: f"http://127.0.0.1:{server.server_port}/{app}/{path}"
    previous_token = os.environ.get("FLY_TANTIVY_A_TOKEN")
    os.environ["FLY_TANTIVY_A_TOKEN"] = "fixture-token"

    def qualify(url, bound, receipt, provider):
        assert url == f"https://{APPS[0]}.fly.dev"
        return adapter.qualify(
            public, bound, receipt, dict(provider, test_only=True), source, allow_loopback=True
        )

    controller.qualify_tantivy_candidate = qualify
    before = controller.route()[0]
    try:
        result = slots.prepare(
            portable["active_build"],
            portable.get("image_digest", "registry.fly.io/fixture@sha256:" + "f" * 64),
            request_id="d" * 32,
        )
        assert result["routing_target_changed"] is False
        assert result["plan"]["test_only"] is True
        assert result["plan"]["promotion_permitted"] is False
        assert starts == 2 and installs == 1
        repeated = slots.prepare(
            portable["active_build"],
            portable.get("image_digest", "registry.fly.io/fixture@sha256:" + "f" * 64),
            request_id="d" * 32,
        )
        assert repeated["plan_sha256"] == result["plan_sha256"]
        assert starts == 2 and installs == 1
        assert controller.route()[0] == before
        assert (
            fixtures.smoke.request(public, "/search?q=real")[2]["actors"][0]["handle"]
            == "real.test"
        )
        assert calls.count(("POST", f"/{APPS[0]}/machines")) == 1
        assert calls.count(("POST", f"/{APPS[0]}/volumes")) == 1
        assert {
            portable["active_build"],
            portable["source_build_id"],
            portable["legacy_build_id"],
        } <= controller.retained_build_ids()
        with pytest.raises(ValueError, match="Test-only"):
            slots.promote(
                result["plan_sha256"], result["plan"]["expected_current_etag"], None, None
            )
        canceled = slots.cancel("d" * 32)
        assert canceled["phase"] == "canceled-retained"
        assert controller.route()[0] == before
        assert process is None
        state = slots.journal("d" * 32)
        state["pending_action"] = {
            "name": "start",
            "method": "POST",
            "path": f"machines/{state['machine_id']}/start",
        }
        slots.save(state, "start-intent")
        canceled = slots.cancel("d" * 32)
        assert canceled["phase"] == "canceled-retained"
        assert process is None and starts == 2
        assert controller.route()[0] == before
    finally:
        if process is not None:
            fixtures.smoke.stop(process)
        if previous_token is None:
            os.environ.pop("FLY_TANTIVY_A_TOKEN", None)
        else:
            os.environ["FLY_TANTIVY_A_TOKEN"] = previous_token
        server.shutdown()
        server.server_close()
        thread.join()
        log.close()


def test_failed_public_verification_compensates_only_exact_head(controller, tmp_path):
    slots = slot_service(controller, tmp_path)
    before = {"version": 1, "current": NATIVE, "previous": TANTIVY, "native_rollback": NATIVE}
    controller.s3.seed(refresh.ROUTE_KEY, raw_json(before))
    _, etag = controller.route()
    target = {"version": 1, "current": TANTIVY, "previous": NATIVE, "native_rollback": NATIVE}
    written = controller._put_route(target, etag)
    state = {
        "request_id": "e" * 32,
        "owner": OWNER,
        "execution_id": "execution-one",
        "app": APPS[0],
    }
    with pytest.raises(RuntimeError, match="verification failed"):
        slots.complete_promotion(
            state, "fixture-plan", before, target, written, {"test_only": True}
        )
    assert controller.route()[0] == before
    assert slots.journal("e" * 32)["phase"] == "compensated"
    _, etag = controller.route()
    written = controller._put_route(target, etag)
    changed = {**before, "operator_change": "wins"}
    controller.public_race = lambda: controller.s3.seed(refresh.ROUTE_KEY, raw_json(changed))
    with pytest.raises(RuntimeError, match="refusing compensating rollback"):
        slots.complete_promotion(
            state, "fixture-plan", before, target, written, {"test_only": True}
        )
    assert controller.route()[0] == changed


def test_public_transport_identity_and_canary_primitive_uses_real_http(controller, tmp_path):
    slots = slot_service(controller, tmp_path)
    before = {"version": 1, "current": NATIVE, "previous": TANTIVY, "native_rollback": NATIVE}
    controller.s3.seed(refresh.ROUTE_KEY, raw_json(before))
    _, etag = controller.route()
    target = {"version": 1, "current": TANTIVY, "previous": NATIVE, "native_rollback": NATIVE}
    written = controller._put_route(target, etag)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path.startswith("/xrpc/tech.waow.typeahead.searchActors?")
            body = raw_json({"actors": [{"handle": "zzstoatzz.io"}]})
            self.send_response(200)
            for key, header in (
                ("engine", "X-Typeahead-Engine"),
                ("build_id", "X-Typeahead-Build"),
                ("format", "X-Typeahead-Format"),
                ("binary_sha256", "X-Typeahead-Binary-Sha256"),
                ("retrieval_strategy", "X-Typeahead-Retrieval-Strategy"),
            ):
                self.send_header(header, TANTIVY[key])
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    old_get = controller.get

    def get(base, path):
        if base == refresh.PUBLIC and path.startswith("/xrpc/"):
            return refresh.Refresh.get(controller, f"http://127.0.0.1:{server.server_port}", path)
        return old_get(base, path)

    controller.get = get
    state = {
        "request_id": "f" * 32,
        "owner": OWNER,
        "execution_id": "execution-one",
        "app": APPS[0],
    }
    try:
        result = slots.complete_promotion(
            state, "fixture-plan", before, target, written, {"test_only": True}
        )
        assert result["public_verified"] is True
        assert result["release_proof"]["test_only"] is True
        assert controller.route()[0] == target
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_retirement_rechecks_strict_source_age_over_actual_http(tmp_path, pinned_adapter):
    import importlib.util
    import logging
    import os
    import time
    from pathlib import Path

    import httpx

    repository = Path(os.environ["TA_REPOSITORY"])
    spec = importlib.util.spec_from_file_location(
        "retirement_reference", repository / "scripts/snapshot_refresh/test_tantivy_install.py"
    )
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    reference = fixtures.AdmissionTests()
    now = int(time.time())
    manifest = reference.manifest()
    status = reference.status()
    status["source_cursor"]["reconciliation"].update(
        last_completed_at=now - 10, last_completed_started_at=now - 100
    )
    status["moderation"]["source_observed_at"] = now - 5
    status["watermark"] = now - 121
    status["source_cursor"]["through"] = status["watermark"]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = (
                {
                    "ready": True,
                    "sync_lag_s": now - status["watermark"],
                    "build_id": manifest["active_build"],
                    "engine": "tantivy",
                    "format": manifest["backend_format"],
                    "binary_sha256": manifest["binary_sha256"],
                    "retrieval_strategy": manifest["retrieval_strategy"],
                }
                if self.path == "/health"
                else status
            )
            raw = raw_json(body)
            self.send_response(200)
            for key, header in (
                ("engine", "X-Typeahead-Engine"),
                ("build_id", "X-Typeahead-Build"),
                ("format", "X-Typeahead-Format"),
                ("binary_sha256", "X-Typeahead-Binary-Sha256"),
                ("retrieval_strategy", "X-Typeahead-Retrieval-Strategy"),
            ):
                self.send_header(header, str(body[key]))
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    service = refresh.Refresh.__new__(refresh.Refresh)
    service.http = httpx.Client(timeout=3)
    service.logger = logging.getLogger(__name__)
    service.tantivy_adapter = lambda: pinned_adapter
    target = {
        "engine": "tantivy",
        "origin": f"http://127.0.0.1:{server.server_port}",
        "build_id": manifest["active_build"],
        "format": manifest["backend_format"],
        "binary_sha256": manifest["binary_sha256"],
        "retrieval_strategy": manifest["retrieval_strategy"],
    }
    slots = Slots(service, tmp_path / "journals", timeout_seconds=2)
    try:
        assert service.healthy(target)["index"]["watermark"] == now - 121
        with pytest.raises(ValueError, match="Source polling is not fresh"):
            slots.retirement_freshness(target, manifest)
        status["watermark"] = now - 5
        status["source_cursor"]["through"] = status["watermark"]
        assert slots.retirement_freshness(target, manifest)["watermark"] == now - 5
    finally:
        service.http.close()
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("behavior", ["recover", "route-change", "timeout"])
def test_qualification_guard_transport_retry_preserves_route_fence_and_deadline(
    controller, tmp_path, behavior
):
    initial = {"version": 1, "current": NATIVE, "previous": None}
    controller.s3.seed(refresh.ROUTE_KEY, raw_json(initial))
    counts = {"health": 0, "candidate": 0}
    timeout_errors = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/health":
                counts["health"] += 1
                if counts["health"] == 1 or behavior == "timeout":
                    if behavior == "route-change":
                        controller.s3.seed(
                            refresh.ROUTE_KEY,
                            raw_json(
                                {
                                    **initial,
                                    "current": {**NATIVE, "build_id": "b1792908655-changed"},
                                }
                            ),
                        )
                    time.sleep(0.15)
                value = {"ready": True, "sync_lag_s": 0}
            elif self.path == "/index/status":
                value = {**NATIVE, "snapshot_format": "FLATPX05"}
            elif self.path == "/health/freshness":
                value = {"ok": True, "probe_ok": True, "build_id": NATIVE["build_id"]}
            elif self.path == "/candidate":
                counts["candidate"] += 1
                value = {"qualified": True}
            else:
                self.send_error(404)
                return
            raw = raw_json(value)
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            with suppress(BrokenPipeError):
                self.wfile.write(raw)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    slots = Slots(
        controller,
        tmp_path / "qualification-journal",
        timeout_seconds=1 if behavior == "timeout" else 8,
    )
    state = {"app": APPS[0], "route": initial}

    def get(_base, path):
        try:
            response = controller.http.get(base + path, timeout=0.05)
        except httpx.ReadTimeout as error:
            timeout_errors.append(type(error))
            raise
        return response.status_code, response.json(), response.headers

    def qualify(_state, _bound):
        response = controller.http.get(base + "/candidate", timeout=1)
        response.raise_for_status()
        return response.json(), {}

    controller.get = get
    slots.qualify = qualify
    started = time.monotonic()
    try:
        if behavior == "recover":
            assert slots.wait_qualified(state, {}) == ({"qualified": True}, {})
            assert counts == {"health": 2, "candidate": 1}
            assert 5 <= time.monotonic() - started < 8
            assert controller.route()[0] == initial
        elif behavior == "route-change":
            with pytest.raises(RuntimeError, match="Routing changed"):
                slots.wait_qualified(state, {})
            assert counts == {"health": 1, "candidate": 0}
            assert time.monotonic() - started < 8
        else:
            with pytest.raises(RuntimeError, match="qualification timed out"):
                slots.wait_qualified(state, {})
            assert counts == {"health": 1, "candidate": 0}
            assert time.monotonic() - started < 2
        assert timeout_errors == [httpx.ReadTimeout]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("behavior", ["recover", "deadline", "unauthorized"])
def test_qualification_candidate_urllib_startup_failure_is_bounded(controller, tmp_path, behavior):
    initial = {"version": 1, "current": NATIVE, "previous": None}
    controller.s3.seed(refresh.ROUTE_KEY, raw_json(initial))
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(time.monotonic())
            status = (
                401
                if behavior == "unauthorized"
                else 503
                if behavior == "deadline" or len(calls) == 1
                else 200
            )
            raw = raw_json({"qualified": status == 200})
            self.send_response(status)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    slots = Slots(
        controller, tmp_path / "urllib-journal", timeout_seconds=8 if behavior == "recover" else 1
    )
    state = {"app": APPS[0], "route": initial}

    def qualify(_state, _bound):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/ready", timeout=0.5
        ) as response:
            return json.load(response), {}

    slots.qualify = qualify
    started = time.monotonic()
    try:
        if behavior == "recover":
            assert slots.wait_qualified(state, {}) == ({"qualified": True}, {})
            assert len(calls) == 2 and calls[1] - calls[0] >= 5
        elif behavior == "deadline":
            with pytest.raises(RuntimeError, match="qualification timed out"):
                slots.wait_qualified(state, {})
            assert len(calls) == 1 and time.monotonic() - started < 2
        else:
            with pytest.raises(urllib.error.HTTPError) as error:
                slots.wait_qualified(state, {})
            assert error.value.code == 401
            assert len(calls) == 1 and time.monotonic() - started < 1
        assert controller.route()[0] == initial
        assert controller.s3.writes == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_promotion_route_guard_survives_unavailable_public_serving(controller, tmp_path):
    slots = slot_service(controller, tmp_path)
    original, _ = controller.route()
    state = {
        "request_id": "f" * 32,
        "owner": OWNER,
        "app": APPS[0],
        "route": original,
        "execution_id": "execution-one",
    }
    controller.public_fail = True
    with pytest.raises(RuntimeError, match="Public serving identity"):
        slots.acquire(state)
    leased, _ = controller.route()
    assert leased["mutation"] == state["mutation"]
    slots.acquire(state, serving_health=False)
    slots.guard(state, serving_health=False)
    controller.s3.seed(refresh.ROUTE_KEY, raw_json({**leased, "operator_change": True}))
    with pytest.raises(RuntimeError, match="Routing changed"):
        slots.guard(state, serving_health=False)
