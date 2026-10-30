"""One cloud checkpoint, bounded smoke/import, then an immutable portable release."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

from mps.typeahead_checkpoint import fetch_checkpoint
from mps.typeahead_checkpoint_pilot import BINARY_SHA, measure_sample
from mps.typeahead_checkpoint_stream import file_witness
from mps.typeahead_tantivy import TantivyAdapter, durable_receipt, fetch_cloud_binary


def read_anchor(service):
    from mps.typeahead_refresh import target_engine

    route, _etag = service.route()
    target = route["current"]
    if target_engine(target) != "native":
        target = route.get("native_rollback")
    if not isinstance(target, dict) or target_engine(target) != "native":
        raise ValueError("A retained native rollback target is required")
    service.healthy(target)
    key = f"releases/search/{target['build_id']}/manifest.json"
    result = service.s3.get_object(Bucket=service.bucket, Key=key)
    body = result["Body"]
    try:
        raw = body.read(1024 * 1024 + 1)
    finally:
        body.close()
    if len(raw) > 1024 * 1024:
        raise ValueError("Native rollback manifest exceeds metadata bound")
    native = json.loads(raw)
    if (
        native.get("active_build") != target["build_id"]
        or native.get("source_watermark") != target["source_watermark"]
    ):
        raise ValueError("Native rollback manifest differs from the serving target")
    return raw


def run_cloud_release(repository, *, timeout_seconds=1800):
    from prefect import get_run_logger
    from prefect.artifacts import create_table_artifact
    from prefect.client.orchestration import get_client
    from prefect.context import FlowRunContext, get_run_context

    from mps.typeahead_checkpoint_build import build_checkpoint
    from mps.typeahead_refresh import Refresh

    context = get_run_context()
    if not isinstance(context, FlowRunContext):
        raise ValueError("Cloud release requires a flow execution")
    run = context.flow_run
    home = Path(os.environ["INDEXER_HOME"])
    if (
        sys.platform != "linux"
        or home != Path("/var/lib/typeahead-indexer/work")
        or not 60 <= timeout_seconds <= 1800
    ):
        raise ValueError("Bounded cloud-only release required")
    with get_client(sync_client=True) as client:
        deployment = client.read_deployment(run.deployment_id)
        pool = client.read_work_pool(deployment.work_pool_name)
        execution = client.read_flow_run(run.id)
        if (
            pool.name != "typeahead-ephemeral"
            or pool.type != "hcloud-builder"
            or not (execution.infrastructure_pid or "").startswith(
                "prefect-build-" + run.id.hex + "-r"
            )
        ):
            raise ValueError("Owned ephemeral cloud execution required")
    if (
        os.environ.get("INDEX_TANTIVY_BINARY_SHA256") != BINARY_SHA
        or os.environ.get("TA_RETRIEVAL_STRATEGY") != "global-merge"
    ):
        raise ValueError("Qualified reader and retrieval strategy required")
    revision = os.environ["INDEX_TANTIVY_SOURCE_REVISION"]
    import subprocess

    actual = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()
    if actual != revision:
        raise ValueError("Pinned producer checkout differs")
    adapter = TantivyAdapter(
        Path(repository) / "scripts/snapshot_refresh/tantivy_install.py",
        os.environ["INDEX_TANTIVY_ADAPTER_SHA256"],
    )
    logger = get_run_logger()
    service = Refresh(logger)
    try:
        anchor = read_anchor(service)
    finally:
        service.close()
    root = home / "tantivy-builds" / ("checkpoint-" + run.id.hex)
    root.mkdir(parents=True, exist_ok=False)
    (root / "owner").write_text("typeahead-index-tantivy-v1\n")
    source = root / "native-anchor"
    source.mkdir()
    (source / "manifest.json").write_bytes(anchor)
    binary = fetch_cloud_binary(root / "code/typeahead-tantivy", BINARY_SHA, root / "code")
    started = time.monotonic()
    deadline = started + timeout_seconds

    def progress(event):
        logger.info("checkpoint_progress %s", json.dumps(event, sort_keys=True))

    checkpoint = fetch_checkpoint(
        os.environ["TURSO_URL"],
        os.environ["TURSO_AUTH_TOKEN"],
        root / "source.db",
        max_seconds=min(900, timeout_seconds - 30),
        progress=progress,
    )
    raw = json.dumps(checkpoint, sort_keys=True).encode() + b"\n"
    (root / "checkpoint.json").write_bytes(raw)
    smoke = measure_sample(
        root / "source.db", root / "sample", Path(repository), binary, checkpoint, 100000
    )
    logger.info("checkpoint_sample %s", json.dumps(smoke, sort_keys=True))
    remaining = int(deadline - time.monotonic()) - 120
    if remaining < 60:
        raise TimeoutError("Checkpoint download/smoke left insufficient full-import budget")
    build = f"b{checkpoint['source_clock_before']}-cp-{run.id.hex[:10]}"
    imported = build_checkpoint(
        Path(repository),
        root / "build",
        binary,
        root / "source.db",
        receipt_bytes=raw,
        receipt_sha256=hashlib.sha256(raw).hexdigest(),
        expected_source_origin_sha256=checkpoint["source_origin_sha256"],
        expected_file_witness=file_witness(root / "source.db"),
        native_manifest_bytes=anchor,
        native_manifest_sha256=hashlib.sha256(anchor).hexdigest(),
        build_id=build,
        environment=dict(os.environ),
        timeout=remaining,
    )
    if (
        imported.get("consumer_verified") is not True
        or imported.get("live_handoff_verified") is not True
    ):
        raise ValueError("Complete import and genuine live catch-up are required")
    (root / "build/data").rename(root / "data")
    manifest = dict(imported["manifest_fields"])
    manifest.update(
        engine="tantivy",
        manifest_version=1,
        artifact_format="tantivy-directory-tar-v1",
        backend_format=adapter.installer.FORMAT,
        normalize_version="v2-cjk-bigram",
        plan_version="complete-prefix-groups-v1",
        active_build=build,
        binary_sha256=BINARY_SHA,
        retrieval_strategy="global-merge",
        sha256="0" * 64,
        byte_size=1,
        object_key=f"releases/search-tantivy/{build}/index.tar",
        files=[{"path": "index/meta.json", "byte_size": 1, "sha256": "0" * 64}],
    )
    portable = adapter.freeze(root / "data", root / "publication", manifest, binary, anchor)
    report = {
        "engine": "tantivy",
        "active_build": portable["active_build"],
        "source_revision": revision,
        "adapter_sha256": adapter.source_sha256,
        "checkpoint": checkpoint,
        "sample": smoke,
        "import_seconds": imported["wall_seconds"],
        "promotion_permitted": False,
        "release_qualified": False,
        "frozen_bundle_bytes": portable["byte_size"],
        "source_handoff_verified": True,
    }
    durable_receipt(root / "preparation.json", report)
    create_table_artifact(
        key="typeahead-checkpoint-release-" + run.id.hex,
        table=[report],
        description="Cloud checkpoint/import/handoff and frozen bundle; not serving qualification or cutover permission",
    )
    logger.info(
        "checkpoint_release_frozen build=%s bytes=%s seconds=%.3f",
        build,
        portable["byte_size"],
        time.monotonic() - started,
    )
    return root / "publication", source
