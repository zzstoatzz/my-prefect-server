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


def fetch_pds_fallback(destination, sha256):
    import re

    import boto3
    from botocore.config import Config

    if not isinstance(sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", sha256):
        raise ValueError("Pinned archive mapping checksum required")
    destination = Path(destination)
    part = destination.with_suffix(".partial")
    if destination.exists() or destination.is_symlink() or part.exists() or part.is_symlink():
        raise ValueError("New owned mapping destination required")
    client = boto3.client(
        "s3",
        endpoint_url=os.environ["INDEX_R2_ENDPOINT"],
        aws_access_key_id=os.environ["INDEX_R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["INDEX_R2_SECRET_ACCESS_KEY"],
        config=Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 2}),
    )
    deadline = time.monotonic() + 300
    try:
        result = client.get_object(
            Bucket=os.environ["INDEX_R2_BUCKET"],
            Key=f"releases/pds-fallback/{sha256}/pds-fallback.sqlite",
        )
        with result["Body"] as body:
            size = result.get("ContentLength")
            if type(size) is not int or not 4096 <= size <= 3 * 1024**3:
                raise ValueError("Archive mapping exceeds size bound")
            checksum = hashlib.sha256()
            written = 0
            with part.open("xb") as stream:
                while chunk := body.read(1024 * 1024):
                    written += len(chunk)
                    if written > size or time.monotonic() > deadline:
                        raise ValueError("Archive mapping download exceeded bounds")
                    stream.write(chunk)
                    checksum.update(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            if written != size or checksum.hexdigest() != sha256:
                raise ValueError("Archive mapping checksum differs")
            part.chmod(0o400)
            part.rename(destination)
        return destination
    finally:
        part.unlink(missing_ok=True)
        client.close()


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


def run_cloud_release(
    repository, *, timeout_seconds=5400, pds_fallback_path=None, pds_fallback_sha256=None
):
    from prefect import get_run_logger
    from prefect.artifacts import create_table_artifact
    from prefect.client.orchestration import get_client
    from prefect.context import FlowRunContext, TaskRunContext, get_run_context

    from mps.typeahead_checkpoint_build import build_checkpoint
    from mps.typeahead_refresh import Refresh

    context = get_run_context()
    if isinstance(context, TaskRunContext):
        if context.task_run.flow_run_id is None:
            raise ValueError("Cloud release task must belong to a flow execution")
        with get_client(sync_client=True) as client:
            run = client.read_flow_run(context.task_run.flow_run_id)
    elif isinstance(context, FlowRunContext):
        run = context.flow_run
    else:
        raise ValueError("Cloud release requires a flow execution")
    home = Path(os.environ["INDEXER_HOME"])
    if (
        sys.platform != "linux"
        or home != Path("/var/lib/typeahead-indexer/work")
        or not 60 <= timeout_seconds <= 5400
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
    started = time.monotonic()
    deadline = started + timeout_seconds
    configured_mapping = os.environ.get("INDEX_PDS_FALLBACK_SHA256")
    if configured_mapping is not None:
        if pds_fallback_path is not None or pds_fallback_sha256 is not None:
            raise ValueError("Specify one archive mapping source")
        pds_fallback_sha256 = configured_mapping
        pds_fallback_path = fetch_pds_fallback(root / "pds-fallback.sqlite", configured_mapping)
    source = root / "native-anchor"
    source.mkdir()
    (source / "manifest.json").write_bytes(anchor)
    binary = fetch_cloud_binary(root / "code/typeahead-tantivy", BINARY_SHA, root / "code")

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
        pds_fallback_path=pds_fallback_path,
        pds_fallback_sha256=pds_fallback_sha256,
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
        files=[{"path": "index/meta.json", "byte_size": 1, "sha256": "0" * 64}]
        + (
            [
                {
                    key: imported["manifest_fields"]["pds_fallback"][key]
                    for key in ("path", "byte_size", "sha256")
                }
            ]
            if "pds_fallback" in imported["manifest_fields"]
            else []
        ),
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
        "pds_coverage": imported["stream"]["pds_coverage"],
        "pds_fallback": imported["manifest_fields"].get("pds_fallback"),
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
