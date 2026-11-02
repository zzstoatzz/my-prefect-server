"""Post-authorization orchestration uses real HTTP and filesystem-backed R2.

These failure tests enter the continuation below the release-proof gate. They do
not manufacture a positive production proof or perform provider mutations.
"""

import hashlib
import json

import pytest
from mps import typeahead_tantivy_automatic
from mps.typeahead_refresh import Refresh
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
    controller.s3.seed("control/search-routing.json", raw_json({"version": 1, "current": current}))
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
    with pytest.raises(ValueError, match="Tantivy route identity"):
        refresh(controller, slots, lambda: calls.append("build"), **PROOF)
    controller.s3.seed("control/search-routing.json", raw_json({"version": 1, "current": TANTIVY}))
    with pytest.raises(ValueError, match="release proof is missing"):
        refresh(controller, slots, lambda: calls.append("build"), **PROOF)
    assert calls == [] and controller.s3.writes == []


def unreachable_current(controller, tmp_path, monkeypatch, *, recover_after=None):
    monkeypatch.setattr(typeahead_tantivy_automatic, "UNAVAILABLE_PROBE_INTERVAL_SECONDS", 0)
    slots = slot_service(controller, tmp_path)
    current = {**TANTIVY, "origin": f"https://{APPS[0]}.fly.dev"}
    controller.s3.seed("control/search-routing.json", raw_json({"version": 1, "current": current}))
    probes = []
    original_get = controller.get

    def get(base, path):
        if base != current["origin"]:
            return original_get(base, path)
        probes.append(path)
        if recover_after is not None and len(probes) > recover_after:
            return original_get(TANTIVY["origin"], path)
        return Refresh.get(controller, "http://127.0.0.1:9", path)

    controller.get = get
    return slots, current, probes


def test_unreachable_current_slot_continues_into_the_inactive_slot(
    controller, tmp_path, monkeypatch
):
    slots, current, probes = unreachable_current(controller, tmp_path, monkeypatch)
    route, etag = controller.route()
    builds = []

    class PrepareBoundary(RuntimeError):
        pass

    def prepare(build_id, image, **kwargs):
        assert build_id == "b1793300000-next" and image == IMAGE
        assert kwargs["app"] == APPS[1] and kwargs["serving_unavailable"] is True
        assert kwargs["expected_route"] == route and kwargs["expected_etag"] == etag
        raise PrepareBoundary("Reached inactive slot preparation")

    slots.prepare = prepare
    with pytest.raises(PrepareBoundary):
        _continue_refresh(
            controller,
            slots,
            lambda: builds.append("build") or "b1793300000-next",
            route,
            etag,
            IMAGE,
            serving_unavailable=True,
            **PROOF,
        )
    assert builds == ["build"]
    assert controller.route() == (route, etag)
    with pytest.raises(ValueError, match="release proof is missing"):
        refresh(controller, slots, lambda: None, **PROOF)
    assert probes == ["/health"] * typeahead_tantivy_automatic.UNAVAILABLE_PROBES
    assert controller.route()[0]["current"] == current


@pytest.mark.parametrize("candidate_actors, reached", [(4, True), (2, False)])
def test_preparation_past_a_dead_serving_slot_uses_the_admitted_artifact_floor(
    controller, tmp_path, monkeypatch, candidate_actors, reached
):
    slots, current, probes = unreachable_current(controller, tmp_path, monkeypatch)
    serving = {"source_actor_count": 6}
    candidate = {
        "source_actor_count": candidate_actors,
        "source_build_id": "b1793300000-next",
        "legacy_build_id": "b1793290000-legacy",
        "source_manifest_sha256": "c" * 64,
    }
    artifacts = {current["build_id"]: serving, "b1793300000-next": candidate}
    slots.artifact = lambda build_id: (artifacts[build_id], raw_json(artifacts[build_id]))
    admitted = {"artifact_manifest_sha256": hashlib.sha256(raw_json(serving)).hexdigest()}
    slots.admission_record = lambda target: ({}, admitted)
    slots.protect = lambda state: None

    class AllocationBoundary(RuntimeError):
        pass

    def allocate(state, _manifest):
        assert state["serving_unavailable"] is True and state["app"] == APPS[1]
        assert "native_rollback" not in state
        assert controller.route()[0]["mutation"]["request_id"] == state["request_id"]
        raise AllocationBoundary("Reached inactive slot allocation")

    slots.allocate = allocate
    expected = AllocationBoundary if reached else ValueError
    with pytest.raises(expected, match=None if reached else "corpus floor"):
        slots.prepare(
            "b1793300000-next",
            IMAGE,
            request_id="f" * 32,
            automatic_refresh=True,
            serving_unavailable=True,
        )
    assert probes == []
    assert controller.route()[0]["current"] == current
    admitted["artifact_manifest_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="differs from its admitted deployment"):
        slots.admitted_actor_count(current)


def test_preparation_with_a_live_serving_slot_reads_its_live_floor(
    controller, tmp_path, monkeypatch
):
    import httpx

    slots, _, probes = unreachable_current(controller, tmp_path, monkeypatch)
    manifest = {"source_actor_count": 3}
    slots.artifact = lambda build_id: (manifest, raw_json(manifest))
    slots.admission_record = lambda target: pytest.fail("A live slot supplies its own floor")
    with pytest.raises(httpx.ConnectError):
        slots.prepare("b1793300000-next", IMAGE, request_id="f" * 32, automatic_refresh=True)
    assert probes == ["/health"]


def test_transiently_unreachable_current_slot_keeps_its_route(controller, tmp_path, monkeypatch):
    slots, current, probes = unreachable_current(controller, tmp_path, monkeypatch, recover_after=1)
    with pytest.raises(ValueError, match="release proof is missing"):
        refresh(controller, slots, lambda: None, **PROOF)
    assert probes[:2] == ["/health", "/health"]
    assert controller.route()[0]["current"] == current
    assert controller.s3.writes == []


def test_route_change_during_publication_wins_and_artifact_is_retained(controller, tmp_path):
    slots, (route, etag) = active(controller, tmp_path)
    artifact = tmp_path / "immutable-publication.json"
    changed = {"version": 1, "current": {**route["current"], "build_id": "b1793000001-operator"}}

    def publish():
        artifact.write_bytes(raw_json({"test_only": True, "build_id": "b1793300000-next"}))
        controller._put_route(dict(changed), etag)
        return "b1793300000-next"

    with pytest.raises(RuntimeError, match="Routing changed during automatic publication"):
        _continue_refresh(controller, slots, publish, route, etag, IMAGE, **PROOF)
    assert controller.route()[0] == changed
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
    changed = {"version": 1, "current": TANTIVY, "previous": route["current"]}
    controller._put_route(dict(changed), etag)
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


def test_promotion_writes_a_tantivy_only_route_and_reads_no_native_origin(controller, tmp_path):
    slots, (route, etag) = active(controller, tmp_path)
    candidate = {**TANTIVY, "origin": f"https://{APPS[1]}.fly.dev", "build_id": "b1793300000-next"}
    state = {
        "schema_version": 1,
        "owner": OWNER,
        "request_id": "c" * 32,
        "execution_id": "execution-one",
        "app": APPS[1],
        "route": route,
        "native_rollback": NATIVE,
        "deployment_key": "control/tantivy-slots/fixture.json",
    }
    plan = {
        "test_only": False,
        "expected_current_etag": etag,
        "expected_route": route,
        "native_rollback": NATIVE,
        "candidate": candidate,
    }
    # Below the separately tested proof and qualification gates; nothing here is
    # production evidence.
    slots.load_plan = lambda _: (plan, state)
    slots.require_release = lambda *_: {"test_only": True}
    slots.read = lambda key, maximum=0: raw_json({})
    slots.qualify = lambda state, bound: ({"route": candidate}, {})
    written = {}

    def complete(state, plan_sha256, before, target, etag, proof):
        written.update(target)
        return {"phase": "promoted", "route": target}

    slots.complete_promotion = complete
    slots.promote("fixture-plan", etag, "fixture-proof", "e" * 64)
    assert written == {"version": 1, "current": candidate, "previous": route["current"]}
    stored = json.loads(controller.s3.path("control/search-routing.json").read_bytes())
    assert stored == written
    assert "native_rollback" not in json.dumps(stored)


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


@pytest.mark.parametrize(
    "options, message",
    [
        ({"tantivy_operation": {"operation": "cancel"}, "automatic_refresh": True}, "combined"),
        ({"tantivy_operation": {"operation": "cancel"}, "resume_rollout": True}, "combined"),
        ({"resume_rollout": True}, "requires automatic refresh"),
    ],
)
def test_flow_rejects_conflicting_modes_before_checkout(monkeypatch, options, message):
    import flows.typeahead_index as flow

    monkeypatch.setattr(flow, "clone_repo", lambda *_: pytest.fail("No checkout may start"))
    with pytest.raises(ValueError, match=message):
        flow.typeahead_index.fn(**options)


@pytest.mark.parametrize("revision", [None, "main", "1" * 39])
def test_every_flow_mode_requires_an_immutable_source_revision(monkeypatch, revision):
    import flows.typeahead_index as flow

    if revision is None:
        monkeypatch.delenv("INDEX_TANTIVY_SOURCE_REVISION", raising=False)
    else:
        monkeypatch.setenv("INDEX_TANTIVY_SOURCE_REVISION", revision)
    monkeypatch.setattr(flow, "clone_repo", lambda *_: pytest.fail("No checkout may start"))
    for options in ({}, {"tantivy_operation": {"operation": "cancel"}}):
        with pytest.raises(ValueError, match="immutable Typeahead source revision"):
            flow.typeahead_index.fn(**options)
