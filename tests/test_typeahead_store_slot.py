"""A store slot in the routing record stops every path that could promote."""

import hashlib
import json

import pytest
from mps import typeahead_refresh, typeahead_tantivy_automatic, typeahead_tantivy_slots
from mps.typeahead_refresh import ROUTE_KEY, STORE_SLOT_ORIGINS, StoreSlotRouted
from mps.typeahead_tantivy_slots import APPS, OWNER, raw_json

from tests.test_typeahead_engine_compatibility import (
    TANTIVY,
    controller as controller,
    seed_stub_publication,
)
from tests.test_typeahead_tantivy_slots import slot_service

IMAGE = "registry.fly.io/fixture@sha256:" + "d" * 64
PROOF = {"release_key": "test-only-authorization-boundary", "release_sha256": "e" * 64}
FIELDS = (
    "engine",
    "origin",
    "build_id",
    "format",
    "admission_sha256",
    "binary_sha256",
    "retrieval_strategy",
)
SLOT_A = {**{key: TANTIVY[key] for key in FIELDS}, "origin": f"https://{APPS[0]}.fly.dev"}
SLOT_B = {**{key: TANTIVY[key] for key in FIELDS}, "origin": f"https://{APPS[1]}.fly.dev"}
OLD_A = {**SLOT_A, "build_id": "b1793000000-old-a", "admission_sha256": "1" * 64}
SERVING_B = {**SLOT_B, "build_id": "b1793100000-serving-b", "admission_sha256": "2" * 64}
MANAGED = {"version": 1, "current": SERVING_B, "previous": OLD_A}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def route_object(controller):
    result = controller.s3.get_object(Bucket="fixture", Key=ROUTE_KEY)
    return result["Body"].read(), result["ETag"]


def cut_over(controller, prior=MANAGED):
    """Write what store_slot_route.py cutover writes: its admission, then the record."""
    identity = {
        "engine": "tantivy",
        "origin": STORE_SLOT_ORIGINS[0],
        "build_id": "b1793050000-store-bootstrap",
        "format": TANTIVY["format"],
        "binary_sha256": "c" * 64,
        "retrieval_strategy": TANTIVY["retrieval_strategy"],
    }
    admission = {
        "schema_version": 1,
        "kind": "store-slot-route-admission",
        "admitted_at": 1793200000,
        "target": identity,
        "gates": {"sync_lag_s": 4},
        "prior_route": prior,
    }
    digest = hashlib.sha256(canonical(admission)).hexdigest()
    controller.s3.seed(
        f"control/store-slot-admissions/{digest}.json",
        canonical(admission),
    )
    record = {
        "version": 1,
        "current": {**identity, "admission_sha256": digest},
        "previous": prior["current"],
    }
    controller.s3.seed(ROUTE_KEY, json.dumps(record, separators=(",", ":")).encode())
    return record


def test_promoting_paths_refuse_a_store_slot_before_any_export_or_provider_call(
    controller, tmp_path
):
    slots = slot_service(controller, tmp_path)
    cut_over(controller)
    seed_stub_publication(controller, {**SLOT_A, "build_id": "b1793300000-next"})
    before = route_object(controller)
    calls = []
    with pytest.raises(StoreSlotRouted, match="only the standby refresh may run"):
        typeahead_tantivy_automatic.refresh(
            controller, slots, lambda: calls.append("publish"), **PROOF
        )
    for operation in (
        controller.route,
        controller.prune_artifacts,
        controller.retained_build_ids,
        lambda: slots.prepare("b1793300000-next", IMAGE),
        lambda: slots.guard({"app": APPS[0], "route": MANAGED}),
        lambda: controller._put_route(MANAGED, before[1]),
    ):
        with pytest.raises(StoreSlotRouted):
            operation()
    assert calls == [] and route_object(controller) == before
    assert controller.routed_to_store_slot() is True


def test_a_managed_route_reads_as_it_did(controller):
    controller.s3.seed(ROUTE_KEY, raw_json(MANAGED))
    etag = route_object(controller)[1]
    assert controller.routed_to_store_slot() is False
    assert controller.route() == (MANAGED, etag)
    written = controller._put_route({"version": 1, "current": SERVING_B}, etag)
    assert route_object(controller) == (
        b'{"version":1,"current":' + json.dumps(SERVING_B, separators=(",", ":")).encode() + b"}",
        written,
    )


def test_a_store_slot_added_to_the_slot_lists_still_cannot_be_promoted(
    controller, tmp_path, monkeypatch
):
    slots = slot_service(controller, tmp_path)
    shadow_app = STORE_SLOT_ORIGINS[0][8:-8]
    monkeypatch.setattr(typeahead_refresh, "TANTIVY_SLOTS", (shadow_app, APPS[1]))
    monkeypatch.setattr(typeahead_tantivy_slots, "APPS", (shadow_app, APPS[1]))
    monkeypatch.setenv("FLY_TANTIVY_A_TOKEN", "fixture-token")
    controller.s3.seed(ROUTE_KEY, raw_json({"version": 1, "current": SERVING_B}))
    route, etag = controller.route()
    before = route_object(controller)
    shadow = {**SLOT_A, "origin": STORE_SLOT_ORIGINS[0]}

    with pytest.raises(StoreSlotRouted):
        typeahead_refresh.route_target({"version": 1, "current": shadow})
    with pytest.raises(StoreSlotRouted):
        controller._put_route({"version": 1, "current": shadow, "previous": SERVING_B}, etag)
    with pytest.raises(ValueError, match="approved inactive Tantivy app"):
        slots.guard({"app": shadow_app, "route": route}, serving_health=False)
    slots.artifact = lambda build_id: ({"source_actor_count": 4}, b"{}")
    with pytest.raises(ValueError, match="approved inactive Tantivy app"):
        slots.prepare("b1793300000-next", IMAGE, app=shadow_app)
    with pytest.raises(ValueError, match="Unmanaged provider app"):
        typeahead_refresh.Refresh.fly(controller, "GET", shadow_app, "machines")
    with pytest.raises(ValueError, match="Invalid inactive mutation lease"):
        typeahead_tantivy_slots.validate_mutation(
            {"owner": OWNER, "request_id": "a" * 32, "app": shadow_app, "acquired_at": 1}
        )
    assert route_object(controller) == before
