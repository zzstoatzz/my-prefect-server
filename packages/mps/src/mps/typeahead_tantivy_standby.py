"""Keep the managed Tantivy slots rebuilt while a store slot holds the route.

Typeahead's `store_slot_route.py` routes search to a store slot by writing
`{current: store slot, previous: managed slot}`; `previous` is the slot its
rollback returns to. A standby refresh publishes a new generation, installs and
qualifies it on the managed slot that is not that rollback target, and then
repoints `previous` at it with one conditional write. `current` is copied byte
for byte, so this path cannot move traffic.

The slot lifecycle runs unchanged against the rollback route: the record that a
rollback would restore, `{current: rollback target, previous: the other slot}`.
Before the first standby refresh that is the prior route stored in the store
slot's admission. Afterwards it is the ledger this module writes under
`control/tantivy-standby/` before each record update, keyed by the new rollback
target's admission digest.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re

from mps.typeahead_refresh import (
    BUILD_PATTERN,
    OWNER,
    ROUTE_KEY,
    STORE_SLOT_ORIGINS,
    Refresh,
    names_store_slot,
    normalize_route,
    tantivy_identity,
    validate_route_record,
)
from mps.typeahead_tantivy_automatic import _continue_refresh, unavailable
from mps.typeahead_tantivy_slots import APPS, raw_json, validate_mutation

STORE_ADMISSION_PREFIX = "control/store-slot-admissions/"
STANDBY_PREFIX = "control/tantivy-standby/"
STANDBY_KIND = "standby-rollback-route"
# store_slot_route.py refuses a record whose targets carry any other field.
ROUTED_FIELDS = {
    "engine",
    "origin",
    "build_id",
    "format",
    "admission_sha256",
    "binary_sha256",
    "retrieval_strategy",
}
IDENTITY = ("origin", "build_id", "format", "binary_sha256", "retrieval_strategy")


class StandbyRouteChanged(RuntimeError):
    """The routing record is not, or is no longer, the store-slot record this run read."""


class StandbyWriteRefused(RuntimeError):
    """A standby refresh was asked to write something other than the rollback target."""


def canonical_digest(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def encode_record(record: dict) -> bytes:
    return json.dumps(record, separators=(",", ":")).encode()


def managed_origins() -> tuple[str, ...]:
    origins = (f"https://{app}.fly.dev" for app in APPS)
    return tuple(origin for origin in origins if origin not in STORE_SLOT_ORIGINS)


def routed(target, origins) -> dict:
    if not isinstance(target, dict) or set(target) != ROUTED_FIELDS:
        raise ValueError("Route target does not have exactly the routed fields")
    if (
        target["origin"] not in origins
        or not isinstance(target["build_id"], str)
        or not re.fullmatch(BUILD_PATTERN, target["build_id"])
        or not tantivy_identity(target)
    ):
        raise ValueError("Invalid Tantivy route identity")
    return target


class StandbyRefresh(Refresh):
    """Reads the rollback route as the route and can write only its `previous`."""

    # The public aliases answer from the store slot, so they say nothing about
    # the rollback target that slot preparation guards here.
    guards_public_identity = False
    _lease = None
    _rollback_routes = None

    def store_record(self):
        raw, etag = self._route_object()
        record = normalize_route(json.loads(raw))
        if not names_store_slot(record.get("current")):
            raise StandbyRouteChanged("Search is not routed to a store slot")
        if type(record.get("version")) is not int or record["version"] != 1:
            raise ValueError("Invalid routing record")
        if set(record) != {"version", "current", "previous"}:
            raise ValueError("Store-slot routing record has no rollback target or unknown fields")
        routed(record["current"], STORE_SLOT_ORIGINS)
        routed(record["previous"], managed_origins())
        if raw.count(b'"current":' + encode_record(record["current"])) != 1:
            raise StandbyWriteRefused("Serving target could not be copied byte for byte")
        return record, etag, raw

    def rollback_route(self, record: dict) -> dict:
        current, previous = record["current"], record["previous"]
        key = (current["admission_sha256"], previous["admission_sha256"])
        if self._rollback_routes is None:
            self._rollback_routes = {}
        if key not in self._rollback_routes:
            admission = self.optional_metadata(
                STORE_ADMISSION_PREFIX + current["admission_sha256"] + ".json", 1024 * 1024
            )
            if (
                not isinstance(admission, dict)
                or admission.get("kind") != "store-slot-route-admission"
                or canonical_digest(admission) != current["admission_sha256"]
                or admission.get("target")
                != {field: value for field, value in current.items() if field != "admission_sha256"}
                or not isinstance(admission.get("prior_route"), dict)
            ):
                raise ValueError("Store slot admission is missing or differs from the routed slot")
            route = normalize_route(admission["prior_route"])
            if route.get("current") != previous:
                ledger = self.optional_metadata(
                    STANDBY_PREFIX + previous["admission_sha256"] + ".json", 65536
                )
                if (
                    not isinstance(ledger, dict)
                    or ledger.get("schema_version") != 1
                    or ledger.get("owner") != OWNER
                    or ledger.get("kind") != STANDBY_KIND
                    or ledger.get("store_admission_sha256") != current["admission_sha256"]
                    or not isinstance(ledger.get("route"), dict)
                ):
                    raise ValueError("Recorded rollback target has no rollback route")
                route = ledger["route"]
            validate_route_record(route)
            if route.get("mutation") is not None or route["current"] != previous:
                raise ValueError("Rollback route does not restore the recorded rollback target")
            self._rollback_routes[key] = route
        return copy.deepcopy(self._rollback_routes[key])

    def route(self):
        record, etag, _ = self.store_record()
        route = self.rollback_route(record)
        if self._lease is None:
            return route, etag
        return {**route, "mutation": self._lease}, f"{etag};standby-lease"

    def _put_route(self, route: dict, etag: str, *, release_mutation=None):
        """Hold the inactive-mutation lease in this process; the record is not written.

        A lease in the record would block `store_slot_route.py rollback` for
        the length of a slot preparation and would outlive a failed run. The
        pending-candidate object and its journal already fence a second run.
        """
        before, actual_etag = self.route()
        if actual_etag != etag:
            raise RuntimeError("Routing changed before conditional update")
        lease = route.get("mutation")
        if {key: value for key, value in route.items() if key != "mutation"} != {
            key: value for key, value in before.items() if key != "mutation"
        }:
            raise StandbyWriteRefused("A standby refresh cannot write a serving route")
        if release_mutation is not None:
            if lease is not None or release_mutation != self._lease:
                raise RuntimeError("Mutation release changed routing identity")
            self._lease = None
        elif lease is not None and self._lease is None:
            if before["current"]["origin"] == f"https://{validate_mutation(lease)['app']}.fly.dev":
                raise RuntimeError("Mutation acquisition cannot lease the rollback target")
            self._lease = copy.deepcopy(lease)
        else:
            raise StandbyWriteRefused("A standby refresh cannot write a serving route")
        return self.route()[1]

    def retained_build_ids(self, route: dict | None = None) -> set[str]:
        keep = super().retained_build_ids(route)
        keep.add(self.store_record()[0]["current"]["build_id"])
        return keep

    def record_rollback_target(self, candidate: dict, expected_etag: str):
        """Point `previous` at `candidate`, leaving the bytes of `current` as they were."""
        record, etag, raw = self.store_record()
        if etag != expected_etag:
            raise StandbyRouteChanged("Routing changed before the rollback target was recorded")
        routed(candidate, managed_origins())
        target = {"version": 1, "current": record["current"], "previous": candidate}
        body = encode_record(target)
        current = b'"current":' + encode_record(record["current"])
        if raw.count(current) != 1 or body.count(current) != 1:
            raise StandbyWriteRefused("Serving target would not be copied byte for byte")
        result = self.s3.put_object(
            Bucket=self.bucket,
            Key=ROUTE_KEY,
            Body=body,
            ContentType="application/json",
            IfMatch=etag,
        )
        return target, result["ETag"]


def _settle_landed_promotion(slots, route):
    """Close a promotion whose route write landed before the cutover but was never confirmed."""
    app = route["current"]["origin"].removeprefix("https://").removesuffix(".fly.dev")
    raw = slots.read(f"control/tantivy-slots/{app}/pending.json", 4096)
    if raw is None:
        return
    state = slots.journal(json.loads(raw).get("request_id"))
    if (
        state is not None
        and state.get("app") == app
        and state.get("promotion_target") == route
        and state.get("phase") in ("promotion-cas-intent", "promotion-cas-confirmed")
    ):
        slots.save(state, "promoted")
        slots.unprotect(state)


def _report(plan_sha256, state, proof):
    return {
        "plan_sha256": plan_sha256,
        "phase": "standby-recorded",
        "route": state["standby_target"],
        "rollback_route": state["standby_rollback_route"],
        "release_proof": proof,
        "current_unchanged": state["standby_target"]["current"]
        == state["standby_before"]["current"],
        "publication_execution_id": state.get("publication_execution_id"),
    }


def _recorded(service, slots, deployment, record):
    """Finish, and report, a rollback-target write that this slot's own request made."""
    request_id = deployment.get("request_id")
    raw = slots.read(f"control/tantivy-operations/{request_id}.json", 65536) if request_id else None
    if raw is None:
        return None
    observed = json.loads(raw)
    if observed.get("request_id") != request_id or observed.get("owner") != OWNER:
        raise ValueError("Admitted standby rollout journal identity changed")
    if observed.get("standby_target") != record or observed.get("phase") not in (
        "prepared",
        "standby-recorded",
    ):
        return None
    state = slots.journal(request_id)
    if state["phase"] != "standby-recorded":
        slots.save(state, "standby-recorded")
    slots.unprotect(state)
    if state.get("publication_execution_id") != service.execution_identity():
        return None
    return _report(state["plan_sha256"], state, state.get("release_proof"))


def record_standby(service, slots, prepared, release_key, release_sha256):
    plan_sha256 = prepared["plan_sha256"]
    plan, state = slots.load_plan(plan_sha256)
    if plan.get("test_only") is not False:
        raise ValueError("Test-only preparation cannot become the rollback target")
    candidate, before = plan["candidate"], plan["expected_route"]
    route, etag = service.route()
    if route != before:
        raise StandbyRouteChanged("Routing changed after standby preparation")
    proof = slots.require_release(candidate, release_key, release_sha256)
    qualified, _ = slots.qualify(
        state, json.loads(slots.read(state["deployment_key"], 1024 * 1024))
    )
    if any(qualified["route"].get(key) != candidate.get(key) for key in IDENTITY):
        raise RuntimeError("Fresh candidate identity differs from the prepared plan")
    record, record_etag, _ = service.store_record()
    if record_etag != etag or record["previous"] != before["current"]:
        raise StandbyRouteChanged("Routing changed after standby preparation")
    rollback_route = {"version": 1, "current": candidate, "previous": before["current"]}
    target = {"version": 1, "current": record["current"], "previous": candidate}
    slots.immutable(
        STANDBY_PREFIX + candidate["admission_sha256"] + ".json",
        raw_json(
            {
                "schema_version": 1,
                "owner": OWNER,
                "kind": STANDBY_KIND,
                "store_admission_sha256": record["current"]["admission_sha256"],
                "request_id": state["request_id"],
                "route": rollback_route,
            }
        ),
    )
    state.update(
        release_proof=proof,
        standby_before=record,
        standby_target=target,
        standby_rollback_route=rollback_route,
    )
    # The phase stays "prepared" until the write is confirmed, so a retry
    # returns this plan instead of restarting the candidate to qualify it again.
    slots.save(state)
    try:
        service.record_rollback_target(candidate, etag)
    except Exception:
        if service.store_record()[0] != target:
            raise
    if service.store_record()[0] != target or service.route()[0] != rollback_route:
        raise RuntimeError("Recorded rollback target does not read back")
    slots.save(state, "standby-recorded")
    slots.unprotect(state)
    return _report(plan_sha256, state, proof)


def standby_refresh(service, slots, publish, *, release_key, release_sha256, resume_only=False):
    if not isinstance(service, StandbyRefresh):
        raise TypeError("A standby refresh needs the standby routing view")
    route, etag = service.route()
    record, _, _ = service.store_record()
    target = route["current"]
    target_unavailable = unavailable(service, target)
    slots.require_release(target, release_key, release_sha256)
    _, deployment = slots.admission_record(target)
    if not target_unavailable:
        manifest, _ = slots.artifact(target["build_id"])
        slots.retirement_freshness(target, manifest)
    report = _recorded(service, slots, deployment, record)
    if report is not None:
        return report
    _settle_landed_promotion(slots, route)
    return _continue_refresh(
        service,
        slots,
        publish,
        route,
        etag,
        deployment["image_digest"],
        release_key=release_key,
        release_sha256=release_sha256,
        resume_only=resume_only,
        serving_unavailable=target_unavailable,
        complete=lambda prepared: record_standby(
            service, slots, prepared, release_key, release_sha256
        ),
    )
