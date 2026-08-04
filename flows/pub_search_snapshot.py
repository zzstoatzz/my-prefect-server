"""
Build the pub-search replica snapshot (the `BUILDER_MODE=1` job) on the home
box and publish it to R2.

This is pub-search's Zig snapshot builder lifted off Fly, the same move
typeahead made in June (see flows/typeahead_index.py — this file is modeled on
it 1:1). Pure batch: reads the document corpus from Turso, builds the FTS
replica on local disk, runs its gates (doc-count vs Turso, FTS sentinel,
quick_check), uploads via rclone to R2, rewrites the channel pointer LAST, and
exits. The serving backend's promote watcher adopts it on its own. A late or
failed run just leaves the prior snapshot serving — no SLA here.

Expected env (set by the deployment):
  - TURSO_URL / TURSO_TOKEN       (shared blocks: turso-url / turso-token —
                                   already the leaf db; full libsql:// URL is
                                   what pub-search's client wants, no stripping)
  - INDEX_R2_ENDPOINT/_BUCKET/_ACCESS_KEY_ID/_SECRET_ACCESS_KEY
                                  (blocks: leaflet-index-r2-* — bucket
                                   leaflet-search-index, NOT typeahead's)
  - BUILDER_CHANNEL               (staging | prod; prod also needs BUILDER_ALLOW_PROD=1)
  - BUILDER_HOME                  (persistent path for repo + build scratch)
  - RCLONE_BWLIMIT                (cap the ~600MB upload; rclone inherits it)
"""

import os
import shutil
import subprocess
from pathlib import Path

from prefect import flow, get_run_logger, task
from prefect.tasks import exponential_backoff

REPO_URL = "https://tangled.org/zzstoatzz.io/pub-search.git"
REPO_URL_FALLBACK = "https://github.com/zzstoatzz/pub-search.git"

BUILDER_HOME = Path(os.environ.get("BUILDER_HOME") or (Path.home() / ".pub-search-snapshot"))
REPO_DIR = BUILDER_HOME / "repo"


def _stream(cmd: list[str], cwd: Path, env: dict, timeout: int) -> None:
    """Run a subprocess, streaming output to the run logger live (long builds
    should show progress, not go silent — the typeahead lesson)."""
    logger = get_run_logger()
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            logger.info(line.rstrip())
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise RuntimeError(f"{cmd[0]} exceeded {timeout}s timeout")
    if code != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])} ... exited {code}")


@task(
    retries=2,
    retry_delay_seconds=exponential_backoff(backoff_factor=10),
    retry_jitter_factor=1,
)
def clone_repo() -> Path:
    """Fresh shallow clone of pub-search (tangled, github fallback). The
    banned/kept DID lists are baked into the binary at build time, so building
    from a fresh clone is what keeps policy current."""
    logger = get_run_logger()
    if REPO_DIR.exists():
        shutil.rmtree(REPO_DIR)
    REPO_DIR.parent.mkdir(parents=True, exist_ok=True)
    for url in (REPO_URL, REPO_URL_FALLBACK):
        r = subprocess.run(
            ["git", "clone", "--depth", "1", url, str(REPO_DIR)],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            logger.info(f"cloned {url}")
            return REPO_DIR
        logger.warning(f"clone failed for {url}: {r.stderr.strip()}")
    raise RuntimeError("clone failed from both tangled and github")


@task(retries=1, retry_delay_seconds=30)
def build_binary(repo_dir: Path) -> tuple[Path, str]:
    """`zig build -Doptimize=ReleaseSafe` in backend/ (host toolchain — same
    zig 0.16 install typeahead's home-indexer install.sh provides)."""
    backend = repo_dir / "backend"
    binary = backend / "zig-out" / "bin" / "pub-search"
    _stream(["zig", "build", "-Doptimize=ReleaseSafe"], backend, {**os.environ}, timeout=1200)
    if not binary.is_file():
        raise RuntimeError(f"build reported success but binary missing at {binary}")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo_dir), capture_output=True, text=True
    ).stdout.strip()
    return binary, sha


@task
def run_builder(binary: Path, version: str) -> None:
    """BUILDER_MODE=1: turso → gated build → rclone to R2 → pointer last → exit."""
    logger = get_run_logger()
    channel = os.environ.get("BUILDER_CHANNEL", "staging")
    logger.info(f"BUILDER_MODE=1 channel={channel} version={version}")
    rclone = shutil.which("rclone")
    if not rclone:
        raise RuntimeError("rclone not found on PATH — run typeahead deploy/home-indexer/install.sh")
    env = {
        **os.environ,
        "BUILDER_MODE": "1",
        "BUILDER_VERSION": version,
        "RCLONE": rclone,
        # healthy builds run 15-40min at a ~60k-doc corpus, but export/VACUUM/
        # hash/upload all scale ~linearly and we're planning for 5x (see
        # pub-search docs/scale-300k-plan.md), so leave real headroom while
        # still bounding a hung build before it eats the next 2h cycle
        # (2026-08-01: a wedged build held the deployment concurrency slot for
        # hours and stalled all snapshots). 100min subprocess bound, 110min
        # flow bound — the builder logs per-phase timings; tighten these back
        # once real 300k-corpus numbers exist.
        "BUILDER_TIMEOUT_SECS": "6000",
    }
    _stream([str(binary)], binary.parent, env, timeout=6000)


@flow(name="pub-search-snapshot", log_prints=True, timeout_seconds=6600)
def pub_search_snapshot():
    repo = clone_repo()
    binary, version = build_binary(repo)
    run_builder(binary, version)
