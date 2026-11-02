"""
Publish and roll out Typeahead's Tantivy search generations from the cloud worker.

The flow clones the immutable Typeahead revision named by
`INDEX_TANTIVY_SOURCE_REVISION`, takes one cloud checkpoint of the Turso source,
imports it into a frozen Tantivy bundle and publishes that bundle to R2. With
`automatic_refresh`, it then prepares the inactive Tantivy slot, promotes it
behind the release proof and prunes unrouted Tantivy artifacts. Explicit
`tantivy_operation` requests run one slot prepare, cancel or promote step.

Runtime secrets are resolved from deployment Secret-block references. The flow
passes them to child processes without reading Secret blocks itself.
"""

import hashlib
import json
import logging
import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import time
from collections import deque
from contextlib import contextmanager, suppress
from pathlib import Path

from prefect import flow, get_run_logger, task
from prefect.exceptions import MissingContextError
from prefect.tasks import exponential_backoff

REPO_URL = "https://tangled.org/zzstoatzz.io/typeahead.git"
REPO_URL_FALLBACK = "https://github.com/zzstoatzz/typeahead.git"

# persistent working root. process flow runs execute in an ephemeral /tmp, so
# the deployment sets INDEXER_HOME to a real persistent path on the build host.
INDEXER_HOME = Path(os.environ.get("INDEXER_HOME") or (Path.home() / ".typeahead-index"))


class ProcessExecutionError(RuntimeError):
    def __init__(self, command: str, returncode: int, output: str):
        self.returncode = returncode
        self.output = output
        super().__init__(f"{command} exited {returncode}\n{output}")


def _stream(cmd: list[str], cwd: Path, env: dict, timeout: int) -> None:
    """Run a subprocess, streaming stdout+stderr to the run logger live.

    The build/run are long (a full snapshot is the better part of an hour);
    capture-then-dump would leave the run silent the whole time. Stream so
    progress is visible (see the typeahead 'progress indicators' lesson).
    """
    try:
        logger = get_run_logger()
    except MissingContextError:
        logger = logging.getLogger(__name__)
    output: deque[str] = deque(maxlen=50)
    deadline = time.monotonic() + timeout

    def check_disk_reserve() -> None:
        minimum = int(env.get("INDEX_MIN_REMAIN_GIB", "0")) * 2**30
        if minimum and shutil.disk_usage(env["INDEX_BUILD_ROOT"]).free < minimum:
            raise RuntimeError("Offline build stopped before exhausting disk reserve")

    check_disk_reserve()
    proc = subprocess.Popen(
        [sys.executable, "-m", "mps.process_guard", "--parent", str(os.getpid()), "--", *cmd],
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    pending = b""

    def log_line(line: bytes) -> None:
        text = line.decode("utf-8", errors="replace").rstrip()
        logger.info(text)
        output.append(text[-2000:])

    try:
        assert proc.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while selector.get_map():
                check_disk_reserve()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(cmd, timeout)
                for key, _ in selector.select(min(1.0, remaining)):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        break
                    pending += chunk
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        log_line(line)
                    if len(pending) > 65536:
                        log_line(pending)
                        pending = b""
            if pending:
                log_line(pending)
        code = proc.wait(timeout=max(0, deadline - time.monotonic()))
    except BaseException as exc:
        with suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGTERM)
        with suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=10)
        with suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        if isinstance(exc, subprocess.TimeoutExpired):
            raise RuntimeError(f"{cmd[0]} exceeded {timeout}s timeout") from None
        raise
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
    if code != 0:
        raise ProcessExecutionError(cmd[0], code, "\n".join(output))


@task(
    retries=2,
    retry_delay_seconds=exponential_backoff(backoff_factor=10),
    retry_jitter_factor=1,
)
def clone_repo(revision: str) -> Path:
    """Check out one exact Typeahead commit into a fresh owned directory."""
    from uuid import uuid4

    logger = get_run_logger()
    root = INDEXER_HOME / "tantivy-repos"
    if root.is_symlink() or root.resolve() != root.absolute():
        raise ValueError("An owned canonical producer checkout root is required")
    destination = root / uuid4().hex
    destination.parent.mkdir(parents=True, exist_ok=True)
    for url in (REPO_URL, REPO_URL_FALLBACK):
        for command in (
            ["git", "init", str(destination)],
            ["git", "-C", str(destination), "remote", "add", "origin", url],
            ["git", "-C", str(destination), "fetch", "--depth", "1", "origin", revision],
            ["git", "-C", str(destination), "checkout", "--detach", revision],
        ):
            r = subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)
            if r.returncode:
                break
        if r.returncode == 0:
            logger.info(f"cloned {url}")
            return destination
        logger.warning(f"clone failed for {url}: {r.stderr.strip()}")
        if destination.exists():
            shutil.rmtree(destination)
    raise RuntimeError("clone failed from both tangled and github")


@contextmanager
def pinned_typeahead():
    """Clone the pinned revision and load the controller's installer from it."""
    revision = os.environ.get("INDEX_TANTIVY_SOURCE_REVISION", "")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Tantivy operations require an immutable Typeahead source revision")
    repo = clone_repo(revision)
    previous_adapter = os.environ.get("INDEX_TANTIVY_ADAPTER_PATH")
    os.environ["INDEX_TANTIVY_ADAPTER_PATH"] = str(
        repo / "scripts/snapshot_refresh/tantivy_install.py"
    )
    try:
        yield repo
    finally:
        if previous_adapter is None:
            os.environ.pop("INDEX_TANTIVY_ADAPTER_PATH", None)
        else:
            os.environ["INDEX_TANTIVY_ADAPTER_PATH"] = previous_adapter
        shutil.rmtree(repo)


@task(retries=0, timeout_seconds=5400)
def prepare_checkpoint_generation(repo: Path) -> tuple[Path, Path]:
    from mps.typeahead_checkpoint_release import run_cloud_release

    return run_cloud_release(repo)


def tantivy_workspace(generation: Path) -> Path:
    workspace = generation.parent
    if (
        workspace.parent != INDEXER_HOME / "tantivy-builds"
        or workspace.resolve() != workspace
        or generation.name != "publication"
        or generation.resolve() != generation
        or (workspace / "owner").read_text() != "typeahead-index-tantivy-v1\n"
    ):
        raise ValueError("Tantivy publication workspace ownership mismatch")
    return workspace


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
def publish_tantivy_generation(
    repo: Path, generation: Path, source: Path, config: Path | None = None
) -> Path:
    from mps.typeahead_tantivy import durable_receipt

    tantivy_workspace(generation)

    verify_only = not (generation / "index.tar").is_file()
    if verify_only:
        published = json.loads((generation.parent / "published.json").read_bytes())
        if (
            published.get("remote_readback_passed") is not True
            or published.get("manifest_sha256")
            != hashlib.sha256((generation / "manifest.json").read_bytes()).hexdigest()
        ):
            raise ValueError("Retired local payload lacks a verified publication receipt")

    _stream(
        [
            sys.executable,
            str(repo / "scripts/snapshot_compaction/publish.py"),
            "--engine",
            "tantivy",
            "--generation",
            str(generation),
            "--source-manifest",
            str(source / "manifest.json"),
        ]
        + (["--verify-only"] if verify_only else [])
        + (["--config", str(config)] if config is not None else []),
        repo,
        dict(os.environ),
        timeout=7200,
    )
    manifest_raw = (generation / "manifest.json").read_bytes()
    durable_receipt(
        generation.parent / "published.json",
        {
            "engine": "tantivy",
            "build_id": json.loads(manifest_raw)["active_build"],
            "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
            "remote_readback_passed": True,
        },
    )
    return generation


@task(retries=0)
def retire_tantivy_generation(generation: Path) -> Path:
    from mps.typeahead_tantivy import durable_receipt

    workspace = tantivy_workspace(generation)
    published = json.loads((workspace / "published.json").read_bytes())
    if (
        published.get("remote_readback_passed") is not True
        or published.get("manifest_sha256")
        != hashlib.sha256((generation / "manifest.json").read_bytes()).hexdigest()
    ):
        raise ValueError("Verified publication receipt required before retirement")
    payloads = (workspace / "data", generation / "index.tar")
    if any(path.is_symlink() for path in payloads):
        raise ValueError("Tantivy publication cleanup refuses symlinks")
    for path in payloads:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    durable_receipt(
        workspace / "retired.json",
        {
            "engine": "tantivy",
            "manifest_sha256": published["manifest_sha256"],
            "local_payloads_retired": True,
        },
    )
    return generation


@flow(name="typeahead-index", log_prints=True, timeout_seconds=43200)
def typeahead_index(
    automatic_refresh: bool = False,
    resume_rollout: bool = False,
    tantivy_operation: dict | None = None,
):
    """Publish one isolated Tantivy generation, or roll one out.

    With no arguments the flow publishes an immutable generation without
    touching routing. `automatic_refresh` continues the serving Tantivy route
    into the inactive slot; `resume_rollout` only finishes a pending automatic
    preparation. `tantivy_operation` runs one explicit slot operation.
    """
    if tantivy_operation is not None:
        if automatic_refresh or resume_rollout:
            raise ValueError("Explicit slot operations cannot be combined with automatic refresh")
        from mps.typeahead_tantivy_slots import run_slot_operation

        with pinned_typeahead():
            return run_slot_operation(tantivy_operation, get_run_logger())
    if resume_rollout and not automatic_refresh:
        raise ValueError("Rollout recovery requires automatic refresh")
    if not automatic_refresh:
        with pinned_typeahead() as repo:
            prepared, source = prepare_checkpoint_generation(repo)
            publish_tantivy_generation(repo, prepared, source)
            retire_tantivy_generation(prepared)
        return None
    from mps.typeahead_refresh import Refresh, prune_snapshot_artifacts
    from mps.typeahead_tantivy_automatic import refresh, settings
    from mps.typeahead_tantivy_slots import Slots

    config = settings(os.environ)
    service = Refresh(get_run_logger())
    try:
        with pinned_typeahead() as repo:
            publication = None

            def publish():
                nonlocal publication
                publication, source = prepare_checkpoint_generation(repo)
                publish_tantivy_generation(repo, publication, source)
                return json.loads((publication / "manifest.json").read_bytes())["active_build"]

            slots = Slots(service, INDEXER_HOME / "tantivy-slot-journals")
            report = refresh(
                service,
                slots,
                publish,
                release_key=config["release_key"],
                release_sha256=config["release_sha256"],
                resume_only=resume_rollout,
            )
            if report.get("phase") != "promoted" or report.get("public_verified") is not True:
                raise RuntimeError("Automatic promotion is not confirmed; retention fenced")
            if publication is not None:
                retire_tantivy_generation(publication)
            prune_snapshot_artifacts()
            return report
    finally:
        service.close()
