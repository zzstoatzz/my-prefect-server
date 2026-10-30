"""Pipe a verified checkpoint into D1 and establish a distinct remote-source handoff."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

from mps.typeahead_checkpoint_stream import stream_checkpoint
from mps.typeahead_source_budget import accounted_source
from mps.typeahead_sqlite_seed import encode
from mps.typeahead_tantivy import progress


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("Pinned checkpoint dependency unavailable")
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


def port():
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


def stop(child):
    if child.poll() is None:
        with suppress(ProcessLookupError):
            os.killpg(child.pid, signal.SIGTERM)
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=3)


def build_checkpoint(
    repository,
    workspace,
    binary,
    database,
    *,
    receipt_bytes,
    receipt_sha256,
    expected_source_origin_sha256,
    expected_file_witness,
    native_manifest_bytes,
    native_manifest_sha256,
    build_id,
    environment,
    timeout=1800,
    source_bytes=512 * 1024**2,
    min_request_interval=0.5,
    allow_fixture=False,
):
    started = time.monotonic()
    started_at = int(time.time())
    if type(timeout) not in (int, float) or not 1 <= timeout <= 3600:
        raise ValueError("Checkpoint build requires a bounded deadline")
    if type(source_bytes) is not int or not 16 * 1024**2 <= source_bytes <= 8 * 1024**3:
        raise ValueError("Checkpoint build source byte bound differs")
    if type(allow_fixture) is not bool:
        raise ValueError("Explicit fixture policy required")
    repository, workspace, binary, database = map(Path, (repository, workspace, binary, database))
    if not workspace.is_absolute() or workspace.resolve() != workspace or workspace.exists():
        raise ValueError("New canonical checkpoint workspace required")
    if not binary.is_file() or not repository.is_dir():
        raise ValueError("Pinned source and prebuilt D1 binary required")
    sync = module("_mps_checkpoint_build_sync", repository / "services/tantivy-input/sync.py")
    current = module(
        "_mps_checkpoint_build_current", repository / "services/tantivy-input/current_state.py"
    )
    adapter = module(
        "_mps_checkpoint_build_install", repository / "scripts/snapshot_refresh/tantivy_install.py"
    )
    try:
        source_url = sync.source_url(environment.get("TURSO_URL", ""), allow_fixture)
    except sync.SafeError as error:
        raise ValueError("Configured remote source is invalid") from error
    if adapter.source_origin_sha256(source_url, allow_fixture) != expected_source_origin_sha256:
        raise ValueError("Configured remote source differs from checkpoint origin")
    token = environment.get("TURSO_AUTH_TOKEN")
    if not isinstance(token, str) or not token or "\n" in token or "\r" in token:
        raise ValueError("Read-only source credential required")
    workspace.mkdir(mode=0o700)
    deadline = started + timeout
    processes = []
    source = None

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError("Checkpoint build deadline exceeded")
        return value

    class DeadlineHttp(sync.Http):
        def request(self, *args, **kwargs):
            self.timeout = min(10, remaining())
            result = super().request(*args, **kwargs)
            remaining()
            return result

    try:
        # The two child processes exchange actor bytes directly through a kernel pipe.
        request = {
            "database": str(database),
            "repository": str(repository),
            "receipt_bytes": receipt_bytes.decode("utf8"),
            "native_manifest_bytes": native_manifest_bytes.decode("utf8"),
            "receipt_sha256": receipt_sha256,
            "expected_source_origin_sha256": expected_source_origin_sha256,
            "expected_file_witness": expected_file_witness,
            "native_manifest_sha256": native_manifest_sha256,
            "build_id": build_id,
            "allow_fixture": allow_fixture,
            "max_seconds": max(1, int(remaining())),
        }
        request_path = workspace / "stream-request.json"
        request_path.write_bytes(encode(request))
        stream_receipt = workspace / "stream-receipt.json"
        data = workspace / "data"
        child_env = {
            key: environment[key]
            for key in ("PATH", "TMPDIR", "LANG", "LC_ALL", "TZ")
            if key in environment
        }
        with (
            (workspace / "import.log").open("xb") as importer_log,
            (workspace / "stream.log").open("xb") as stream_log,
        ):
            importer = subprocess.Popen(
                [
                    str(binary),
                    "import",
                    "--data",
                    str(data),
                    "--input",
                    "-",
                    "--writer-memory-mb",
                    "128",
                ],
                stdin=subprocess.PIPE,
                stdout=importer_log,
                stderr=importer_log,
                env=child_env,
                start_new_session=True,
            )
            processes.append(importer)
            producer = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "mps.typeahead_checkpoint_build",
                    str(request_path),
                    str(stream_receipt),
                ],
                stdout=importer.stdin,
                stderr=stream_log,
                start_new_session=True,
            )
            processes.append(producer)
            assert importer.stdin is not None
            importer.stdin.close()
            next_report = 0
            while producer.poll() is None or importer.poll() is None:
                remaining()
                if producer.returncode not in (None, 0):
                    raise ValueError("Canonical checkpoint producer failed")
                if importer.returncode not in (None, 0):
                    raise ValueError("D1 checkpoint importer failed")
                if time.monotonic() >= next_report:
                    progress(
                        "tantivy-checkpoint-import",
                        elapsed_s=round(time.monotonic() - started, 1),
                        import_log_bytes=(workspace / "import.log").stat().st_size,
                    )
                    next_report = time.monotonic() + 30
                time.sleep(min(0.25, remaining()))
            if producer.returncode != 0 or importer.returncode != 0:
                raise ValueError("Checkpoint producer or D1 importer failed")
        if stream_receipt.stat().st_size > 1024**2:
            raise ValueError("Checkpoint producer receipt exceeds metadata bound")
        stream = json.loads(stream_receipt.read_bytes())
        header = stream["source_manifest"]
        bootstrap = header["source_bootstrap"]
        counts = bootstrap["counts"]
        secret = secrets.token_urlsafe(32)
        public, private = port(), port()
        while public == private:
            private = port()
        admin = sync.Admin(
            f"http://127.0.0.1:{private}",
            secret,
            f"http://127.0.0.1:{public}/index/status",
            http=DeadlineHttp(),
        )
        with (workspace / "reader.log").open("xb") as log:
            reader = subprocess.Popen(
                [
                    str(binary),
                    "serve",
                    "--data",
                    str(data),
                    "--listen",
                    f"127.0.0.1:{public}",
                    "--admin-listen",
                    f"127.0.0.1:{private}",
                    "--writer-memory-mb",
                    "128",
                    "--retrieval",
                    "global-merge",
                    "--max-sync-lag-s",
                    "120",
                ],
                stdout=log,
                stderr=log,
                env={**child_env, "ADMIN_SECRET": secret},
                start_new_session=True,
            )
            processes.append(reader)
            while True:
                remaining()
                if reader.poll() is not None:
                    raise ValueError("D1 checkpoint reader exited during startup")
                try:
                    initial = admin.status()
                    break
                except sync.Retryable:
                    time.sleep(min(0.05, remaining()))
            expiry = initial.get("expiry_coverage") or {}
            if (
                initial.get("source_cursor") is not None
                or initial.get("source_observation_revision") != 0
                or initial.get("import_complete") is not True
                or initial.get("full_source") is not True
                or initial.get("source_manifest") != header
                or (
                    initial.get("input_actors"),
                    initial.get("actor_count"),
                    initial.get("tombstone_count"),
                )
                != (
                    counts["input_documents"],
                    counts["live_actor_documents"],
                    counts["deleted_actor_documents"],
                )
            ):
                raise ValueError("D1 initial checkpoint identity/counts differ")
            if (
                expiry.get("verified_input_actors_sha256") != stream["actors_sha256"]
                or expiry.get("input_actors_checked") != counts["input_documents"]
                or expiry.get("import_complete") is not True
                or expiry.get("full_source") is not True
                or expiry.get("canonical_rules_source_sha256")
                != header["canonical_rules_source_sha256"]
                or any(
                    expiry.get("current", {}).get(key) != 0
                    for key in ("actors_with_unsupported_expiry", "actors_with_malformed_labels")
                )
            ):
                raise ValueError("D1 checkpoint footer/expiry coverage differs")
            record = {
                "schema_version": 1,
                "kind": "verified-checkpoint-import",
                "bootstrap_receipt_sha256": adapter.digest_json(bootstrap),
                "actors_sha256": stream["actors_sha256"],
                "verified_input_actors": initial["input_actors"],
                "raw_actor_rows": counts["raw_actor_rows"],
                "initial_live_actor_documents": initial["actor_count"],
                "initial_deleted_actor_documents": initial["tombstone_count"],
                "started_at": started_at,
                "completed_at": int(time.time()),
            }
            floor = header["source_watermark"]
            handoff = {
                "schema_version": 1,
                "kind": "sqlite-checkpoint-to-turso-v2",
                "prior_cursor_sha256": adapter.digest_json(None),
                "prior_observation_revision": 0,
                "checkpoint_reconciliation_sha256": adapter.digest_json(record),
                "bootstrap_receipt_sha256": adapter.digest_json(bootstrap),
                "source_origin_sha256": expected_source_origin_sha256,
                "replay_floor": floor,
                "first_remote_through": None,
            }
            cursor = {
                "schema_version": 1,
                "build_id": build_id,
                "snapshot_watermark": floor,
                "source_policy": current.SOURCE_POLICY,
                "through": floor,
                "cycle": None,
                "lookback_seconds": bootstrap["replay_overlap_seconds"],
                "actors": {"updated_at": floor, "did": ""},
                "tombstones": {"deleted_at": floor, "did": ""},
                "checkpoint_reconciliation": record,
                "checkpoint_handoff": handoff,
            }
            writer = current.ObservationAdmin(admin, sync)
            state, revision = writer.state([])
            if state.get("source_cursor") is not None or revision != 0:
                raise ValueError("D1 initial source CAS is no longer null/revision zero")
            writer.reconcile([], [], 0, None, cursor)
            source = accounted_source(
                sync,
                source_url,
                token,
                workspace / "source-budget.json",
                build_id,
                source_bytes,
                allow_test_loopback=allow_fixture,
                min_request_interval=min_request_interval,
            )
            source.http = DeadlineHttp()
            source.retries = 1
            source.backoff = 0
            cycles = pages = 0
            next_report = 0
            progress(
                "tantivy-checkpoint-live-catchup",
                elapsed_s=round(time.monotonic() - started, 1),
                pages=0,
                completed_cycles=0,
            )
            while True:
                remaining()
                observed = current.ObservedSynchronizer(
                    source,
                    admin,
                    sync,
                    policy_mode="snapshot",
                    anti_entropy_pages=0,
                    safety_lag=0,
                    page_size=500,
                    max_pages=64,
                    max_seconds=min(30, remaining()),
                    retries=1,
                    backoff=0,
                    lookback_seconds=bootstrap["replay_overlap_seconds"],
                ).run()
                pages += observed.get("pages", 0)
                if time.monotonic() >= next_report:
                    progress(
                        "tantivy-checkpoint-live-catchup",
                        elapsed_s=round(time.monotonic() - started, 1),
                        pages=pages,
                        completed_cycles=cycles,
                    )
                    next_report = time.monotonic() + 30
                if reader.poll() is not None:
                    raise ValueError("D1 checkpoint reader exited during source handoff")
                if not observed.get("caught_up"):
                    continue
                state, revision = writer.state([])
                cursor = copy.deepcopy(state["source_cursor"])
                through = cursor["through"]
                cycles += 1
                if cursor.get("cycle") is not None or any(
                    cursor.get(key) != {column: through, "did": ""}
                    for key, column in (("actors", "updated_at"), ("tombstones", "deleted_at"))
                ):
                    raise ValueError("Remote cycle did not commit complete discovery positions")
                if through >= bootstrap["checkpoint_receipt"]["source_clock_after"]:
                    break
                time.sleep(min(0.1, remaining()))
            cursor["checkpoint_handoff"]["first_remote_through"] = through
            writer.reconcile([], [], revision, state["source_cursor"], cursor)
            fields = {
                "source_kind": header["source_kind"],
                "source_policy": header["source_policy"],
                "source_build_id": header["build_id"],
                "source_watermark": floor,
                "source_actor_count": stream["input_documents"],
                "source_database_sha256": bootstrap["checkpoint_receipt"]["database_sha256"],
                "source_manifest_sha256": receipt_sha256,
                "source_actors_sha256": stream["actors_sha256"],
                "source_bootstrap": bootstrap,
                "rollback_native_anchor": header["rollback_native_anchor"],
                "legacy_build_id": header["legacy_build_id"],
                "normalize_version": header["normalize_version"],
                "canonical_rules_source_sha256": header["canonical_rules_source_sha256"],
                "checkpoint_reconciliation_sha256": adapter.digest_json(record),
                "checkpoint_handoff_sha256": adapter.digest_json(cursor["checkpoint_handoff"]),
            }
            final = admin.status()
            gates = adapter.checkpoint_source_gates(final, fields, int(time.time()))
            if final.get("ready") is not True or final.get("cutover_source_ready") is not False:
                raise ValueError(
                    "Checkpoint reader is not independently ready under distinct coverage"
                )
            accounting = source.accounting()
        return {
            "schema_version": 1,
            "kind": "sqlite-checkpoint-build",
            "fixture": allow_fixture,
            "data_path": str(data),
            "stream": stream,
            "manifest_fields": fields,
            "checkpoint_reconciliation": record,
            "checkpoint_handoff": cursor["checkpoint_handoff"],
            "source_accounting": accounting,
            "source_gates": gates,
            "completed_remote_cycles": cycles,
            "pages": pages,
            "consumer_verified": True,
            "live_handoff_verified": True,
            "provider_qualified": False,
            "wall_seconds": time.monotonic() - started,
        }
    except sync.SafeError as error:
        remaining()
        raise ValueError("Checkpoint source handoff failed") from error
    finally:
        if source is not None:
            source.close()
        for process in reversed(processes):
            stop(process)


def main():
    if len(sys.argv) != 3:
        raise ValueError("Private checkpoint stream request and receipt paths required")
    request = json.loads(Path(sys.argv[1]).read_bytes())
    seconds = request.pop("max_seconds")
    if type(seconds) is not int or not 1 <= seconds <= 3600:
        raise ValueError("Invalid producer process deadline")

    def expired(*_):
        raise TimeoutError("Checkpoint producer hard process deadline")

    signal.signal(signal.SIGALRM, expired)
    signal.alarm(seconds)
    try:
        database = request.pop("database")
        repository = request.pop("repository")
        request["receipt_bytes"] = request["receipt_bytes"].encode()
        request["native_manifest_bytes"] = request["native_manifest_bytes"].encode()
        result = stream_checkpoint(
            database, sys.stdout.buffer, repository, max_seconds=seconds, **request
        )
        Path(sys.argv[2]).write_bytes(encode(result))
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            json.dumps(
                {"error": "checkpoint_build_stream_failed", "error_class": type(error).__name__}
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
