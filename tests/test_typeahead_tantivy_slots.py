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
    PREVIOUS,
    SOURCE,
    TANTIVY,
    ConditionalWriteFailed,
    DiskBucket,
    bucket_keys,
    controller as controller,
    pinned_adapter as pinned_adapter,
    seed_checkpoint_publication,
    seed_stub_publication,
    seed_tantivy_publication,
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


def test_lease_preserves_route_etag_race_and_compensation(controller, tmp_path):
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
    # A controlled update to another target preserves the lease; failed public
    # verification can compensate only the same exact route and ETag.
    failed = {"version": 1, "current": {**TANTIVY, "build_id": "b1793000001-operator"}}
    written = controller._put_route(failed, etag)
    assert failed["mutation"] == leased["mutation"]
    restored = controller._restore_previous_route(leased, failed, written)
    assert controller.route() == (leased, restored)
    controller.s3.seed(refresh.ROUTE_KEY, raw_json({**leased, "previous": PREVIOUS}))
    with pytest.raises(RuntimeError, match="Routing changed"):
        controller._restore_previous_route(leased, failed, written)
    assert controller.route()[0]["previous"] == PREVIOUS


def test_journal_and_plan_routes_written_before_native_removal_still_match(controller, tmp_path):
    slots = slot_service(controller, tmp_path)
    legacy = {"version": 1, "current": TANTIVY, "previous": NATIVE, "native_rollback": NATIVE}
    controller.s3.seed(refresh.ROUTE_KEY, raw_json(legacy))
    route, etag = controller.route()
    state = {
        "request_id": "9" * 32,
        "owner": OWNER,
        "app": APPS[0],
        "route": legacy,
        "native_rollback": NATIVE,
        "promotion_target": legacy,
        "execution_id": "execution-one",
    }
    slots.save(state, "planned")
    restored = slots.journal(state["request_id"])
    assert restored["route"] == restored["promotion_target"] == route
    assert slots.guard(restored, serving_health=False) == (route, etag)
    plan = raw_json(
        {
            "schema_version": 1,
            "owner": OWNER,
            "request_id": state["request_id"],
            "app": APPS[0],
            "expected_route": legacy,
            "native_rollback": NATIVE,
        }
    )
    digest = hashlib.sha256(plan).hexdigest()
    controller.s3.seed(f"control/tantivy-plans/{digest}.json", plan)
    restored["plan_sha256"] = digest
    slots.save(restored)
    assert slots.load_plan(digest)[0]["expected_route"] == route


@pytest.mark.parametrize("publication_owner", [None, "execution-one"])
def test_remote_journal_survives_local_loss_and_rejects_active_owner(
    controller, tmp_path, publication_owner
):
    slots = slot_service(controller, tmp_path)
    state = {"request_id": "b" * 32, "owner": OWNER, "execution_id": "execution-one"}
    if publication_owner:
        state["publication_execution_id"] = publication_owner
    slots.save(state, "planned")
    (slots.root / ("b" * 32 + ".json")).unlink()
    recovered = Slots(controller, tmp_path / "other", timeout_seconds=2)
    assert recovered.journal("b" * 32)["phase"] == "planned"
    controller.execution_identity = lambda: "execution-two"
    with pytest.raises(RuntimeError, match="still active"):
        recovered.journal("b" * 32)
    controller.execution_finished = lambda _: True
    resumed = recovered.journal("b" * 32)
    assert resumed["execution_id"] == "execution-two"
    assert resumed.get("publication_execution_id") == publication_owner


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
        manifest = seed_checkpoint_publication(controller, target)
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
        assert not {"source_manifest_key", "rollback_native_anchor", "legacy_build_id"} & set(bound)
        assert bucket_keys(controller, "releases/search") == []
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
            resumed.action(
                restored, "stop", "POST", f"machines/{state['machine_id']}/stop-rejected"
            )
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


@pytest.mark.parametrize("legacy", [False, True])
def test_retained_previous_slot_needs_only_its_bundle(controller, tmp_path, pinned_adapter, legacy):
    controller.tantivy_adapter = lambda: pinned_adapter
    slots = slot_service(controller, tmp_path)
    target = {**TANTIVY, "origin": f"https://{APPS[0]}.fly.dev"}
    manifest = seed_checkpoint_publication(controller, target, legacy=legacy)
    bound = {**manifest, "provider_app": APPS[0]}
    if legacy:
        bound["source_manifest_key"] = "releases/search/b1792000000-rollback-px05/manifest.json"
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
    assert bucket_keys(controller, "releases/search") == []
    retained = slots.retained(target)
    assert "anchor_manifest_key" not in retained
    assert [item["key"] for item in retained["objects"]] == [manifest["object_key"]]
    controller.s3.seed(manifest["object_key"], b"x")
    with pytest.raises(ValueError, match="bundle is missing or changed size"):
        slots.retained(target)


def test_preparation_resumes_records_written_with_retired_provenance(controller, tmp_path):
    slots = slot_service(controller, tmp_path)
    seed_stub_publication(controller, TANTIVY, legacy=True)
    manifest, raw = seed_stub_publication(
        controller, {**TANTIVY, "build_id": "b1793236000-slot"}, legacy=True
    )
    state = {
        "request_id": "f" * 32,
        "app": APPS[1],
        "build_id": manifest["active_build"],
        "source_build_id": manifest["source_build_id"],
        "legacy_build_id": manifest["legacy_build_id"],
        "artifact_manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "volume_id": "vol_fixture",
    }
    pending_key = f"control/tantivy-slots/{APPS[1]}/pending.json"
    pending = {
        "schema_version": 1,
        "owner": OWNER,
        "request_id": state["request_id"],
        "app": APPS[1],
        "build_id": state["build_id"],
        "source_build_id": state["source_build_id"],
        "legacy_build_id": state["legacy_build_id"],
        "artifact_manifest_sha256": state["artifact_manifest_sha256"],
    }
    controller.s3.seed(pending_key, raw_json(pending))
    slots.protect(state)
    assert json.loads(controller.s3.path(pending_key).read_bytes()) == pending
    assert manifest["active_build"] in controller.retained_build_ids()
    controller.s3.seed(pending_key, raw_json({**pending, "build_id": "b1793236001-other"}))
    with pytest.raises(RuntimeError, match="different prepared candidate"):
        slots.protect(state)

    bound = {**manifest, "provider_app": APPS[1], "request_id": state["request_id"]}
    key = f"control/tantivy-slots/{APPS[1]}/{state['request_id']}.json"
    written = raw_json({**bound, "source_manifest_key": "releases/search/b1/manifest.json"})
    controller.s3.seed(key, written)
    slots.config(state, bound, "registry.fly.io/fixture@sha256:" + "d" * 64)
    assert state["deployment_sha256"] == hashlib.sha256(written).hexdigest()
    with pytest.raises(RuntimeError, match="Immutable slot metadata changed"):
        slots.config(
            state, {**bound, "volume_id": "vol_other"}, "registry.fly.io/x@sha256:" + "d" * 64
        )


def exercise_real_rust_preparation(
    controller, tmp_path, adapter, portable, bundle, binary, fixtures, *, automatic=False
):
    import os

    slots = slot_service(controller, tmp_path)
    controller.tantivy_adapter = lambda: adapter
    seed_tantivy_publication(controller, controller.route()[0]["current"], SOURCE)
    controller.s3.seed(
        f"releases/search-tantivy/{portable['active_build']}/manifest.json", raw_json(portable)
    )
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
                    receipt = adapter.install(bundle, tmp_path / "slot-cache", bound, binary)
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
            public, bound, receipt, dict(provider, test_only=True), allow_loopback=True
        )

    controller.qualify_tantivy_candidate = qualify
    before = controller.route()[0]
    try:
        result = slots.prepare(
            portable["active_build"],
            portable.get("image_digest", "registry.fly.io/fixture@sha256:" + "f" * 64),
            request_id="d" * 32,
            automatic_refresh=automatic,
        )
        assert result["routing_target_changed"] is False
        assert result["plan"]["test_only"] is True
        assert result["plan"]["promotion_permitted"] is False
        assert starts == 2 and installs == 1
        assert result["preparation_receipt"].get("publication_execution_id") == (
            "execution-one" if automatic else None
        )
        controller.execution_identity = lambda: "execution-two"
        controller.execution_finished = lambda _: True
        repeated = slots.prepare(
            portable["active_build"],
            portable.get("image_digest", "registry.fly.io/fixture@sha256:" + "f" * 64),
            request_id="d" * 32,
            automatic_refresh=automatic,
        )
        assert repeated["plan_sha256"] == result["plan_sha256"]
        assert starts == 2 and installs == 1
        resumed = slots.journal("d" * 32)
        assert resumed["execution_id"] == "execution-two"
        assert resumed.get("publication_execution_id") == ("execution-one" if automatic else None)
        assert controller.route()[0] == before
        assert (
            fixtures.smoke.request(public, "/search?q=real")[2]["actors"][0]["handle"]
            == "real.test"
        )
        assert calls.count(("POST", f"/{APPS[0]}/machines")) == 1
        assert calls.count(("POST", f"/{APPS[0]}/volumes")) == 1
        assert portable["active_build"] in controller.retained_build_ids()
        assert bucket_keys(controller, "releases/search") == []
        deployment = json.loads(
            controller.s3.path(slots.journal("d" * 32)["deployment_key"]).read_bytes()
        )
        assert not {"source_manifest_key", "rollback_native_anchor", "legacy_build_id"} & set(
            deployment
        )
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
    before = {"version": 1, "current": TANTIVY}
    controller.s3.seed(refresh.ROUTE_KEY, raw_json(before))
    _, etag = controller.route()
    candidate = {**TANTIVY, "origin": f"https://{APPS[0]}.fly.dev", "build_id": "b1793300000-next"}
    target = {"version": 1, "current": candidate, "previous": TANTIVY}
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
    changed = {**before, "previous": PREVIOUS}
    controller.public_race = lambda: controller.s3.seed(refresh.ROUTE_KEY, raw_json(changed))
    with pytest.raises(RuntimeError, match="refusing compensating rollback"):
        slots.complete_promotion(
            state, "fixture-plan", before, target, written, {"test_only": True}
        )
    assert controller.route()[0] == changed


@pytest.mark.parametrize("publication_owner", [None, "execution-one"])
def test_public_transport_identity_and_canary_primitive_uses_real_http(
    controller, tmp_path, publication_owner, pinned_adapter
):
    slots = slot_service(controller, tmp_path)
    controller.tantivy_adapter = lambda: pinned_adapter
    serving = {**TANTIVY, "origin": f"https://{APPS[0]}.fly.dev", "build_id": "b1792999999-old"}
    before = {"version": 1, "current": serving}
    controller.s3.seed(refresh.ROUTE_KEY, raw_json(before))
    _, etag = controller.route()
    admission = {"bundle_sha256": "0" * 64}
    candidate = {
        **TANTIVY,
        "origin": f"https://{APPS[1]}.fly.dev",
        "admission_sha256": pinned_adapter.installer.digest_json(admission),
    }
    target = {"version": 1, "current": candidate, "previous": serving}
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
        return old_get(TANTIVY["origin"] if base == candidate["origin"] else base, path)

    controller.get = get
    state = {
        "request_id": "f" * 32,
        "owner": OWNER,
        "execution_id": "execution-one",
        "app": APPS[0],
    }
    deployment_key = f"control/tantivy-slots/{APPS[1]}/{state['request_id']}.json"
    bound = {
        "provider_app": APPS[1],
        "active_build": candidate["build_id"],
        "request_id": state["request_id"],
        "image_digest": "registry.fly.io/fixture@sha256:" + "d" * 64,
    }
    deployment_raw = raw_json(bound)
    state.update(
        app=APPS[1],
        automatic_refresh=bool(publication_owner),
        deployment_key=deployment_key,
        deployment_sha256=hashlib.sha256(deployment_raw).hexdigest(),
        image_digest=bound["image_digest"],
    )
    controller.s3.seed(deployment_key, deployment_raw)
    controller.s3.seed(
        f"control/tantivy-admissions/{candidate['admission_sha256']}.json",
        raw_json(
            {
                "schema_version": 1,
                "admission": admission,
                "deployment_key": deployment_key,
                "deployment_sha256": state["deployment_sha256"],
            }
        ),
    )
    plan = {
        "schema_version": 1,
        "owner": OWNER,
        "request_id": state["request_id"],
        "test_only": False,
        "app": state["app"],
        "image_digest": state["image_digest"],
        "deployment_key": deployment_key,
        "deployment_sha256": state["deployment_sha256"],
        "expected_current_etag": etag,
        "expected_route": before,
        "candidate": candidate,
    }
    plan_raw = raw_json(plan)
    plan_sha = hashlib.sha256(plan_raw).hexdigest()
    controller.s3.seed(f"control/tantivy-plans/{plan_sha}.json", plan_raw)
    state.update(
        plan_sha256=plan_sha,
        promotion_before=before,
        promotion_target=target,
        promotion_written_etag=written,
    )
    if publication_owner:
        state["publication_execution_id"] = publication_owner
    try:
        result = slots.complete_promotion(
            state, plan_sha, before, target, written, {"test_only": True}
        )
        assert result["public_verified"] is True
        assert result["release_proof"]["test_only"] is True
        assert result["publication_execution_id"] == publication_owner
        assert controller.route()[0] == target
        if publication_owner:
            from mps.typeahead_tantivy_automatic import refresh as automatic_refresh

            from tests.test_typeahead_engine_compatibility import seed_checkpoint_publication

            seed_checkpoint_publication(controller, candidate)
            slots.require_release = lambda *_: {"test_only": True}
            slots.retirement_freshness = lambda *_: False
            slots.qualify = lambda *_: ({"route": candidate, **controller.healthy(candidate)}, {})
            before_retry = controller.route()

            def forbidden_publication():
                raise AssertionError("Completed automatic promotion must not publish/install again")

            resumed = automatic_refresh(
                controller,
                slots,
                forbidden_publication,
                release_key="fixture-boundary",
                release_sha256="e" * 64,
            )
            assert (
                resumed["public_verified"]
                and resumed["publication_execution_id"] == publication_owner
            )
            assert controller.route() == before_retry

            controller.execution_identity = lambda: "execution-two"
            with pytest.raises(AssertionError, match="must not publish/install again"):
                automatic_refresh(
                    controller,
                    slots,
                    forbidden_publication,
                    release_key="fixture-boundary",
                    release_sha256="e" * 64,
                )
            controller.execution_identity = lambda: "execution-one"
            changed = {**target, "previous": {**serving, "build_id": "b1793000001-operator"}}
            controller._put_route(changed, before_retry[1])
            with pytest.raises(RuntimeError, match="Routing changed after confirmed promotion"):
                automatic_refresh(
                    controller,
                    slots,
                    forbidden_publication,
                    release_key="fixture-boundary",
                    release_sha256="e" * 64,
                )
            assert controller.route()[0] == changed
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
    initial = {"version": 1, "current": TANTIVY, "previous": None}
    controller.s3.seed(refresh.ROUTE_KEY, raw_json(initial))
    counts = {"health": 0, "candidate": 0}
    timeout_errors = []
    identity = {
        "X-Typeahead-Engine": "tantivy",
        "X-Typeahead-Build": TANTIVY["build_id"],
        "X-Typeahead-Format": TANTIVY["format"],
        "X-Typeahead-Binary-Sha256": TANTIVY["binary_sha256"],
        "X-Typeahead-Retrieval-Strategy": TANTIVY["retrieval_strategy"],
    }

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
                                    "current": {**TANTIVY, "build_id": "b1792908655-changed"},
                                }
                            ),
                        )
                    time.sleep(0.15)
                value = {
                    "ready": True,
                    "sync_lag_s": 0,
                    "engine": "tantivy",
                    "build_id": TANTIVY["build_id"],
                }
            elif self.path == "/index/status":
                value = {**TANTIVY, "ready": True, "snapshot_watermark": 1792950000}
            elif self.path == "/health/freshness":
                value = {"ok": True, "probe_ok": True, "build_id": TANTIVY["build_id"]}
            elif self.path == "/candidate":
                counts["candidate"] += 1
                value = {"qualified": True}
            else:
                self.send_error(404)
                return
            raw = raw_json(value)
            self.send_response(200)
            for key, header in identity.items():
                self.send_header(key, header)
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
    initial = {"version": 1, "current": TANTIVY, "previous": None}
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
    controller.s3.seed(refresh.ROUTE_KEY, raw_json({**leased, "previous": PREVIOUS}))
    with pytest.raises(RuntimeError, match="Routing changed"):
        slots.guard(state, serving_health=False)


@pytest.mark.parametrize(
    "behavior", ["recover", "resume", "rejected", "unknown", "route-change", "config-change"]
)
def test_start_reconciles_rejected_request_over_provider_http(
    controller, tmp_path, pinned_adapter, monkeypatch, behavior
):
    slots = slot_service(controller, tmp_path)
    controller.tantivy_adapter = lambda: pinned_adapter
    route, _ = controller.route()
    state = {
        "request_id": "e" * 32,
        "owner": OWNER,
        "execution_id": "execution-one",
        "app": APPS[0],
        "route": route,
        "machine_id": "abcdef12345678",
        "volume_id": "vol_fixture",
        "deployment_sha256": "a" * 64,
        "image_digest": "registry.fly.io/fixture@sha256:" + "b" * 64,
        "config": {"env": {}, "init": {}},
    }
    config = {
        "metadata": {
            "owner": OWNER,
            "role": "tantivy-slot",
            "request_id": state["request_id"],
            "deployment_sha256": state["deployment_sha256"],
        },
        "image": state["image_digest"],
        "mounts": [{"volume": "vol_fixture", "path": "/data"}],
        "guest": {"cpu_kind": "shared", "cpus": 2, "memory_mb": 1024},
        "env": {},
        "init": {},
    }
    machine = {"id": state["machine_id"], "state": "stopped", "config": config}
    starts = []
    slots.acquire(state)
    if behavior == "resume":
        state["pending_action"] = {"name": "start", "error": "HTTP 412"}
        slots.save(state, "start-unconfirmed")
        state = slots.journal(state["request_id"])

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.respond(200, machine)

        def do_POST(self):
            assert self.path.endswith("/start")
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            starts.append(self.path)
            if behavior == "unknown":
                self.respond(500, {})
            elif behavior == "rejected" or (behavior != "resume" and len(starts) == 1):
                if behavior == "route-change":
                    current, _ = controller.route()
                    controller.s3.seed(
                        refresh.ROUTE_KEY, raw_json({**current, "previous": PREVIOUS})
                    )
                if behavior == "config-change":
                    machine["config"]["guest"]["memory_mb"] = 2048
                self.respond(412, {})
            else:
                machine["state"] = "started"
                self.respond(200, machine)

        def respond(self, status, value):
            raw = raw_json(value)
            self.send_response(status)
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
    try:
        if behavior in ("recover", "resume"):
            assert slots.start(state)["state"] == "started"
            assert len(starts) == (1 if behavior == "resume" else 2)
            assert state["pending_action"] is None
        else:
            expected = {
                "rejected": "three attempts",
                "unknown": "HTTP 500",
                "route-change": "Routing changed",
                "config-change": "configuration changed",
            }
            with pytest.raises(RuntimeError, match=expected[behavior]):
                slots.start(state)
            assert len(starts) == (3 if behavior == "rejected" else 1)
        assert all(path == f"/{APPS[0]}/machines/{state['machine_id']}/start" for path in starts)
        assert controller.route()[0]["current"] == route["current"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class _SequencedRuntime:
    """healthy() returns successive statuses; validate_runtime raises what each one carries."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.reads = 0
        self.installer = self

    def healthy(self, _target):
        self.reads += 1
        return {"index": {"outcome": self.outcomes[min(self.reads, len(self.outcomes)) - 1]}}

    def tantivy_adapter(self):
        return self

    def validate_runtime(self, status, _manifest, *_args):
        if status["outcome"] is not None:
            raise ValueError(status["outcome"])


def _settling_slots(tmp_path, monkeypatch, outcomes, settle=120):
    from mps import typeahead_tantivy_slots as slots_module

    monkeypatch.setattr(slots_module, "POLL_SETTLE_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(slots_module, "POLL_SETTLE_SECONDS", settle)
    runtime = _SequencedRuntime(outcomes)
    return Slots(runtime, tmp_path / "journals", timeout_seconds=2), runtime


def test_retirement_freshness_waits_for_a_completed_poll_cycle(tmp_path, monkeypatch):
    from mps.typeahead_tantivy_slots import POLL_CYCLE_INCOMPLETE

    slots, runtime = _settling_slots(
        tmp_path, monkeypatch, [POLL_CYCLE_INCOMPLETE, POLL_CYCLE_INCOMPLETE, None]
    )
    assert slots.retirement_freshness({}, {}) == {"outcome": None}
    assert runtime.reads == 3


def test_retirement_freshness_fails_other_gates_immediately(tmp_path, monkeypatch):
    slots, runtime = _settling_slots(tmp_path, monkeypatch, ["Source polling is not fresh", None])
    with pytest.raises(ValueError, match="Source polling is not fresh"):
        slots.retirement_freshness({}, {})
    assert runtime.reads == 1


def test_retirement_freshness_gives_up_on_a_cycle_that_never_completes(tmp_path, monkeypatch):
    from mps.typeahead_tantivy_slots import POLL_CYCLE_INCOMPLETE

    slots, runtime = _settling_slots(tmp_path, monkeypatch, [POLL_CYCLE_INCOMPLETE], settle=0)
    with pytest.raises(ValueError, match="actually completed remote poll cycle"):
        slots.retirement_freshness({}, {})
    assert runtime.reads == 1


def _waiting_slots(tmp_path, monkeypatch, guard_outcomes):
    from mps import typeahead_tantivy_slots as slots_module

    monkeypatch.setattr(slots_module.time, "sleep", lambda _s: None)
    slots = Slots(object(), tmp_path / "journals", timeout_seconds=2)
    outcomes = list(guard_outcomes)
    calls = {"guard": 0, "qualify": 0}

    def guard(_state):
        calls["guard"] += 1
        outcome = outcomes[min(calls["guard"], len(outcomes)) - 1]
        if outcome is not None:
            raise outcome

    def qualify(_state, _bound):
        calls["qualify"] += 1
        return "qualified", "provider"

    slots.guard = guard
    slots.qualify = qualify
    return slots, calls


def test_wait_qualified_rides_out_a_serving_slot_that_briefly_reads_not_ready(
    tmp_path, monkeypatch
):
    from mps.typeahead_refresh import BackendNotReady

    slots, calls = _waiting_slots(
        tmp_path, monkeypatch, [BackendNotReady("Backend is not fresh and ready"), None]
    )
    assert slots.wait_qualified({}, {}) == ("qualified", "provider")
    assert calls == {"guard": 2, "qualify": 1}


def test_wait_qualified_still_stops_when_routing_changes(tmp_path, monkeypatch):
    slots, calls = _waiting_slots(
        tmp_path,
        monkeypatch,
        [RuntimeError("Routing changed; same preparation intent must be reconciled")],
    )
    with pytest.raises(RuntimeError, match="Routing changed"):
        slots.wait_qualified({}, {})
    assert calls["qualify"] == 0


def test_coverage_stale_is_tolerated_only_past_the_installers_own_deadline(pinned_adapter):
    import copy
    import importlib.util
    import os
    from pathlib import Path

    from mps.typeahead_tantivy_slots import COVERAGE_STALE, coverage_stale

    spec = importlib.util.spec_from_file_location(
        "typeahead_coverage_fixture",
        Path(os.environ["TA_REPOSITORY"]) / "scripts/snapshot_refresh/test_tantivy_install.py",
    )
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    manifest, status, now = fixtures.AdmissionTests().checkpoint_status()
    installer = pinned_adapter.installer
    fixture = {"allow_checkpoint_fixture": True}
    assert coverage_stale(installer, status, manifest, now, **fixture) is False

    later = now + 90000
    polled = copy.deepcopy(status)
    polled["watermark"] = polled["source_cursor"]["through"] = later - 5
    polled["source_cursor"]["actors"]["updated_at"] = later - 5
    polled["source_cursor"]["tombstones"]["deleted_at"] = later - 5
    polled["moderation"]["source_observed_at"] = later - 5
    with pytest.raises(ValueError, match="coverage or actual remote polling is stale"):
        installer.validate_runtime(polled, manifest, later, **fixture)
    assert coverage_stale(installer, polled, manifest, later, **fixture) is True

    unpolled = copy.deepcopy(status)
    with pytest.raises(ValueError, match=COVERAGE_STALE):
        coverage_stale(installer, unpolled, manifest, now + 200, **fixture)

    miscounted = copy.deepcopy(polled)
    miscounted["input_actors"] = 3
    with pytest.raises(ValueError, match="count mismatch"):
        coverage_stale(installer, miscounted, manifest, later, **fixture)
    with pytest.raises(ValueError, match="Fixture checkpoint"):
        coverage_stale(installer, polled, manifest, later)
