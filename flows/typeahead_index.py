"""
Build the offline Typeahead snapshot on the cloud process worker and publish to R2.

The flow clones the pinned builder source, compiles it with the host toolchain,
exports the actor corpus, builds on local SSD, and publishes an immutable snapshot
and its manifest. Compact publication also retains a legacy twin at the same
watermark. Search installs new generations while the previous snapshot serves.
Freshness monitoring requires a successful publication at least every four days.

Runtime secrets are resolved from deployment Secret-block references. The flow
passes them to child processes without reading Secret blocks itself. Deployment
configuration controls staging versus production publication, local SSD and archive
paths, and minimum free-space reserves. Completed generations move to the archive
only after copied artifacts match their published checksums.
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
import tempfile
import time
import urllib.error
import urllib.request
from collections import deque
from contextlib import suppress
from pathlib import Path
from typing import Literal

from mps.zig_toolchain import zig_for
from prefect import flow, get_run_logger, task
from prefect.exceptions import MissingContextError
from prefect.tasks import exponential_backoff

# typeahead's canonical remote is tangled (there is NO github mirror yet —
# unlike my-prefect-server). The github URL is a fallback for if/when a mirror
# is pushed. The point either way: the binary is always rebuilt from a REMOTE
# source, never from anything pre-staged on the build host.
REPO_URL = "https://tangled.org/zzstoatzz.io/typeahead.git"
REPO_URL_FALLBACK = "https://github.com/zzstoatzz/typeahead.git"

# persistent working root for the repo + build artifacts. process flow runs
# execute in an ephemeral /tmp, so this MUST be a real persistent path — the
# deployment sets INDEXER_HOME to wherever the host has disk (e.g. an NVMe
# mount). No machine-specific path is baked into this code: the fallback is
# under the runtime user's home, so this flow reinstantiates on any host that
# has the deployment's env + the install.sh prereqs. Nothing here assumes a
# particular box.
INDEXER_HOME = Path(os.environ.get("INDEXER_HOME") or (Path.home() / ".typeahead-index"))
REPO_DIR = INDEXER_HOME / "repo"


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


def quiet_command(repo: Path, command: list[str]) -> list[str]:
    if os.environ.get("INDEX_QUIET") != "1":
        return command
    supervisor = repo / "scripts/snapshot_compaction/quiet.py"
    if not supervisor.is_file():
        raise RuntimeError("quiet supervisor missing from the selected typeahead revision")
    return [sys.executable, str(supervisor), "--", *command]


@task(
    retries=2,
    retry_delay_seconds=exponential_backoff(backoff_factor=10),
    retry_jitter_factor=1,
)
def clone_repo(ref: str | None = None, isolated: bool = False) -> Path:
    """Fresh shallow clone of typeahead (tangled, github fallback). `ref` names
    a branch to build instead of the default one."""
    logger = get_run_logger()
    destination = REPO_DIR
    if isolated:
        from uuid import uuid4

        root = INDEXER_HOME / "tantivy-repos"
        if root.is_symlink() or root.resolve() != root.absolute():
            raise ValueError("An owned canonical producer checkout root is required")
        destination = root / uuid4().hex
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    branch = ["--branch", ref] if ref else []
    for url in (REPO_URL, REPO_URL_FALLBACK):
        if isolated and ref and re.fullmatch(r"[0-9a-f]{40}", ref):
            commands = [
                ["git", "init", str(destination)],
                ["git", "-C", str(destination), "remote", "add", "origin", url],
                ["git", "-C", str(destination), "fetch", "--depth", "1", "origin", ref],
                ["git", "-C", str(destination), "checkout", "--detach", ref],
            ]
        else:
            commands = [["git", "clone", "--depth", "1", *branch, url, str(destination)]]
        for command in commands:
            r = subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)
            if r.returncode:
                break
        if r.returncode == 0:
            logger.info(f"cloned {url}")
            return destination
        logger.warning(f"clone failed for {url}: {r.stderr.strip()}")
        if isolated and destination.exists():
            shutil.rmtree(destination)
    raise RuntimeError("clone failed from both tangled and github")


@task(retries=1, retry_delay_seconds=30)
def build_binary(repo_dir: Path) -> Path:
    """Build pinned source with its vendored dependencies or explicit legacy fallback."""
    services = repo_dir / "services"
    binary = services / "zig-out" / "bin" / "typeahead-ingester"
    env = {**os.environ}
    zig = zig_for(services)
    cache = INDEXER_HOME / "zig-cache"
    global_cache = INDEXER_HOME / "zig-global-cache"
    env["ZIG_GLOBAL_CACHE_DIR"] = str(global_cache)
    vendor = services / ".zig-pkg-vendor"
    if vendor.is_dir():
        shutil.copytree(vendor, global_cache / "p", dirs_exist_ok=True)
    command = quiet_command(
        repo_dir,
        [
            zig,
            "build",
            "-Doptimize=ReleaseSafe",
            "-j1",
            "--cache-dir",
            str(cache),
        ],
    )
    logger = get_run_logger()
    logger.info(f"building with {zig}")
    try:
        _stream(command, services, env, timeout=10800)
    except ProcessExecutionError as primary:
        fallback = services / "build.zig.zon.gh"
        if not fallback.is_file():
            primary.add_note(
                "Selected Typeahead revision has no services/build.zig.zon.gh fallback; "
                "preserving the original build failure."
            )
            raise
        logger.warning("build failed; retrying with the selected revision's dependency fallback")
        manifest = services / "build.zig.zon"
        original = manifest.read_bytes()
        try:
            shutil.copyfile(fallback, manifest)
            _stream(command, services, env, timeout=10800)
        except ProcessExecutionError as secondary:
            raise secondary from primary
        finally:
            manifest.write_bytes(original)
    if not binary.is_file():
        raise RuntimeError(f"build reported success but binary missing at {binary}")
    return binary


@task(retries=3, retry_delay_seconds=[30, 120, 300])
def run_indexer(
    binary: Path, local_only: bool = False, reuse_source: str | None = None
) -> Path | None:
    """Run MODE=indexer: read Turso → build snapshot → publish to R2 → exit.

    Channel/arming come from the inherited env (INDEX_CHANNEL, INDEX_ALLOW_PROD).
    Build root must be a persistent NVMe path (INDEX_BUILD_ROOT). Long timeout —
    a full prod build is the better part of an hour.
    """
    logger = get_run_logger()
    build_root = os.environ.get("INDEX_BUILD_ROOT", str(INDEXER_HOME / "build"))
    Path(build_root).mkdir(parents=True, exist_ok=True)
    channel = os.environ.get("INDEX_CHANNEL", "local")
    logger.info(f"MODE=indexer channel={channel} build_root={build_root}")
    env = {**os.environ, "MODE": "indexer", "INDEX_BUILD_ROOT": build_root}
    if reuse_source is not None:
        path = Path(reuse_source)
        if not path.is_absolute() or not path.is_file():
            raise ValueError("reuse_source must name an existing absolute checkpoint path")
        env["INDEX_REUSE_SOURCE"] = str(path)
        env["INDEX_SOURCE"] = "export_http"
    existing = set(Path(build_root).glob("build-*/manifest.json")) if local_only else set()
    if local_only:
        if env.get("INDEX_PUBLISH_ONLY"):
            raise ValueError("compact source build cannot use INDEX_PUBLISH_ONLY")
        env["INDEX_CHANNEL"] = "local"
    # tell the binary where rclone is (it shells out for the R2 upload). Resolve
    # the absolute path here rather than relying on the binary PATH-searching —
    # install.sh puts rclone under ~/.local/bin, not the /usr/local/bin the Fly
    # image uses. Fail loudly if it's missing (the upload would fail anyway).
    rclone = shutil.which("rclone")
    if not rclone:
        raise RuntimeError(
            "rclone not found on PATH — run typeahead deploy/home-indexer/install.sh"
        )
    env["INDEX_RCLONE_BIN"] = rclone
    # the shared `turso-url` block holds the full libsql:// URL (atlas's client
    # wants it), but the indexer's TursoClient wants a BARE host. Normalize here
    # so one block serves both.
    if "://" in env.get("TURSO_URL", ""):
        env["TURSO_URL"] = env["TURSO_URL"].split("://", 1)[1]
    _stream(quiet_command(binary.parents[3], [str(binary)]), binary.parent, env, timeout=28800)
    if local_only:
        created = set(Path(build_root).glob("build-*/manifest.json")) - existing
        if len(created) != 1:
            raise RuntimeError("expected exactly one completed legacy source build")
        return created.pop().parent
    return None


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
def ensure_compactor(repo: Path) -> Path:
    suffix = "dylib" if sys.platform == "darwin" else "so"
    library = repo / "services/zig-out/lib" / ("libtypeahead_compact." + suffix)
    if not library.is_file():
        zig = zig_for(repo / "services")
        _stream(
            [
                zig,
                "build",
                "compact",
                "-Doptimize=ReleaseSafe",
                "-j1",
                "--cache-dir",
                str(INDEXER_HOME / "zig-cache"),
            ],
            repo / "services",
            dict(os.environ),
            timeout=600,
        )
    if not library.is_file():
        raise RuntimeError("Native compactor artifact is missing after cache restoration")
    return library


def compact_target() -> tuple[str, str] | None:
    channel = os.environ.get("INDEX_CHANNEL", "local")
    if channel == "local":
        return None
    if channel == "prod":
        if os.environ.get("INDEX_ALLOW_PROD") != "1":
            raise ValueError("production publication is not armed")
        return "builds", "latest.json"
    if channel == "staging":
        name = os.environ.get("INDEX_STAGING_NAME", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError("staging requires a safe INDEX_STAGING_NAME")
        return f"staging/{name}", f"staging/{name}/latest.json"
    if channel == "managed":
        return "releases/search", "control/builds/latest.json"
    raise ValueError("unknown INDEX_CHANNEL")


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
def publish_compact(repo: Path, source: Path, update_pointer: bool = True) -> Path:
    target = compact_target()
    if not update_pointer and target != ("releases/search", "control/builds/latest.json"):
        raise ValueError("Immutable source publication requires the managed release namespace")
    legacy = json.loads((source / "manifest.json").read_text())
    compact_id = legacy["active_build"] + "-px05"
    output = source.parent / f"build-{compact_id}"
    scripts = repo / "scripts/snapshot_compaction"
    env = dict(os.environ)
    if output.exists() and not (output / "manifest.json").is_file():
        shutil.rmtree(output)
    if not output.exists():
        _stream(
            quiet_command(
                repo,
                [
                    sys.executable,
                    str(scripts / "convert.py"),
                    "--unlimited-cpu",
                    "--source",
                    str(source / "index.db"),
                    "--manifest",
                    str(source / "manifest.json"),
                    "--output",
                    str(output),
                    "--build-id",
                    compact_id,
                    "--object-prefix",
                    target[0] if target else "builds",
                ],
            ),
            repo,
            env,
            timeout=7200,
        )
    candidate = json.loads((output / "manifest.json").read_text())
    if (
        candidate["active_build"] != compact_id
        or candidate["legacy_twin"]["build_id"] != legacy["active_build"]
        or candidate["source_watermark"] != legacy["source_watermark"]
    ):
        raise ValueError("existing compact generation does not match source")
    if target == ("releases/search", "control/builds/latest.json"):
        return publish_completed_generation.fn(repo, str(output), update_pointer=update_pointer)
    if target is not None:
        _stream(
            quiet_command(
                repo,
                [
                    sys.executable,
                    str(scripts / "publish.py"),
                    "--generation",
                    str(output),
                    "--legacy-database",
                    str(source / "index.db"),
                    "--legacy-manifest",
                    str(source / "manifest.json"),
                    "--pointer",
                    target[1],
                ],
            ),
            repo,
            env,
            timeout=7200,
        )
    return output


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
def publish_completed_generation(repo: Path, generation: str, update_pointer: bool = True) -> Path:
    source = Path(generation).resolve()
    roots = [
        Path(os.environ["INDEX_BUILD_ROOT"]).resolve(),
        Path(os.environ["INDEX_ARCHIVE_ROOT"]).resolve(),
    ]
    if not any(source.is_relative_to(root) for root in roots):
        raise ValueError("Completed generation must belong to this publisher")
    if compact_target() != ("releases/search", "control/builds/latest.json"):
        raise ValueError("Completed-generation recovery requires the managed channel")
    manifest = json.loads((source / "manifest.json").read_bytes())
    if (
        manifest["manifest_version"] != 2
        or not 0 <= time.time() - manifest["source_watermark"] < 3 * 86400
    ):
        raise ValueError("Completed generation must be a fresh compact snapshot")
    bid = manifest["active_build"]
    twin_id = manifest["legacy_twin"]["build_id"]
    if not all(re.fullmatch(r"b[0-9]{10}-[A-Za-z0-9_-]+", value) for value in [bid, twin_id]):
        raise ValueError("Invalid completed generation identity")
    legacy_path = source.parent / ("build-" + twin_id)
    legacy = json.loads((legacy_path / "manifest.json").read_bytes())
    if (
        legacy["active_build"] != twin_id
        or legacy["source_watermark"] != manifest["source_watermark"]
    ):
        raise ValueError("Completed generation twin mismatch")
    output = INDEXER_HOME / "completed-publications" / bid
    output.mkdir(parents=True, exist_ok=True)
    for name in ["index.db", "index.db.prefix"]:
        data = (source / name).resolve()
        if not any(data.is_relative_to(root) for root in roots):
            raise ValueError("Completed artifact leaves the publisher roots")
        link = output / name
        if link.is_symlink() or (link.exists() and link.samefile(data)):
            link.unlink()
        elif link.exists():
            raise ValueError("Completed-publication artifact is not a managed link")
        link.symlink_to(data)
    legacy["snapshot_url"] = f"releases/search/{twin_id}/index.db"
    raw = json.dumps(legacy, separators=(",", ":")).encode()
    legacy_manifest = output / "legacy-manifest.json"
    legacy_manifest.write_bytes(raw)
    manifest["snapshot_url"] = f"releases/search/{bid}/index.db"
    manifest["prefix_map"]["object_key"] = manifest["snapshot_url"] + ".prefix"
    manifest["legacy_twin"]["manifest"] = {
        "object_key": f"releases/search/{twin_id}/manifest.json",
        "byte_size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    (output / "manifest.json").write_text(json.dumps(manifest, separators=(",", ":")))
    _stream(
        [
            sys.executable,
            str(repo / "scripts/snapshot_compaction/publish.py"),
            "--generation",
            str(output),
            "--legacy-database",
            str(legacy_path / "index.db"),
            "--legacy-manifest",
            str(legacy_manifest),
        ]
        + (["--pointer", "control/builds/latest.json"] if update_pointer else ["--immutable-only"]),
        repo,
        dict(os.environ, RCLONE_COPY_LINKS="true"),
        timeout=7200,
    )
    return output


# builds the flow leaves on disk after a successful publish. The published
# snapshot lives in R2 and App B promotes from there, so a build directory is
# a reproducible artifact, not a serving dependency. Two is the serving build
# plus its predecessor for an offline differential (docs/evals in typeahead).
KEEP_BUILDS = 2
FRESHNESS_URL = "https://typeahead.waow.tech/health/freshness"


def serving_build_id(url: str = FRESHNESS_URL, timeout: int = 10) -> str | None:
    """The build_id the search service is serving, or None if unreachable."""
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "fleet-health/1"})
        try:
            response = urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code != 503:
                raise
            response = exc
        with response:
            health = json.load(response)
        if not isinstance(health, dict):
            return None
        build_id = health.get("build_id")
        if (
            health.get("ok") is True
            and health.get("probe_ok") is True
            and isinstance(build_id, str)
            and re.fullmatch(r"(?:compact-)?b[0-9]{10}-[A-Za-z0-9_-]+", build_id)
        ):
            return build_id
        return None
    except (OSError, ValueError):
        return None


def builds_to_prune(
    build_dirs: list[Path], serving: str | set[str] | None, keep: int = KEEP_BUILDS
) -> list[Path]:
    """Keep the newest generations and the serving one, including format twins.

    Build ids start with a unix-second timestamp (`b<seconds>-<hex>`), so
    lexical order is chronological. Never prunes when the serving id is
    unknown: a stale snapshot is a state we recover from, a deleted build
    that a person was about to inspect is not.
    """
    if not serving:
        return []
    ordered = sorted(build_dirs, key=lambda p: p.name, reverse=True)

    def generation(name: str) -> str:
        return name.removesuffix("-px05")

    newest = sorted({generation(p.name) for p in ordered}, reverse=True)[:keep]
    serving_ids = {serving} if isinstance(serving, str) else serving
    protected = {*newest, *(generation(f"build-{build}") for build in serving_ids)}
    return [p for p in ordered if generation(p.name) not in protected]


@task
def prune_builds(build_root: str | None = None) -> int:
    """Delete old build directories under the build root; returns bytes freed.

    Runs before new-build disk admission and after successful archival.
    Affects nobody but this flow: no other
    deployment reads the build root, and the search service serves from R2.
    """
    try:
        logger = get_run_logger()
    except MissingContextError:
        logger = logging.getLogger(__name__)
    root = Path(build_root or os.environ.get("INDEX_BUILD_ROOT", str(INDEXER_HOME / "build")))
    if not root.exists():
        return 0
    build_dirs = [
        p
        for p in root.iterdir()
        if p.is_dir() and not p.is_symlink() and p.name.startswith("build-")
    ]
    from mps.typeahead_refresh import protected_source_build_ids

    try:
        serving = protected_source_build_ids(logger)
    except (OSError, ValueError, RuntimeError, KeyError):
        serving = None
    if not serving:
        logger.warning("Routed source identities unavailable; pruning nothing")
        if len(build_dirs) > KEEP_BUILDS * 2:
            raise RuntimeError("Build retention blocked: serving identity unavailable")
        return 0
    victims = builds_to_prune(build_dirs, serving)
    freed = 0
    for victim in victims:
        if protected_source_build_ids(logger) != serving:
            raise RuntimeError("Routing source identities changed; refusing local cleanup")
        size = sum(f.stat().st_size for f in victim.rglob("*") if f.is_file())
        shutil.rmtree(victim)
        freed += size
        logger.info(f"pruned {victim.name} ({size / 2**30:.1f} GiB)")
    kept = sorted(p.name for p in build_dirs if p not in victims)
    logger.info(f"kept {kept}; serving {serving}; freed {freed / 2**30:.1f} GiB")
    return freed


def cleanup_abandoned_compactions(root: Path) -> dict:
    removed, busy = [], []
    if not Path("/proc/self/fd").is_dir():
        return {"removed": removed, "busy": busy, "bytes_freed": 0}
    cutoff = time.time() - 600
    candidates = []
    for path in root.glob(".compact-release-*"):
        if path.is_symlink() or not path.is_dir() or path.stat().st_uid != os.geteuid():
            continue
        files = [item for item in path.rglob("*") if item.is_file() and not item.is_symlink()]
        if max([path.stat().st_mtime, *(item.stat().st_mtime for item in files)]) < cutoff:
            candidates.append((path, sum(item.stat().st_size for item in files)))
    for process in Path("/proc").glob("[0-9]*"):
        try:
            opened = [str(fd.resolve()) for fd in (process / "fd").iterdir()]
            mapped = (process / "maps").read_text()
        except (OSError, PermissionError):
            continue
        for path, _ in candidates:
            prefix = str(path) + "/"
            if any(item.startswith(prefix) for item in opened) or prefix in mapped:
                busy.append(str(path))
    for path, size in candidates:
        if str(path) not in busy:
            shutil.rmtree(path)
            removed.append({"path": str(path), "bytes": size})
    return {
        "removed": removed,
        "busy": sorted(set(busy)),
        "bytes_freed": sum(item["bytes"] for item in removed),
    }


def require_build_space() -> None:
    root = Path(os.environ.get("INDEX_BUILD_ROOT", str(INDEXER_HOME / "build")))
    root.mkdir(parents=True, exist_ok=True)
    cleanup_abandoned_compactions(root)
    minimum = int(os.environ.get("INDEX_MIN_FREE_GIB", "0")) * 2**30
    if shutil.disk_usage(root).free < minimum:
        raise RuntimeError("Insufficient free space for the offline index build")


def verify_archived_generation(source: Path, destination: Path) -> None:
    manifest_bytes = (source / "manifest.json").read_bytes()
    if (destination / "manifest.json").read_bytes() != manifest_bytes:
        raise ValueError("Archive manifest differs from completed generation")
    manifest = json.loads(manifest_bytes)
    artifacts = [("index.db", manifest)]
    if prefix := manifest.get("prefix_map"):
        artifacts.append(("index.db.prefix", prefix))
    for name, expected in artifacts:
        path = destination / name
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if path.stat().st_size != expected["byte_size"] or digest != expected["sha256"]:
            raise ValueError("Archive artifact differs from published checksum: " + name)


def archive_completed_generations(root: Path, archive: Path) -> list[Path]:
    root, archive = root.resolve(), archive.resolve()
    if archive == root or archive.is_relative_to(root) or root.is_relative_to(archive):
        raise ValueError("Build and archive roots must not overlap")
    archive.mkdir(parents=True, exist_ok=True)
    archived = []
    for source in sorted(root.glob("build-*")):
        if source.is_symlink() or not (source / "manifest.json").is_file():
            continue
        destination = archive / source.name
        if not destination.exists():
            with tempfile.TemporaryDirectory(dir=archive, prefix=".archive-") as temp:
                staged = Path(temp) / source.name
                shutil.copytree(source, staged)
                verify_archived_generation(source, staged)
                for path in staged.rglob("*"):
                    if path.is_file():
                        with path.open("rb") as stream:
                            os.fsync(stream.fileno())
                descriptor = os.open(staged, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                staged.rename(destination)
        else:
            verify_archived_generation(source, destination)
        descriptor = os.open(archive, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        shutil.rmtree(source)
        archived.append(destination)
    return archived


@task(retries=2, retry_delay_seconds=30)
def archive_builds() -> list[str]:
    archive_root = os.environ.get("INDEX_ARCHIVE_ROOT")
    if not archive_root:
        return []
    root = Path(os.environ["INDEX_BUILD_ROOT"])
    archived = archive_completed_generations(root, Path(archive_root))
    cleanup = cleanup_abandoned_compactions(root)
    get_run_logger().info("Abandoned compaction staging cleanup: %s", cleanup)
    get_run_logger().info("archived %d verified generations off local SSD", len(archived))
    return [str(path) for path in archived]


@task(retries=0, timeout_seconds=1800)
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
    ref: str | None = None,
    snapshot_format: Literal["sqlite", "compact"] = "sqlite",
    reuse_source: str | None = None,
    automatic_refresh: bool = False,
    resume_generation: str | None = None,
    rollback: bool = False,
    resume_rollout: bool = False,
    snapshot_engine: Literal["native", "tantivy"] = "native",
    tantivy_operation: dict | None = None,
):
    if tantivy_operation is not None:
        if (
            ref is not None
            or reuse_source is not None
            or automatic_refresh
            or resume_generation is not None
            or rollback
            or resume_rollout
        ):
            raise ValueError(
                "Explicit slot operations cannot be combined with build/automatic/native operations"
            )
        from mps.typeahead_tantivy_slots import run_slot_operation

        revision = os.environ.get("INDEX_TANTIVY_SOURCE_REVISION", "")
        if not re.fullmatch(r"[a-f0-9]{40}", revision):
            raise ValueError("Slot operations require an immutable Typeahead source revision")
        repo = clone_repo(revision, isolated=True)
        previous_adapter = os.environ.get("INDEX_TANTIVY_ADAPTER_PATH")
        os.environ["INDEX_TANTIVY_ADAPTER_PATH"] = str(
            repo / "scripts/snapshot_refresh/tantivy_install.py"
        )
        try:
            return run_slot_operation(tantivy_operation, get_run_logger())
        finally:
            if previous_adapter is None:
                os.environ.pop("INDEX_TANTIVY_ADAPTER_PATH", None)
            else:
                os.environ["INDEX_TANTIVY_ADAPTER_PATH"] = previous_adapter
            shutil.rmtree(repo)
    if snapshot_engine not in ("native", "tantivy"):
        raise ValueError("Unsupported snapshot engine")
    if snapshot_engine == "tantivy" and (
        snapshot_format != "compact" or automatic_refresh or rollback or resume_rollout
    ):
        raise ValueError(
            "Tantivy supports isolated compact publication only; automatic installation remains fenced pending slot lifecycle qualification"
        )
    if snapshot_engine == "tantivy":
        if reuse_source is not None or resume_generation is not None:
            raise ValueError("Checkpoint publication requires one new owned cloud checkpoint")
        repo = clone_repo(ref, isolated=True)
        try:
            prepared, source = prepare_checkpoint_generation(repo)
            publish_tantivy_generation(repo, prepared, source)
            retire_tantivy_generation(prepared)
        finally:
            shutil.rmtree(repo)
        return
    if rollback:
        if resume_generation is not None or reuse_source is not None or resume_rollout:
            raise ValueError("Rollback does not build or install a generation")
        from mps.typeahead_refresh import rollback_snapshot
        from prefect.states import Completed

        report = rollback_snapshot()
        if report["freshness"]["stale"]:
            return Completed(
                name="Degraded",
                message="Rollback restored availability; the previous snapshot is stale and needs refresh",
            )
        return report
    if automatic_refresh:
        from mps.typeahead_refresh import require_automatic_refresh_supported

        require_automatic_refresh_supported(get_run_logger())
    if resume_rollout:
        if (
            snapshot_format != "compact"
            or not automatic_refresh
            or resume_generation is not None
            or reuse_source is not None
        ):
            raise ValueError("Rollout recovery requires only the automatic compact channel")
        from mps.typeahead_refresh import prune_snapshot_artifacts, refresh_snapshot

        report = refresh_snapshot()
        prune_snapshot_artifacts()
        wrapper = INDEXER_HOME / "completed-publications" / report["build_id"]
        if wrapper.exists():
            if (
                json.loads((wrapper / "manifest.json").read_text())["active_build"]
                != report["build_id"]
            ):
                raise ValueError("Completed-publication cleanup identity mismatch")
            shutil.rmtree(wrapper)
        archive_builds()
        prune_builds(os.environ.get("INDEX_ARCHIVE_ROOT"))
        return
    if resume_generation is not None:
        if snapshot_format != "compact" or not automatic_refresh:
            raise ValueError("Completed-generation recovery requires automatic compact refresh")
        from mps.typeahead_refresh import prune_snapshot_artifacts, refresh_snapshot

        repo = clone_repo(ref)
        source = Path(resume_generation).resolve()
        allowed = [
            Path(os.environ["INDEX_BUILD_ROOT"]).resolve(),
            Path(os.environ["INDEX_ARCHIVE_ROOT"]).resolve(),
        ]
        if not any(source.is_relative_to(root) for root in allowed):
            raise ValueError("Completed generation must belong to this publisher")
        manifest = json.loads((source / "manifest.json").read_bytes())
        if (
            source.name != "build-" + manifest["active_build"]
            or not 0 <= time.time() - manifest["source_watermark"] < 3 * 86400
        ):
            raise ValueError("Completed generation identity or freshness is invalid")
        if manifest["manifest_version"] == 1:
            compact = source.parent / (source.name + "-px05")
            if (compact / "manifest.json").exists():
                generation = publish_completed_generation(repo, str(compact))
            else:
                ensure_compactor(repo)
                generation = publish_compact(repo, source)
        else:
            generation = publish_completed_generation(repo, resume_generation)
        refresh_snapshot(generation)
        prune_snapshot_artifacts()
        if generation.parent == INDEXER_HOME / "completed-publications":
            shutil.rmtree(generation)
        archive_builds()
        prune_builds(os.environ.get("INDEX_ARCHIVE_ROOT"))
        return
    prune_builds(os.environ.get("INDEX_ARCHIVE_ROOT"))
    require_build_space()
    repo = clone_repo(ref)
    binary = build_binary(repo)
    if snapshot_format == "compact":
        compact_target()
        source = run_indexer(binary, local_only=True, reuse_source=reuse_source)
        if source is None:
            raise RuntimeError("local index build did not return a source generation")
        generation = publish_compact(repo, source)
        if automatic_refresh:
            if os.environ.get("INDEX_CHANNEL") != "managed":
                raise ValueError("Automatic rollout requires the managed publication channel")
            from mps.typeahead_refresh import prune_snapshot_artifacts, refresh_snapshot

            refresh_snapshot(generation)
            prune_snapshot_artifacts()
        if generation.parent == INDEXER_HOME / "completed-publications":
            shutil.rmtree(generation)
    else:
        if automatic_refresh:
            raise ValueError("Automatic rollout requires compact snapshots")
        run_indexer(binary, reuse_source=reuse_source)
    archive_builds()
    prune_builds(os.environ.get("INDEX_ARCHIVE_ROOT"))
