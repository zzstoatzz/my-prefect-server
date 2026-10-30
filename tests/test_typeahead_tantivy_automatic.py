"""Post-authorization orchestration uses real HTTP and filesystem-backed R2.

These failure tests enter the continuation below the release-proof gate. They do
not manufacture a positive production proof or perform provider mutations.
"""

import json

import pytest
from mps.typeahead_tantivy_automatic import _continue_refresh, refresh
from mps.typeahead_tantivy_slots import APPS, OWNER, raw_json

from tests.test_typeahead_engine_compatibility import (
    NATIVE,
    TANTIVY,
    controller as controller,
    pinned_adapter as pinned_adapter,
    seed_checkpoint_publication,
)
from tests.test_typeahead_tantivy_slots import slot_service

IMAGE = "registry.fly.io/fixture@sha256:" + "d" * 64
PROOF = {"release_key": "test-only-authorization-boundary", "release_sha256": "e" * 64}


def active(controller, tmp_path):
    slots = slot_service(controller, tmp_path)
    current = {**TANTIVY, "origin": f"https://{APPS[0]}.fly.dev"}
    route = {"version": 1, "current": current, "previous": NATIVE, "native_rollback": NATIVE}
    controller.s3.seed("control/search-routing.json", raw_json(route))
    original_get = controller.get
    controller.get = lambda base, path: original_get(
        TANTIVY["origin"] if base == current["origin"] else base, path
    )
    assert controller.healthy(current)["health"]["ready"] is True
    return slots, controller.route()


def test_native_route_and_missing_real_release_proof_refuse_before_publication(
    controller, tmp_path
):
    slots = slot_service(controller, tmp_path)
    calls = []
    controller.s3.seed("control/search-routing.json", raw_json({"version": 1, "current": NATIVE}))
    with pytest.raises(RuntimeError, match="initial native-to-Tantivy"):
        refresh(controller, slots, lambda: calls.append("build"), **PROOF)
    controller.s3.seed("control/search-routing.json", raw_json({"version": 1, "current": TANTIVY}))
    with pytest.raises(ValueError, match="release proof is missing"):
        refresh(controller, slots, lambda: calls.append("build"), **PROOF)
    assert calls == [] and controller.s3.writes == []


def test_native_rollback_during_publication_wins_and_artifact_is_retained(controller, tmp_path):
    slots, (route, etag) = active(controller, tmp_path)
    artifact = tmp_path / "immutable-publication.json"
    rolled_back = {
        "version": 1,
        "current": NATIVE,
        "previous": route["current"],
        "native_rollback": NATIVE,
    }

    def publish():
        artifact.write_bytes(raw_json({"test_only": True, "build_id": "b1793300000-next"}))
        controller._put_route(rolled_back, etag)
        return "b1793300000-next"

    with pytest.raises(RuntimeError, match="Routing changed during automatic publication"):
        _continue_refresh(controller, slots, publish, route, etag, IMAGE, **PROOF)
    assert controller.route()[0] == rolled_back
    assert artifact.is_file()
    assert not list(controller.s3.root.glob("control/tantivy-slots/*/pending.json"))


@pytest.mark.parametrize("automatic", [True, False])
def test_pending_candidate_is_resolved_before_any_bulk_build(controller, tmp_path, automatic):
    slots, (route, etag) = active(controller, tmp_path)
    state = {
        "schema_version": 1,
        "owner": OWNER,
        "execution_id": "execution-one",
        "request_id": "b" * 32,
        "app": APPS[1],
        "build_id": "b1793300000-pending",
        "source_build_id": "b1793290000-source",
        "legacy_build_id": "b1793290000-native",
        "image_digest": IMAGE,
        "artifact_manifest_sha256": "a" * 64,
        "route": route,
        "automatic_refresh": automatic,
        "retire_previous_tantivy": True,
    }
    slots.save(state, "planned")
    slots.protect(state)

    class ResumeBoundary(RuntimeError):
        pass

    def prepare(build_id, image, **kwargs):
        assert build_id == state["build_id"] and image == IMAGE
        assert kwargs["request_id"] == state["request_id"] and kwargs["app"] == APPS[1]
        assert kwargs["expected_route"] == route and kwargs["expected_etag"] == etag
        assert kwargs["automatic_refresh"] is True
        raise ResumeBoundary("Reached existing provider resume boundary")

    slots.prepare = prepare

    def forbidden_build():
        raise AssertionError("Pending refresh must not start another source export")

    if automatic:
        with pytest.raises(ResumeBoundary):
            _continue_refresh(controller, slots, forbidden_build, route, etag, IMAGE, **PROOF)
    else:
        with pytest.raises(RuntimeError, match="Manual pending preparation"):
            _continue_refresh(controller, slots, forbidden_build, route, etag, IMAGE, **PROOF)
    assert controller.route() == (route, etag)
    assert (
        json.loads(slots.read(f"control/tantivy-slots/{APPS[1]}/pending.json"))["request_id"]
        == state["request_id"]
    )


def test_recovery_without_pending_operation_never_starts_new_export(controller, tmp_path):
    slots, (route, etag) = active(controller, tmp_path)

    def forbidden_build():
        raise AssertionError("Recovery cannot start a new export")

    with pytest.raises(RuntimeError, match="recovery cannot start a build"):
        _continue_refresh(
            controller, slots, forbidden_build, route, etag, IMAGE, resume_only=True, **PROOF
        )
    assert controller.route() == (route, etag)


def test_prepare_route_fence_checks_actual_head_before_any_machine_action(
    controller, tmp_path, pinned_adapter
):
    slots, (route, etag) = active(controller, tmp_path)
    controller.tantivy_adapter = lambda: pinned_adapter
    manifest, _ = seed_checkpoint_publication(controller, TANTIVY)
    changed = {
        "version": 1,
        "current": NATIVE,
        "previous": route["current"],
        "native_rollback": NATIVE,
    }
    controller._put_route(changed, etag)
    with pytest.raises(RuntimeError, match="route ETag changed"):
        slots.prepare(
            manifest["active_build"],
            IMAGE,
            expected_route=route,
            expected_etag=etag,
            automatic_refresh=True,
        )
    assert controller.route()[0] == changed
    assert not list(controller.s3.root.glob("control/tantivy-slots/*/pending.json"))


def test_initial_promotion_requires_fresh_native_anchor_over_actual_http(controller, tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from mps.typeahead_refresh import Refresh

    slots, (route, etag) = active(controller, tmp_path)
    requested = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requested.append(self.path)
            raw = raw_json({"ready": True, "sync_lag_s": 601})
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    old_get = controller.get
    controller.get = lambda base, path: (
        Refresh.get(controller, f"http://127.0.0.1:{server.server_port}", path)
        if base == NATIVE["origin"]
        else old_get(base, path)
    )
    plan = {
        "test_only": False,
        "expected_current_etag": etag,
        "expected_route": route,
        "native_rollback": NATIVE,
        "candidate": {**TANTIVY, "origin": f"https://{APPS[1]}.fly.dev"},
    }
    # A negative freshness test below the separately-tested proof gate; no release
    # proof is created, published or accepted as production evidence.
    slots.load_plan = lambda _: (plan, {})
    slots.require_release = lambda *_: {"test_only": True}
    try:
        with pytest.raises(RuntimeError, match="not fresh and ready"):
            slots.promote("fixture-plan", etag, "fixture-proof", "e" * 64)
        assert requested == ["/health"]
        assert controller.route() == (route, etag)
        assert controller.s3.writes == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_automatic_dispatch_rejects_conflicting_source_ref_before_checkout(controller, monkeypatch):
    import mps.typeahead_refresh as owner

    import flows.typeahead_index as flow

    monkeypatch.setattr(owner, "Refresh", lambda _: controller)
    monkeypatch.setattr(flow, "get_run_logger", lambda: controller.logger)
    for name, value in {
        "INDEX_TANTIVY_SOURCE_REVISION": "1" * 40,
        "INDEX_TANTIVY_ADAPTER_SHA256": "2" * 64,
        "INDEX_TANTIVY_RELEASE_RECEIPT_KEY": "fixture-only-proof",
        "INDEX_TANTIVY_RELEASE_RECEIPT_SHA256": "3" * 64,
    }.items():
        monkeypatch.setenv(name, value)

    def forbidden(*_, **__):
        raise AssertionError("Conflicting ref cannot begin checkout/publication/retirement")

    for name in [
        "clone_repo",
        "prepare_checkpoint_generation",
        "publish_tantivy_generation",
        "retire_tantivy_generation",
    ]:
        monkeypatch.setattr(flow, name, forbidden)
    with pytest.raises(ValueError, match="ref conflicts"):
        flow.typeahead_index.fn(
            snapshot_format="compact", automatic_refresh=True, ref="mutable-branch"
        )
    assert controller.s3.writes == [] and controller.s3.deletions == []


def test_existing_native_automatic_path_does_not_require_tantivy_proof(controller, monkeypatch):
    import mps.typeahead_refresh as owner
    import mps.typeahead_tantivy_automatic as automatic

    import flows.typeahead_index as flow

    controller.s3.seed("control/search-routing.json", raw_json({"version": 1, "current": NATIVE}))
    monkeypatch.setattr(owner, "Refresh", lambda _: controller)
    monkeypatch.setattr(flow, "get_run_logger", lambda: controller.logger)
    monkeypatch.setattr(flow, "prune_builds", lambda *_: None)

    class NativeBuildBoundary(RuntimeError):
        pass

    def native_build():
        raise NativeBuildBoundary("Reached existing native build path")

    monkeypatch.setattr(flow, "require_build_space", native_build)
    monkeypatch.setattr(
        automatic, "settings", lambda _: pytest.fail("Native does not require Tantivy settings")
    )
    with pytest.raises(NativeBuildBoundary):
        flow.typeahead_index.fn(
            snapshot_format="compact", automatic_refresh=True, ref="existing-native-ref"
        )
    assert controller.s3.writes == [] and controller.s3.deletions == []


@pytest.mark.parametrize("drift", [None, "guest", "volume"])
def test_release_binding_observes_actual_provider_resources(
    controller, tmp_path, monkeypatch, drift
):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from types import MethodType

    from mps.typeahead_refresh import Refresh

    slots = slot_service(controller, tmp_path)
    bound = {
        "provider_app": APPS[0],
        "image_digest": IMAGE,
        "volume_id": "vol_owned",
        "binary_sha256": "b" * 64,
        "retrieval_strategy": "global-merge",
        "canonical_rules_source_sha256": "c" * 64,
    }
    enrollment = {"machine_id": "abcdef12345678", "deployment_sha256": "d" * 64}
    slots.admission_record = lambda _: (enrollment, bound)
    requested = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requested.append(self.path)
            if "/machines/" in self.path:
                value = {
                    "config": {
                        "image": IMAGE,
                        "guest": {
                            "cpu_kind": "shared",
                            "cpus": 2,
                            "memory_mb": 2048 if drift == "guest" else 1024,
                        },
                        "mounts": [
                            {
                                "volume": "vol_owned",
                                "path": "/data",
                                "encrypted": True,
                                "size_gb": 20,
                            }
                        ],
                        "metadata": {"owner": OWNER, "role": "tantivy-slot"},
                        "env": {"EXPECTED_DEPLOYMENT_SHA256": enrollment["deployment_sha256"]},
                    }
                }
            else:
                value = {
                    "id": "vol_owned",
                    "size_gb": 21 if drift == "volume" else 20,
                    "encrypted": True,
                }
            raw = raw_json(value)
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    controller.fly = MethodType(Refresh.fly, controller)
    controller._fly_url = lambda app, path: f"http://127.0.0.1:{server.server_port}/{app}/{path}"
    monkeypatch.setenv("FLY_TANTIVY_A_TOKEN", "fixture-token")
    try:
        if drift:
            with pytest.raises(RuntimeError, match="provider"):
                slots.release_binding(TANTIVY)
        else:
            assert slots.release_binding(TANTIVY)["guest"]["memory_mb"] == 1024
        assert len(requested) == (1 if drift == "guest" else 2)
        assert controller.s3.writes == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_explicit_availability_rollback_is_not_intercepted_by_automatic_dispatch(monkeypatch):
    import mps.typeahead_refresh as owner

    import flows.typeahead_index as flow

    def forbidden(_):
        raise AssertionError("Explicit rollback must use its existing availability owner")

    monkeypatch.setattr(owner, "Refresh", forbidden)
    monkeypatch.setattr(owner, "rollback_snapshot", lambda: {"freshness": {"stale": True}})
    state = flow.typeahead_index.fn(
        snapshot_format="compact", automatic_refresh=True, rollback=True
    )
    assert state.name == "Degraded"
