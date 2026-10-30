"""Continue an already-approved Tantivy route through existing refresh owners."""

from __future__ import annotations

import json
import re

from mps.typeahead_refresh import target_engine
from mps.typeahead_tantivy_slots import APPS, OWNER


def settings(environment):
    names = {
        "revision": "INDEX_TANTIVY_SOURCE_REVISION",
        "adapter_sha256": "INDEX_TANTIVY_ADAPTER_SHA256",
        "release_key": "INDEX_TANTIVY_RELEASE_RECEIPT_KEY",
        "release_sha256": "INDEX_TANTIVY_RELEASE_RECEIPT_SHA256",
    }
    values = {key: environment.get(name, "") for key, name in names.items()}
    if (
        not re.fullmatch(r"[a-f0-9]{40}", values["revision"])
        or not re.fullmatch(r"[a-f0-9]{64}", values["adapter_sha256"])
        or not re.fullmatch(r"[a-f0-9]{64}", values["release_sha256"])
        or not 1 <= len(values["release_key"]) <= 1024
        or any(char in values["release_key"] for char in "\r\n\0")
    ):
        raise ValueError(
            "Automatic Tantivy installation is fenced without immutable source and release proof"
        )
    return values


def refresh(service, slots, publish, *, release_key, release_sha256, resume_only=False):
    route, etag = service.route()
    current = route["current"]
    if target_engine(current) != "tantivy":
        raise RuntimeError("Automatic initial native-to-Tantivy cutover is forbidden")
    slots.require_release(current, release_key, release_sha256)
    _, deployment = slots.admission_record(current)
    image = deployment["image_digest"]
    manifest, _ = slots.artifact(current["build_id"])
    slots.retirement_freshness(current, manifest)
    return _continue_refresh(
        service,
        slots,
        publish,
        route,
        etag,
        image,
        release_key=release_key,
        release_sha256=release_sha256,
        resume_only=resume_only,
    )


def _continue_refresh(
    service, slots, publish, route, etag, image, *, release_key, release_sha256, resume_only=False
):
    current = route["current"]
    pending = []
    for app in APPS:
        raw = slots.read(f"control/tantivy-slots/{app}/pending.json", 4096)
        if raw is None:
            continue
        record = json.loads(raw)
        if (
            record.get("schema_version") != 1
            or record.get("owner") != OWNER
            or record.get("app") != app
            or not re.fullmatch(r"[a-f0-9]{32}", record.get("request_id", ""))
        ):
            raise ValueError("Unknown pending automatic refresh ownership")
        state = slots.journal(record["request_id"])
        if (
            state is None
            or state.get("app") != app
            or state.get("owner") != OWNER
            or state.get("build_id") != record.get("build_id")
            or state.get("image_digest") != image
            or state.get("artifact_manifest_sha256") != record.get("artifact_manifest_sha256")
        ):
            raise ValueError("Pending automatic refresh identity changed")
        pending.append(state)
    if len(pending) > 1:
        raise RuntimeError("Multiple pending operations fence automatic refresh")

    if pending:
        state = pending[0]
        if state.get("plan_sha256") and state.get("promotion_target") == route:
            plan, _ = slots.load_plan(state["plan_sha256"])
            return slots.promote(
                plan_sha256=state["plan_sha256"],
                expected_current_etag=plan["expected_current_etag"],
                release_receipt_key=release_key,
                release_receipt_sha256=release_sha256,
            )
        if state.get("automatic_refresh") is not True:
            raise RuntimeError("Manual pending preparation fences automatic refresh")
        baseline = {key: value for key, value in route.items() if key != "mutation"}
        if state.get("route") != baseline or (
            route.get("mutation") is not None and route["mutation"] != state.get("mutation")
        ):
            raise RuntimeError("Routing changed during pending automatic refresh")
        build_id, request_id, app = state["build_id"], state["request_id"], state["app"]
    else:
        if resume_only:
            raise RuntimeError("No pending automatic refresh exists; recovery cannot start a build")
        if route.get("mutation") is not None:
            raise RuntimeError("Unknown mutation lease fences automatic refresh")
        baseline = route
        build_id = publish()
        if service.route() != (baseline, etag):
            raise RuntimeError("Routing changed during automatic publication; artifact retained")
        request_id = None
        app = APPS[1] if current["origin"] == f"https://{APPS[0]}.fly.dev" else APPS[0]

    prepared = slots.prepare(
        build_id,
        image,
        request_id=request_id,
        app=app,
        retire_previous_tantivy=True,
        release_receipt_key=release_key,
        release_receipt_sha256=release_sha256,
        expected_route=baseline,
        expected_etag=etag,
        automatic_refresh=True,
    )
    if service.route()[0] != baseline:
        raise RuntimeError("Routing changed after automatic preparation")
    return slots.promote(
        prepared["plan_sha256"],
        prepared["plan"]["expected_current_etag"],
        release_key,
        release_sha256,
    )
