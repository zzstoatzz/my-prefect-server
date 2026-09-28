"""Materialize pinned flow source without downloading it again for every run."""

import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tarfile
import tempfile
import time
from contextlib import contextmanager, suppress
from pathlib import Path


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@contextmanager
def _locked(path: Path, timeout: float):
    with path.open("a") as lock:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Timed out waiting for source cache") from None
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _valid(entry: Path, commit: str) -> bool:
    try:
        metadata = json.loads((entry / "metadata.json").read_text())
        archive = entry / "source.tar"
        return metadata == {
            "commit": commit,
            "size": archive.stat().st_size,
            "sha256": _digest(archive),
        }
    except (OSError, ValueError):
        return False


def _fetch(entry: Path, commit: str, repositories: list[str], timeout: float) -> None:
    with tempfile.TemporaryDirectory(prefix="staging-", dir=entry.parent) as temporary:
        staging = Path(temporary)
        repo = staging / "git"
        repo.mkdir()

        def git(*args: str) -> bytes:
            with subprocess.Popen(
                ["git", "-C", str(repo), *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            ) as process:
                try:
                    stdout, _ = process.communicate(timeout=timeout)
                except BaseException:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()
                    raise
                if process.returncode:
                    raise RuntimeError(f"Source cache git {args[0]} failed ({process.returncode})")
                return stdout

        git("init", "--bare", "--quiet")
        for index, repository in enumerate(repositories):
            try:
                git("fetch", "--quiet", "--depth=1", "--no-tags", "--", repository, commit)
                break
            except (RuntimeError, subprocess.TimeoutExpired):
                if index == len(repositories) - 1:
                    raise
        if git("rev-parse", "FETCH_HEAD^{commit}").decode().strip() != commit:
            raise ValueError("Fetched source does not match MPS_PIN")
        ready = staging / "ready"
        ready.mkdir()
        archive = ready / "source.tar"
        git("archive", "--format=tar", f"--output={archive}", commit)
        (ready / "metadata.json").write_text(
            json.dumps(
                {"commit": commit, "size": archive.stat().st_size, "sha256": _digest(archive)}
            )
        )
        ready.replace(entry)


def _prune(cache: Path, keep: int, max_bytes: int) -> int:
    entries = sorted(
        (p for p in cache.iterdir() if p.is_dir() and re.fullmatch(r"[0-9a-f]{40}", p.name)),
        key=lambda p: p.stat().st_mtime_ns,
        reverse=True,
    )
    retained = 0
    total = 0
    removed = 0
    for entry in entries:
        size = sum(p.stat().st_size for p in entry.iterdir() if p.is_file())
        if retained < keep and total + size <= max_bytes:
            retained += 1
            total += size
        else:
            shutil.rmtree(entry)
            removed += 1
    return removed


def cached_checkout(
    *,
    repository: str,
    cache_root: str = "~/.cache/mps/source",
    directory: str = "my-prefect-server",
    fallback_repository: str | None = None,
    keep: int = 8,
    max_bytes: int = 512 * 1024 * 1024,
    timeout: float = 120,
) -> dict[str, str | bool | int | float]:
    started = time.monotonic()
    pin = os.environ.get("MPS_PIN", "")
    if not re.fullmatch(r"@[0-9a-f]{40}", pin):
        raise ValueError("MPS_PIN must be @ followed by a full commit SHA")
    if keep < 1 or max_bytes < 1 or timeout <= 0:
        raise ValueError("Cache limits and timeout must be positive")
    commit = pin[1:]
    workspace = Path.cwd().resolve()
    destination = (workspace / directory).resolve()
    if destination.parent != workspace or destination.exists():
        raise ValueError("Source destination must be a new direct child of the run workspace")
    cache = (
        Path(cache_root).expanduser().resolve() / hashlib.sha256(repository.encode()).hexdigest()
    )
    cache.mkdir(parents=True, exist_ok=True)
    entry = cache / commit
    with _locked(cache / "lock", timeout * 3):
        for abandoned in cache.glob("staging-*"):
            if abandoned.is_dir():
                shutil.rmtree(abandoned)
        hit = _valid(entry, commit)
        if not hit:
            if entry.exists():
                shutil.rmtree(entry)
            repositories = [repository] + ([fallback_repository] if fallback_repository else [])
            _fetch(entry, commit, repositories, timeout)
        archive = entry / "source.tar"
        archive_bytes = archive.stat().st_size
        with tempfile.TemporaryDirectory(prefix=".source-", dir=workspace) as temporary:
            materialized = Path(temporary) / "source"
            materialized.mkdir()
            with tarfile.open(archive) as source:
                source.extractall(materialized, filter="data")
            materialized.replace(destination)
        os.utime(entry)
        pruned = _prune(cache, keep, max_bytes)
    result = {
        "directory": str(destination),
        "commit": commit,
        "cache_hit": hit,
        "archive_bytes": archive_bytes,
        "pruned": pruned,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    print("mps.source_cache " + json.dumps(result), flush=True)
    return result
