"""Standby refresh against filesystem-backed R2 with the provider stages stubbed.

Slot preparation's own route fences, lease, journal, pending record and plan run
for real; machine, volume and qualification calls are replaced, so nothing here
is production evidence for those.
"""

import hashlib
import json
from contextlib import contextmanager

import pytest
from mps import (
    typeahead_refresh,
    typeahead_tantivy_automatic,
    typeahead_tantivy_slots,
    typeahead_tantivy_standby as standby,
)
from mps.typeahead_refresh import ROUTE_KEY, STORE_SLOT_ORIGINS
from mps.typeahead_tantivy_slots import APPS, OWNER, raw_json
from mps.typeahead_tantivy_standby import (
    StandbyRefresh,
    StandbyRouteChanged,
    StandbyWriteRefused,
    encode_record,
    standby_refresh,
)

from tests.test_typeahead_engine_compatibility import (
    FixtureRefresh,
    bucket_keys,
    controller as controller,
    seed_stub_publication,
)
from tests.test_typeahead_store_slot import (
    FIELDS,
    IMAGE,
    MANAGED,
    OLD_A,
    PROOF,
    SERVING_B,
    SLOT_A,
    cut_over,
    route_object,
)
from tests.test_typeahead_tantivy_slots import slot_service

STAGES = [
    "publish",
    "rollback-target-proof",
    "retire",
    "allocate",
    "start",
    "first-qualification",
    "stop",
    "second-qualification",
    "candidate-proof",
    "final-qualification",
    "ledger",
    "record-write",
]


class FixtureStandby(FixtureRefresh, StandbyRefresh):
    pass


class Injected(RuntimeError):
    pass


class Provider:
    """Stands in for Fly, the installer and the release proof below slot preparation."""

    def __init__(self, controller, slots, fail=None):
        self.controller, self.slots, self.fail = controller, slots, fail
        self.calls, self.probed, self.qualifications, self.builds = [], [], 0, []
        self.machines = {APPS[0]: OLD_A["build_id"], APPS[1]: SERVING_B["build_id"]}
        controller.healthy = self.healthy
        controller.put_route_object = controller.s3.put_object
        controller.s3.put_object = self.put_object
        for name in (
            "require_release",
            "admission_record",
            "artifact",
            "retirement_freshness",
            "retire_previous",
            "allocate",
            "start",
            "stop",
            "wait_qualified",
            "qualify",
        ):
            setattr(slots, name, getattr(self, name))
        self.immutable = slots.immutable
        slots.immutable = self.write_once

    def stage(self, name):
        self.calls.append(name)
        if self.fail == name:
            raise Injected(name)

    def publish(self):
        self.stage("publish")
        build_id = f"b17933{len(self.builds):05d}-standby"
        self.builds.append(build_id)
        return build_id

    def healthy(self, target, *, require_fresh=True):
        self.probed.append(target["origin"])
        return {"health": {"ready": True, "sync_lag_s": 0}, "index": {"actor_count": 4}}

    def put_object(self, **kwargs):
        if kwargs["Key"] == ROUTE_KEY:
            self.stage("record-write")
        return self.controller.put_route_object(**kwargs)

    def write_once(self, key, raw):
        if key.startswith(standby.STANDBY_PREFIX):
            self.stage("ledger")
        return self.immutable(key, raw)

    def candidate(self, state):
        return {
            **SLOT_A,
            "origin": f"https://{state['app']}.fly.dev",
            "build_id": state["build_id"],
            "admission_sha256": hashlib.sha256(state["request_id"].encode()).hexdigest(),
        }

    def require_release(self, target, key, digest):
        installed = self.machines.get(target["origin"][8:-8])
        self.stage(
            "candidate-proof" if target["build_id"] in self.builds else "rollback-target-proof"
        )
        assert installed == target["build_id"], "release proof is bound to an installed slot"
        return {"key": key, "sha256": digest, "passed": True}

    def admission_record(self, target):
        raw = self.slots.read(f"control/tantivy-admissions/{target['admission_sha256']}.json")
        bound = {"image_digest": IMAGE}
        if raw is not None:
            bound["request_id"] = json.loads(raw)["request_id"]
        return {}, bound

    def artifact(self, build_id):
        manifest = {
            "source_actor_count": 4,
            "source_build_id": build_id,
            "source_manifest_sha256": "f" * 64,
        }
        return manifest, raw_json(manifest)

    def retirement_freshness(self, target, manifest):
        return {}

    def retire_previous(self, state, key, digest):
        if state.get("retired_previous") is not None:
            return
        self.stage("retire")
        route = self.controller.route()[0]
        assert route["current"]["origin"] != f"https://{state['app']}.fly.dev"
        assert self.controller.store_record()[0]["previous"] == route["current"]
        del self.machines[state["app"]]
        state["retired_previous"] = {"previous": route.get("previous")}
        self.slots.save(state, "previous-retired")

    def allocate(self, state, manifest):
        self.stage("allocate")
        bound = {"provider_app": state["app"], "active_build": state["build_id"]}
        key = f"control/tantivy-slots/{state['app']}/{state['request_id']}.json"
        self.controller.s3.seed(key, raw_json(bound))
        state.update(
            deployment_key=key,
            deployment_sha256=hashlib.sha256(raw_json(bound)).hexdigest(),
            machine_id="0123456789abcd",
            volume_id="vol_fixture",
        )
        self.slots.save(state, "machine-ready")
        return bound

    def start(self, state):
        self.stage("start")
        self.machines[state["app"]] = state["build_id"]

    def stop(self, state):
        self.stage("stop")

    def wait_qualified(self, state, bound):
        self.qualifications += 1
        self.stage("first-qualification" if self.qualifications % 2 else "second-qualification")
        self.slots.guard(state)
        return {"route": self.candidate(state), "admission": {"test_only": False}}, {}

    def qualify(self, state, bound):
        self.stage("final-qualification")
        return {"route": self.candidate(state)}, {}


@pytest.fixture
def routed(controller, tmp_path):
    slots = slot_service(controller, tmp_path)
    controller.__class__ = FixtureStandby
    for target in (OLD_A, SERVING_B):
        seed_stub_publication(controller, target)
    record = cut_over(controller)
    return controller, slots, record


def run(controller, slots, provider, **options):
    return standby_refresh(controller, slots, provider.publish, **PROOF, **options)


def test_standby_refresh_rebuilds_the_spare_slot_and_only_repoints_previous(routed):
    controller, slots, record = routed
    provider = Provider(controller, slots)
    before, _ = route_object(controller)
    assert controller.route() == (MANAGED, route_object(controller)[1])

    report = run(controller, slots, provider)

    after, _ = route_object(controller)
    written = json.loads(after)
    candidate = written["previous"]
    assert candidate["origin"] == f"https://{APPS[0]}.fly.dev"
    assert candidate["build_id"] == provider.builds[0] and set(candidate) == set(FIELDS)
    assert written == {"version": 1, "current": record["current"], "previous": candidate}
    serving = b'{"version":1,"current":' + encode_record(record["current"]) + b',"previous":'
    assert before.startswith(serving) and after.startswith(serving)
    assert report["phase"] == "standby-recorded" and report["current_unchanged"] is True
    assert report["route"] == written
    rollback_route = {"version": 1, "current": candidate, "previous": SERVING_B}
    assert report["rollback_route"] == rollback_route == controller.route()[0]
    ledger = json.loads(
        controller.s3.path(
            f"control/tantivy-standby/{candidate['admission_sha256']}.json"
        ).read_bytes()
    )
    assert ledger["route"] == rollback_route
    assert ledger["store_admission_sha256"] == record["current"]["admission_sha256"]
    assert provider.calls.index("retire") < provider.calls.index("allocate")
    assert provider.machines == {APPS[0]: provider.builds[0], APPS[1]: SERVING_B["build_id"]}
    assert STORE_SLOT_ORIGINS[0] not in provider.probed
    assert bucket_keys(controller, "control/tantivy-slots") == [
        f"control/tantivy-slots/{APPS[0]}/{ledger['request_id']}.json"
    ]


def test_successive_standby_refreshes_alternate_slots_around_the_rollback_target(routed):
    controller, slots, record = routed
    provider = Provider(controller, slots)
    run(controller, slots, provider)
    controller.execution_identity = lambda: "execution-two"
    controller.execution_finished = lambda _: True
    first = json.loads(route_object(controller)[0])["previous"]
    provider.calls.clear()

    report = run(controller, slots, provider)

    second = report["route"]["previous"]
    assert second["origin"] == f"https://{APPS[1]}.fly.dev"
    assert second["build_id"] == provider.builds[1]
    assert report["route"]["current"] == record["current"]
    assert report["rollback_route"] == {"version": 1, "current": second, "previous": first}
    assert provider.machines == {APPS[0]: provider.builds[0], APPS[1]: provider.builds[1]}
    assert provider.calls.count("publish") == 1


def test_a_retry_in_the_same_execution_reports_the_recorded_target_without_rebuilding(routed):
    controller, slots, _ = routed
    provider = Provider(controller, slots)
    report = run(controller, slots, provider)
    recorded = route_object(controller)
    provider.calls.clear()
    controller._lease = None

    assert run(controller, slots, provider) == report
    assert route_object(controller) == recorded
    assert not {"publish", "retire", "allocate", "record-write"} & set(provider.calls)


@pytest.mark.parametrize("stage", STAGES)
def test_failure_at_any_stage_leaves_the_record_identical_and_resumes(routed, stage):
    controller, slots, record = routed
    provider = Provider(controller, slots, fail=stage)
    before = route_object(controller)

    with pytest.raises(Injected, match=stage):
        run(controller, slots, provider)

    assert route_object(controller) == before
    assert provider.machines[APPS[1]] == SERVING_B["build_id"]
    assert stage in provider.calls

    provider.fail = None
    controller._lease = None
    report = run(controller, slots, provider)
    assert len(provider.builds) == 1
    assert provider.machines == {APPS[0]: provider.builds[0], APPS[1]: SERVING_B["build_id"]}
    assert report["route"]["current"] == record["current"]
    assert report["route"]["previous"]["build_id"] == provider.builds[0]
    assert json.loads(route_object(controller)[0]) == report["route"]


def test_a_record_write_that_landed_without_an_answer_is_confirmed_not_repeated(routed):
    controller, slots, record = routed
    provider = Provider(controller, slots)
    put = controller.put_route_object

    def lost_answer(**kwargs):
        result = put(**kwargs)
        if kwargs["Key"] == ROUTE_KEY:
            raise Injected("answer lost")
        return result

    controller.put_route_object = lost_answer
    report = run(controller, slots, provider)
    assert json.loads(route_object(controller)[0]) == report["route"]
    assert report["route"]["current"] == record["current"]


def test_a_rollback_during_the_refresh_wins_and_nothing_is_written(routed):
    controller, slots, _ = routed
    provider = Provider(controller, slots)
    restored = raw_json(MANAGED)
    qualify = provider.qualify

    def rolled_back(state, bound):
        controller.s3.seed(ROUTE_KEY, restored)
        return qualify(state, bound)

    slots.qualify = rolled_back
    with pytest.raises(StandbyRouteChanged, match="not routed to a store slot"):
        run(controller, slots, provider)
    assert route_object(controller)[0] == restored
    assert "record-write" not in provider.calls
    assert not bucket_keys(controller, "control/tantivy-standby")


def roll_back(controller):
    """What store_slot_route.py rollback leaves: the prior managed record, read as before."""
    controller.__class__ = FixtureRefresh
    controller.s3.seed(ROUTE_KEY, encode_record(MANAGED))
    return controller.route()


def test_a_promoting_refresh_after_a_rollback_finishes_the_standby_candidate(routed):
    controller, slots, _ = routed
    provider = Provider(controller, slots, fail="start")
    with pytest.raises(Injected):
        run(controller, slots, provider)
    provider.fail = None
    route, etag = roll_back(controller)
    promoted = []
    slots.promote = lambda plan_sha256, *_: promoted.append(slots.load_plan(plan_sha256)[0])

    typeahead_tantivy_automatic._continue_refresh(
        controller, slots, provider.publish, route, etag, IMAGE, **PROOF
    )

    assert len(provider.builds) == 1 and provider.calls.count("retire") == 1
    assert promoted[0]["candidate"]["build_id"] == provider.builds[0]
    assert promoted[0]["candidate"]["origin"] == f"https://{APPS[0]}.fly.dev"
    assert promoted[0]["expected_route"] == MANAGED
    assert promoted[0]["expected_current_etag"] == controller.route()[1]


def test_a_rollback_between_preparation_and_recording_needs_the_candidate_canceled(routed):
    controller, slots, _ = routed
    provider = Provider(controller, slots, fail="ledger")
    with pytest.raises(Injected):
        run(controller, slots, provider)
    provider.fail = None
    route, etag = roll_back(controller)
    request_id = json.loads(slots.read(f"control/tantivy-slots/{APPS[0]}/pending.json"))[
        "request_id"
    ]

    with pytest.raises(RuntimeError, match="Prepared plan routing precondition changed"):
        typeahead_tantivy_automatic._continue_refresh(
            controller, slots, provider.publish, route, etag, IMAGE, **PROOF
        )
    assert controller.route() == (route, etag)

    assert slots.cancel(request_id)["phase"] == "canceled-retained"
    route, etag = controller.route()
    promoted = []
    slots.promote = lambda plan_sha256, *_: promoted.append(slots.load_plan(plan_sha256)[0])
    typeahead_tantivy_automatic._continue_refresh(
        controller, slots, provider.publish, route, etag, IMAGE, **PROOF
    )
    assert len(provider.builds) == 1
    assert promoted[0]["candidate"]["build_id"] == provider.builds[0]
    assert promoted[0]["expected_current_etag"] == controller.route()[1]


def test_a_changed_record_fails_the_conditional_write(routed):
    controller, slots, record = routed
    provider = Provider(controller, slots)
    put = controller.put_route_object

    def raced(**kwargs):
        if kwargs["Key"] == ROUTE_KEY:
            controller.s3.seed(ROUTE_KEY, encode_record(record) + b"\n")
        return put(**kwargs)

    controller.put_route_object = raced
    with pytest.raises(RuntimeError, match="changed"):
        run(controller, slots, provider)
    assert route_object(controller)[0] == encode_record(record) + b"\n"


def test_standby_refresh_refuses_a_record_it_cannot_copy_or_explain(routed):
    controller, slots, record = routed
    provider = Provider(controller, slots)
    pretty = json.dumps(record, indent=2).encode()
    controller.s3.seed(ROUTE_KEY, pretty)
    with pytest.raises(StandbyWriteRefused, match="byte for byte"):
        run(controller, slots, provider)
    assert route_object(controller)[0] == pretty and provider.calls == []

    for changed, message in [
        ({**record, "mutation": {"owner": OWNER}}, "no rollback target or unknown fields"),
        ({"version": 1, "current": record["current"]}, "no rollback target or unknown fields"),
        ({**record, "previous": record["current"]}, "Invalid Tantivy route identity"),
        ({**record, "previous": {**SERVING_B, "source_watermark": 1}}, "exactly the routed"),
        ({**record, "previous": OLD_A}, "no rollback route"),
        (
            {**record, "current": {**record["current"], "admission_sha256": "0" * 64}},
            "admission is missing",
        ),
    ]:
        controller._rollback_routes = None
        controller.s3.seed(ROUTE_KEY, encode_record(changed))
        with pytest.raises(ValueError, match=message):
            run(controller, slots, provider)
        assert route_object(controller)[0] == encode_record(changed)
    assert provider.calls == []


def test_a_promotion_that_landed_before_the_cutover_is_settled_first(routed):
    controller, slots, record = routed
    state = {
        "schema_version": 1,
        "owner": OWNER,
        "request_id": "a" * 32,
        "execution_id": "execution-one",
        "app": APPS[1],
        "build_id": SERVING_B["build_id"],
        "source_build_id": SERVING_B["build_id"],
        "artifact_manifest_sha256": "a" * 64,
        "promotion_target": MANAGED,
    }
    slots.save(state, "promotion-cas-intent")
    slots.protect(state)
    provider = Provider(controller, slots)

    report = run(controller, slots, provider)

    assert slots.journal("a" * 32)["phase"] == "promoted"
    assert report["route"]["previous"]["origin"] == f"https://{APPS[0]}.fly.dev"
    assert report["route"]["current"] == record["current"]


def test_standby_prune_keeps_both_managed_builds_and_the_store_bootstrap(routed):
    controller, _, record = routed
    seed_stub_publication(controller, record["current"])
    seed_stub_publication(controller, {**SLOT_A, "build_id": "b1792800000-disposable"})
    before = route_object(controller)

    report = controller.prune_artifacts()

    assert report["retained_builds"] == sorted(
        [OLD_A["build_id"], SERVING_B["build_id"], record["current"]["build_id"]]
    )
    assert sorted(controller.s3.deletions) == [
        "releases/search-tantivy/b1792800000-disposable/index.tar",
        "releases/search-tantivy/b1792800000-disposable/manifest.json",
    ]
    assert route_object(controller) == before


def test_standby_view_refuses_every_serving_route_write(routed):
    controller, slots, record = routed
    provider = Provider(controller, slots)
    route, etag = controller.route()
    before = route_object(controller)
    promoted = {"version": 1, "current": OLD_A, "previous": route["current"]}
    for target in (promoted, dict(route), {"version": 1, "current": record["current"]}):
        with pytest.raises(StandbyWriteRefused):
            controller._put_route(target, etag)
    with pytest.raises(StandbyWriteRefused):
        controller._restore_previous_route(route, route, etag)
    lease = {"owner": OWNER, "request_id": "b" * 32, "app": APPS[1], "acquired_at": 1}
    with pytest.raises(RuntimeError, match="cannot lease the rollback target"):
        controller._put_route({**route, "mutation": lease}, etag)
    with pytest.raises(ValueError, match="Invalid Tantivy route identity"):
        controller.record_rollback_target(record["current"], etag)
    with pytest.raises(StandbyRouteChanged):
        controller.record_rollback_target(OLD_A, "stale-etag")

    state = {
        "request_id": "c" * 32,
        "owner": OWNER,
        "execution_id": "execution-one",
        "app": APPS[0],
        "route": route,
        "deployment_key": "control/tantivy-slots/fixture.json",
    }
    plan = {
        "test_only": False,
        "expected_current_etag": etag,
        "expected_route": route,
        "candidate": OLD_A,
    }
    slots.load_plan = lambda _: (plan, state)
    slots.read = lambda key, maximum=0: raw_json({})
    slots.qualify = lambda state, bound: ({"route": OLD_A}, {})
    with pytest.raises(RuntimeError, match="routing was not overwritten"):
        slots.promote("fixture-plan", etag, "fixture-proof", "e" * 64)
    assert route_object(controller) == before
    assert "record-write" not in provider.calls


def test_standby_refresh_refuses_a_managed_route(controller, tmp_path):
    slots = slot_service(controller, tmp_path)
    controller.__class__ = FixtureStandby
    controller.s3.seed(ROUTE_KEY, encode_record(MANAGED))
    before = route_object(controller)
    calls = []
    with pytest.raises(StandbyRouteChanged, match="not routed to a store slot"):
        standby_refresh(controller, slots, lambda: calls.append("publish"), **PROOF)
    assert calls == [] and route_object(controller) == before


def test_standby_refresh_needs_the_standby_routing_view(controller, tmp_path):
    slots = slot_service(controller, tmp_path)
    cut_over(controller)
    calls = []
    with pytest.raises(TypeError, match="standby routing view"):
        standby_refresh(controller, slots, lambda: calls.append("publish"), **PROOF)
    assert calls == []


@pytest.mark.parametrize("store_routed", [False, True])
def test_flow_chooses_the_refresh_by_what_the_record_routes(
    controller, tmp_path, monkeypatch, store_routed
):
    import flows.typeahead_index as publisher

    controller.s3.seed(ROUTE_KEY, encode_record(MANAGED))
    if store_routed:
        cut_over(controller)
    before = route_object(controller)
    ran = []

    def promoting(service, slots, publish, **options):
        ran.append(("refresh", type(service)))
        return {"phase": "promoted", "public_verified": True, "publication_execution_id": "run"}

    def standing_by(service, slots, publish, **options):
        ran.append(("standby", type(service)))
        return {
            "phase": "standby-recorded",
            "current_unchanged": True,
            "publication_execution_id": "run",
        }

    def standby_service(logger):
        controller.__class__ = FixtureStandby
        return controller

    @contextmanager
    def pinned():
        yield tmp_path

    controller.execution_identity = lambda: "run"
    controller.close = lambda: None
    monkeypatch.setattr(typeahead_refresh, "Refresh", lambda logger: controller)
    monkeypatch.setattr(standby, "StandbyRefresh", standby_service)
    monkeypatch.setattr(typeahead_tantivy_automatic, "refresh", promoting)
    monkeypatch.setattr(standby, "standby_refresh", standing_by)
    monkeypatch.setattr(typeahead_tantivy_slots, "Slots", lambda service, root: object())
    monkeypatch.setattr(
        typeahead_refresh, "prune_snapshot_artifacts", lambda cls=None: ran.append(("prune", cls))
    )
    monkeypatch.setattr(publisher, "pinned_typeahead", pinned)
    monkeypatch.setattr(publisher, "save_result_storage", lambda: None)
    monkeypatch.setattr(publisher, "run_coro_as_sync", lambda _: None)
    monkeypatch.setattr(publisher, "get_run_logger", lambda: controller.logger)
    monkeypatch.setenv("INDEX_TANTIVY_SOURCE_REVISION", "1" * 40)
    monkeypatch.setenv("INDEX_TANTIVY_ADAPTER_SHA256", "2" * 64)
    monkeypatch.setenv("INDEX_TANTIVY_RELEASE_RECEIPT_KEY", "control/tantivy-releases/fixture.json")
    monkeypatch.setenv("INDEX_TANTIVY_RELEASE_RECEIPT_SHA256", "3" * 64)

    report = publisher.typeahead_index.fn(automatic_refresh=True)

    if store_routed:
        assert ran == [("standby", FixtureStandby), ("prune", standby_service)]
        assert report["phase"] == "standby-recorded"
    else:
        assert ran == [("refresh", FixtureRefresh), ("prune", None)]
        assert report["phase"] == "promoted"
    assert route_object(controller) == before


def test_flow_fails_a_standby_refresh_that_did_not_confirm_the_serving_target(
    controller, tmp_path, monkeypatch
):
    import flows.typeahead_index as publisher

    report = {"phase": "standby-recorded", "current_unchanged": False}

    cut_over(controller)

    @contextmanager
    def pinned():
        yield tmp_path

    controller.close = lambda: None
    monkeypatch.setattr(typeahead_refresh, "Refresh", lambda logger: controller)
    monkeypatch.setattr(standby, "StandbyRefresh", lambda logger: controller)
    monkeypatch.setattr(standby, "standby_refresh", lambda *_, **__: report)
    monkeypatch.setattr(typeahead_tantivy_slots, "Slots", lambda service, root: object())
    monkeypatch.setattr(
        typeahead_refresh,
        "prune_snapshot_artifacts",
        lambda cls=None: pytest.fail("An unconfirmed standby refresh must not prune"),
    )
    monkeypatch.setattr(publisher, "pinned_typeahead", pinned)
    monkeypatch.setattr(publisher, "save_result_storage", lambda: None)
    monkeypatch.setattr(publisher, "run_coro_as_sync", lambda _: None)
    monkeypatch.setattr(publisher, "get_run_logger", lambda: controller.logger)
    monkeypatch.setenv("INDEX_TANTIVY_SOURCE_REVISION", "1" * 40)
    monkeypatch.setenv("INDEX_TANTIVY_ADAPTER_SHA256", "2" * 64)
    monkeypatch.setenv("INDEX_TANTIVY_RELEASE_RECEIPT_KEY", "control/tantivy-releases/fixture.json")
    monkeypatch.setenv("INDEX_TANTIVY_RELEASE_RECEIPT_SHA256", "3" * 64)
    with pytest.raises(RuntimeError, match="Standby refresh is not confirmed"):
        publisher.typeahead_index.fn(automatic_refresh=True)
