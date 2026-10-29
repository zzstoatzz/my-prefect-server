"""Cloud publisher integration with the checksum-pinned Typeahead installer."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path

DIGEST = re.compile(r"[a-f0-9]{64}")


def progress(phase, **metadata):
    print(json.dumps({"phase": phase, **metadata}, sort_keys=True), flush=True)


def durable_receipt(path, value):
    temporary = path.with_suffix(path.suffix + ".partial")
    if path.is_symlink() or temporary.is_symlink():
        raise ValueError("Owned receipt paths cannot be symlinks")
    with temporary.open("wb") as stream:
        stream.write(json.dumps(value, sort_keys=True).encode() + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class TantivyAdapter:
    def __init__(self, installer: Path, expected_sha256: str):
        installer = Path(installer)
        if (
            not isinstance(expected_sha256, str)
            or not DIGEST.fullmatch(expected_sha256)
            or installer.is_symlink()
        ):
            raise ValueError("An immutable Typeahead installer checksum is required")
        with installer.open("rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024 or hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise ValueError("Typeahead installer source checksum mismatch")
        spec = importlib.util.spec_from_file_location("_mps_pinned_tantivy_installer", installer)
        if spec is None or spec.loader is None:
            raise ValueError("Cannot load Typeahead installer")
        module = importlib.util.module_from_spec(spec)
        exec(compile(raw, str(installer), "exec"), module.__dict__)
        self.installer = module
        self.source_sha256 = expected_sha256

    def validate_manifest(self, manifest: dict) -> dict:
        return self.installer.validate_artifact_manifest(manifest, manifest.get("active_build"))

    def validate_source(self, manifest: dict, source_raw: bytes) -> dict:
        return self.installer.validate_source_manifest(manifest, source_raw)

    def install(
        self, bundle: Path, destination: Path, manifest: dict, binary: Path, source_manifest: bytes
    ) -> dict:
        self.validate_source(manifest, source_manifest)
        return self.installer.install_bundle(bundle, destination, manifest, binary)

    def freeze(
        self, data: Path, destination: Path, manifest: dict, binary: Path, source_manifest: bytes
    ) -> dict:
        self.validate_source(manifest, source_manifest)
        return self.installer.freeze_bundle(data, destination, manifest, source_manifest, binary)

    def qualify(
        self,
        url: str,
        manifest: dict,
        installation: dict,
        provider_attestation: dict,
        source_manifest: bytes,
        *,
        allow_loopback: bool = False,
    ) -> dict:
        self.validate_source(manifest, source_manifest)
        return self.installer.qualify_runtime(
            url,
            manifest,
            installation,
            allow_loopback=allow_loopback,
            provider_attestation=provider_attestation,
        )


def import_snapshot(repository, source, workspace, binary, *, timeout, environment):
    import subprocess
    import sys
    import time

    manifest = json.loads((source / "manifest.json").read_bytes())
    receipt = workspace / "export-receipt.json"
    command = [
        sys.executable,
        str(repository / "services/tantivy-input/export.py"),
        "--manifest",
        str(source / "manifest.json"),
        "--database",
        str(source / "index.db"),
        "--prefix",
        str(source / "index.db.prefix"),
        "--build-id",
        manifest["active_build"],
        "--scratch-root",
        str(workspace),
        "--output",
        "-",
        "--receipt",
        str(receipt),
        "--owner",
        "typeahead-index-tantivy",
        "--max-seconds",
        str(timeout),
    ]
    processes = []
    started = time.monotonic()
    progress(
        "tantivy-import",
        source_build_id=manifest["active_build"],
        source_actors=manifest["actor_count"],
    )
    try:
        with (
            (workspace / "export.log").open("xb") as export_log,
            (workspace / "import.log").open("xb") as import_log,
        ):
            producer = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=export_log,
                env=environment,
            )
            processes.append(producer)
            assert producer.stdout is not None
            consumer = subprocess.Popen(
                [
                    str(binary),
                    "import",
                    "--data",
                    str(workspace / "data"),
                    "--input",
                    "-",
                    "--writer-memory-mb",
                    "128",
                ],
                stdin=producer.stdout,
                stdout=import_log,
                stderr=import_log,
                env=environment,
            )
            processes.append(consumer)
            producer.stdout.close()
            next_report = started + 30
            while consumer.poll() is None:
                now = time.monotonic()
                if now - started >= timeout:
                    raise TimeoutError("Tantivy export/import deadline expired")
                if now >= next_report:
                    progress(
                        "tantivy-import",
                        elapsed_seconds=round(now - started),
                        import_log_bytes=import_log.tell(),
                    )
                    next_report = now + 30
                time.sleep(0.1)
            producer.wait(timeout=10)
            if consumer.returncode or producer.returncode:
                raise RuntimeError(
                    "Cloud-local Tantivy export/import failed; bounded logs retained"
                )
    finally:
        for child in reversed(processes):
            stop_process(child)
    exported = json.loads(receipt.read_bytes())
    if (
        exported.get("partial") is not False
        or exported.get("emitted_actor_count") != manifest["actor_count"]
        or exported.get("source_unchanged") is not True
    ):
        raise ValueError("Tantivy export did not cover the immutable native source")
    progress(
        "tantivy-import-complete",
        source_actors=exported["emitted_actor_count"],
        elapsed_seconds=round(time.monotonic() - started, 3),
    )
    return exported


def stop_process(child):
    import subprocess

    if child.poll() is None:
        try:
            child.terminate()
        except ProcessLookupError:
            child.wait(timeout=5)
            return
        try:
            child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def builder_status(url):
    import urllib.request

    with urllib.request.urlopen(url + "/index/status", timeout=5) as response:
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("Builder status response too large")
    return json.loads(raw)


def reconcile_checkpoint(repository, workspace, binary, *, timeout, environment, source_bytes):
    import secrets
    import socket
    import subprocess
    import sys
    import time
    import urllib.error

    def port():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    public, private = port(), port()
    while private == public:
        private = port()
    environment = {**environment, "ADMIN_SECRET": secrets.token_urlsafe(32)}
    public_url = f"http://127.0.0.1:{public}"
    deadline = time.monotonic() + timeout
    next_report = time.monotonic()
    progress("tantivy-source-qualification", max_source_bytes=source_bytes, max_seconds=timeout)
    processes = []
    try:
        with (
            (workspace / "builder.log").open("xb") as server_log,
            (workspace / "replay.log").open("xb") as replay_log,
        ):
            server = subprocess.Popen(
                [
                    str(binary),
                    "serve",
                    "--data",
                    str(workspace / "data"),
                    "--listen",
                    f"127.0.0.1:{public}",
                    "--admin-listen",
                    f"127.0.0.1:{private}",
                    "--writer-memory-mb",
                    "128",
                    "--retrieval-strategy",
                    "global-merge",
                ],
                env=environment,
                stdout=server_log,
                stderr=server_log,
            )
            processes.append(server)
            while True:
                if server.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("Tantivy builder did not become available")
                try:
                    initial = builder_status(public_url)
                    if initial.get("import_complete") is True:
                        if initial.get("cutover_source_ready") is True:
                            return initial
                        break
                except (OSError, ValueError, urllib.error.URLError):
                    pass
                time.sleep(0.1)
            marker = workspace / "source-started"
            if marker.exists() and not (workspace / "source-budget.json").is_file():
                raise ValueError("Offline source accounting missing; refusing a fresh allowance")
            with marker.open("wb") as started_file:
                started_file.write(b"durable-source-accounting-v1\n")
                started_file.flush()
                os.fsync(started_file.fileno())
            replay = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).with_name("typeahead_source_budget.py")),
                    "--repository",
                    str(repository),
                    "--workspace",
                    str(workspace),
                    "--admin-url",
                    f"http://127.0.0.1:{private}",
                    "--status-url",
                    public_url + "/index/status",
                    "--max-seconds",
                    str(max(1, int(deadline - time.monotonic()))),
                    "--max-source-bytes",
                    str(source_bytes),
                ],
                env=environment,
                stdout=replay_log,
                stderr=replay_log,
            )
            processes.append(replay)
            while time.monotonic() < deadline:
                status = builder_status(public_url)
                if status.get("cutover_source_ready") is True:
                    progress(
                        "tantivy-source-qualified",
                        source_observation_revision=status.get("source_observation_revision"),
                    )
                    return status
                if time.monotonic() >= next_report:
                    completed = (status.get("source_cursor") or {}).get("reconciliation") or {}
                    progress(
                        "tantivy-source-qualification",
                        source_observation_revision=status.get("source_observation_revision"),
                        last_completed_round_at=completed.get("last_completed_at"),
                    )
                    next_report = time.monotonic() + 30
                if replay.poll() is not None or server.poll() is not None:
                    raise RuntimeError(
                        "Tantivy source qualification stopped before its gates passed"
                    )
                progress_file = workspace / "replay-status.json"
                if progress_file.is_file():
                    observed = json.loads(progress_file.read_bytes())
                    if (
                        observed.get("sweep_pause") == "budget"
                        or (observed.get("source_budget") or {}).get("remaining_source_bytes") == 0
                    ):
                        raise RuntimeError(
                            "Tantivy source qualification exhausted its accounted byte allowance; no automatic retry"
                        )
                time.sleep(0.5)
            raise TimeoutError(
                "Tantivy source/policy/full-round qualification exceeded its deadline"
            )
    finally:
        for child in reversed(processes):
            stop_process(child)


def prepare_generation(
    repository: Path,
    source: Path,
    workspace: Path,
    binary: Path,
    environment: dict,
    *,
    resume=False,
):
    import subprocess
    import time

    expected_revision = environment["INDEX_TANTIVY_SOURCE_REVISION"]
    for name in ("TURSO_URL", "TURSO_AUTH_TOKEN"):
        if not environment.get(name):
            raise ValueError("Missing cloud-only source credential configuration: " + name)
    if not re.fullmatch(r"[a-f0-9]{40}", expected_revision):
        raise ValueError("Tantivy producer requires an immutable Typeahead source revision")
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()
    subprocess.run(
        [
            "git",
            "diff",
            "--quiet",
            "HEAD",
            "--",
            "services/tantivy-input",
            "scripts/snapshot_refresh",
            "scripts/snapshot_compaction",
        ],
        cwd=repository,
        check=True,
        timeout=10,
    )
    if revision != expected_revision:
        raise ValueError("Tantivy producer source revision mismatch")
    expected_binary = environment["INDEX_TANTIVY_BINARY_SHA256"]
    with binary.open("rb") as stream:
        if (
            not DIGEST.fullmatch(expected_binary)
            or hashlib.file_digest(stream, "sha256").hexdigest() != expected_binary
        ):
            raise ValueError("Tantivy producer reader binary checksum mismatch")
    adapter = TantivyAdapter(
        repository / "scripts/snapshot_refresh/tantivy_install.py",
        environment["INDEX_TANTIVY_ADAPTER_SHA256"],
    )
    timeout = int(environment.get("INDEX_TANTIVY_PREPARE_SECONDS", "10800"))
    source_budget = int(environment.get("INDEX_TANTIVY_MAX_SOURCE_BYTES", str(8 * 1024**3)))
    if not 1 <= timeout <= 10800 or not 2 <= source_budget <= 8 * 1024**3:
        raise ValueError("Tantivy preparation exceeds the current bounded qualification settings")
    if workspace.is_symlink() or workspace.absolute() != workspace.resolve():
        raise ValueError("A canonical isolated Tantivy builder directory is required")
    source_raw = (source / "manifest.json").read_bytes()
    native = json.loads(source_raw)
    identity = {
        "source_revision": revision,
        "adapter_sha256": adapter.source_sha256,
        "binary_sha256": expected_binary,
        "source_manifest_sha256": hashlib.sha256(source_raw).hexdigest(),
        "max_source_bytes": source_budget,
    }
    if workspace.exists():
        if (
            not resume
            or (workspace / "owner").read_text() != "typeahead-index-tantivy-v1\n"
            or json.loads((workspace / "identity.json").read_bytes()) != identity
        ):
            raise ValueError("Tantivy preparation resume identity mismatch")
    else:
        if resume:
            raise ValueError("No owned Tantivy preparation exists to resume")
        workspace.mkdir(parents=True)
        (workspace / "owner").write_text("typeahead-index-tantivy-v1\n")
        durable_receipt(workspace / "identity.json", identity)
    destination = workspace / "publication"
    if destination.exists():
        raw = (destination / "manifest.json").read_bytes()
        adapter.validate_source(json.loads(raw), source_raw)
        if not (destination / "index.tar").is_file():
            published = json.loads((workspace / "published.json").read_bytes())
            if (
                published.get("remote_readback_passed") is not True
                or published.get("manifest_sha256") != hashlib.sha256(raw).hexdigest()
            ):
                raise ValueError("Retired local payload lacks a verified publication receipt")
        return destination
    started = time.monotonic()
    if resume:
        exported = json.loads((workspace / "export-receipt.json").read_bytes())
        check = subprocess.run(
            [str(binary), "status", "--data", str(workspace / "data")],
            capture_output=True,
            check=True,
            timeout=60,
        )
        if (
            len(check.stdout) > 8 * 1024**2
            or json.loads(check.stdout).get("import_complete") is not True
        ):
            raise ValueError("Incomplete initial import cannot be resumed as source reconciliation")
    else:
        exported = import_snapshot(
            repository, source, workspace, binary, timeout=timeout, environment=environment
        )
    remaining = timeout - int(time.monotonic() - started)
    if remaining <= 0:
        raise TimeoutError("Tantivy preparation deadline expired after import")
    status = reconcile_checkpoint(
        repository,
        workspace,
        binary,
        timeout=remaining,
        environment=environment,
        source_bytes=source_budget,
    )
    metadata = status["expiry_coverage"]
    manifest = {
        "engine": "tantivy",
        "manifest_version": 1,
        "artifact_format": "tantivy-directory-tar-v1",
        "backend_format": status["format"],
        "active_build": status["build_id"],
        "normalize_version": status["normalize_version"],
        "plan_version": status["plan_version"],
        "source_policy": status["source_cursor"]["source_policy"],
        "sha256": "0" * 64,
        "byte_size": 1,
        "binary_sha256": expected_binary,
        "canonical_rules_source_sha256": metadata["canonical_rules_source_sha256"],
        "retrieval_strategy": "global-merge",
        "source_actor_count": native["actor_count"],
        "source_build_id": native["active_build"],
        "legacy_build_id": native["legacy_twin"]["build_id"],
        "source_manifest_sha256": hashlib.sha256(source_raw).hexdigest(),
        "source_watermark": native["source_watermark"],
        "source_database_sha256": native["sha256"],
        "source_prefix_sha256": native["prefix_map"]["sha256"],
        "source_actors_sha256": exported["actors_sha256"],
        "object_key": f"releases/search-tantivy/{status['build_id']}/index.tar",
        "files": [{"path": "index/meta.json", "byte_size": 1, "sha256": "0" * 64}],
    }
    portable = adapter.freeze(workspace / "data", destination, manifest, binary, source_raw)
    progress(
        "tantivy-checkpoint-frozen",
        active_build=portable["active_build"],
        byte_size=portable["byte_size"],
    )
    (workspace / "preparation.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "engine": "tantivy",
                "source_revision": revision,
                "adapter_sha256": adapter.source_sha256,
                "binary_sha256": expected_binary,
                "active_build": portable["active_build"],
                "source_build_id": portable["source_build_id"],
                "promotion_permitted": False,
            },
            sort_keys=True,
        )
        + "\n"
    )
    return destination


def main():
    import argparse
    import os
    import signal

    parser = argparse.ArgumentParser(
        description="Cloud-owned isolated Tantivy checkpoint preparation"
    )
    for name in ("repository", "source", "workspace", "binary"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    def interrupted(*_):
        raise SystemExit("Tantivy preparation interrupted; owned checkpoint retained")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    prepare_generation(
        args.repository,
        args.source,
        args.workspace,
        args.binary,
        dict(os.environ),
        resume=args.resume,
    )


if __name__ == "__main__":
    main()
