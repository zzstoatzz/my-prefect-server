"""Cloud-only checkpoint diagnostic; never publishes or admits a serving artifact."""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from mps.typeahead_checkpoint import fetch_checkpoint
from mps.typeahead_sqlite_seed import checkpoint_fixture
from mps.typeahead_tantivy import durable_receipt, fetch_cloud_binary

BINARY_SHA = "ef2be085f7ad7b2bf4ea1f0e4976ee07e37b4041a95b5f34c31c562399f17a4d"


def sample_checkpoint(source, destination, documents, *, seconds=30):
    source, destination = Path(source), Path(destination)
    if not 32 <= documents <= 100000 or destination.exists() or destination.is_symlink():
        raise ValueError("A new bounded checkpoint sample is required")
    started = time.monotonic()
    orphan_limit = min(32, documents // 8)
    try:
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as db:
            db.set_progress_handler(lambda: int(time.monotonic() - started > seconds), 1000)
            db.execute("ATTACH DATABASE ? AS sample", (str(destination),))
            db.execute("PRAGMA sample.max_page_count=32768")
            db.execute("BEGIN")
            db.execute(
                "CREATE TABLE sample.actors AS SELECT * FROM main.actors ORDER BY did LIMIT ?",
                (documents - orphan_limit,),
            )
            db.execute("CREATE UNIQUE INDEX sample.actor_did ON actors(did)")
            db.execute(
                "CREATE TABLE sample.tombstones AS SELECT t.* FROM main.tombstones t JOIN sample.actors a ON t.did=a.did"
            )
            db.execute(
                "INSERT INTO sample.tombstones SELECT t.* FROM main.tombstones t WHERE NOT EXISTS(SELECT 1 FROM main.actors a WHERE a.did=t.did) ORDER BY t.did LIMIT ?",
                (orphan_limit,),
            )
            db.execute("CREATE UNIQUE INDEX sample.tombstone_did ON tombstones(did)")
            for table in ["mod_overrides", "blocked_domains"]:
                db.execute(f"CREATE TABLE sample.{table} AS SELECT * FROM main.{table} LIMIT 10001")
            db.commit()
        if destination.stat().st_size > 128 * 1024**2:
            raise ValueError("Sample byte cap exceeded")
        return {
            "sample_bytes": destination.stat().st_size,
            "sample_seconds": time.monotonic() - started,
        }
    except BaseException:
        destination.unlink(missing_ok=True)
        Path(str(destination) + "-journal").unlink(missing_ok=True)
        raise


def measure_sample(raw, root, repository, binary, checkpoint, documents):
    root.mkdir()
    sample = root / "sample.db"
    sampled = sample_checkpoint(raw, sample, documents)
    stream = root / "seed.jsonl"
    seed = checkpoint_fixture(
        sample,
        stream,
        repository,
        build_id=f"b{checkpoint['source_clock_before']}-checkpoint-pilot",
        generation=checkpoint["generation"],
        durable_frame_num=checkpoint["committed_frame"],
        max_actors=documents,
        max_seconds=45,
    )
    data = root / "index"
    started = time.monotonic()
    result = subprocess.run(
        [
            str(binary),
            "import",
            "--data",
            str(data),
            "--input",
            str(stream),
            "--allow-partial",
            "true",
            "--writer-memory-mb",
            "128",
        ],
        capture_output=True,
        check=False,
        timeout=60,
    )
    if result.returncode:
        raise RuntimeError("Pinned pilot reader rejected the checkpoint sample")
    imported = json.loads(result.stdout)
    spec = importlib.util.spec_from_file_location(
        "checkpoint_pilot_smoke", repository / "services/tantivy/tests/smoke.py"
    )
    if spec is None or spec.loader is None:
        raise ValueError("Pinned loopback smoke helper unavailable")
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    smoke.__dict__["BIN"] = binary
    with (root / "reader.log").open("wb") as log:
        for _ in range(2 if documents == 32 else 1):
            child, public, _admin = smoke.start(data, log, "checkpoint-pilot-no-public-admin")
            try:
                status = smoke.request(public, "/index/status")[2]
                if (
                    status.get("cutover_source_ready") is not False
                    or status.get("full_source") is not False
                ):
                    raise ValueError("A partial pilot cannot qualify for cutover")
                with stream.open("rb") as input_stream:
                    header = json.loads(input_stream.readline())
                if status["source_manifest"]["source_bootstrap"] != header["source_bootstrap"]:
                    raise ValueError("Checkpoint provenance changed during import/restart")
                if (
                    status.get("binary_sha256") != BINARY_SHA
                    or status.get("retrieval_strategy") != "global-merge"
                    or imported["input_actors"] != seed["input_documents"]
                ):
                    raise ValueError("Pilot reader/input identity mismatch")
                if smoke.request(public, "/search?q=a&limit=10")[0] != 200:
                    raise ValueError("Pilot search did not answer")
            finally:
                smoke.stop(child)
    return {
        **sampled,
        "requested_document_limit": documents,
        "input_documents": seed["input_documents"],
        "rust_live_documents": imported["actor_count"],
        "export_seconds": seed["wall_seconds"],
        "import_and_smoke_seconds": time.monotonic() - started,
        "index_bytes": sum(path.stat().st_size for path in data.rglob("*") if path.is_file()),
        "restart_checked": documents == 32,
        "release_qualified": False,
    }


def run_cloud_pilot(repository):
    from prefect import get_run_logger
    from prefect.artifacts import create_table_artifact
    from prefect.client.orchestration import get_client
    from prefect.context import FlowRunContext, get_run_context

    context = get_run_context()
    if not isinstance(context, FlowRunContext):
        raise ValueError("Pilot requires a flow run")
    run = context.flow_run
    home = Path(os.environ["INDEXER_HOME"])
    if (
        sys.platform != "linux"
        or home != Path("/var/lib/typeahead-indexer/work")
        or os.environ.get("TA_RETRIEVAL_STRATEGY") != "global-merge"
    ):
        raise ValueError("Checkpoint data work is cloud-only")
    with get_client(sync_client=True) as client:
        deployment = client.read_deployment(run.deployment_id)
        pool = client.read_work_pool(deployment.work_pool_name)
        current = client.read_flow_run(run.id)
        if (
            pool.type != "hcloud-builder"
            or pool.name != "typeahead-ephemeral"
            or not (current.infrastructure_pid or "").startswith(
                "prefect-build-" + run.id.hex + "-r"
            )
        ):
            raise ValueError("Owned ephemeral cloud worker required")
    logger = get_run_logger()
    parent = home / "checkpoint-pilots"
    parent.mkdir(parents=True, exist_ok=True)
    report = {
        "scope": "cloud-checkpoint-partial-import-pilot",
        "flow_run_id": str(run.id),
        "production_changed": False,
        "publication_performed": False,
        "release_qualified": False,
    }
    with tempfile.TemporaryDirectory(prefix="pilot-", dir=parent) as directory:
        root = Path(directory)
        binary = fetch_cloud_binary(root / "code/typeahead-tantivy", BINARY_SHA, root / "code")
        logger.info(
            "checkpoint_pilot fetching one checkpoint; source deadline180s,8GiB byte cap; no retries"
        )
        checkpoint = fetch_checkpoint(
            os.environ["TURSO_URL"],
            os.environ["TURSO_AUTH_TOKEN"],
            root / "source.db",
            max_seconds=180,
        )
        report["checkpoint"] = checkpoint
        logger.info(
            "checkpoint_pilot verified materialized DB: bytes=%s seconds=%.3f generation=%s committed_frame=%s",
            checkpoint["database_bytes"],
            checkpoint["wall_seconds"],
            checkpoint["generation"],
            checkpoint["committed_frame"],
        )
        report["samples"] = []
        for documents in [32, 100000]:
            report["samples"].append(
                measure_sample(
                    root / "source.db",
                    root / str(documents),
                    repository,
                    binary,
                    checkpoint,
                    documents,
                )
            )
        durable_receipt(root / "result.json", report)
        create_table_artifact(
            key="typeahead-checkpoint-pilot-" + run.id.hex,
            table=[report],
            description="Bounded cloud checkpoint and partial import; never release qualification",
        )
        logger.info("CHECKPOINT_PILOT_RESULT %s", json.dumps(report, sort_keys=True))
    report["local_payloads_retired"] = True
    return report
