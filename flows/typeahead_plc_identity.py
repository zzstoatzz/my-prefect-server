"""
Resolve typeahead's DID -> (handle, pds) backlog in bulk from the PLC log.

Companion to `typeahead_index`, same shape: pure batch, no ingress, no SLA. If a
run is late or fails, identity just stays stale until the next one.

WHY IT EXISTS
-------------
typeahead's enrichment cron resolved identity one HTTP request per DID. On
2026-07-28 that left 1,311,817 actors with no handle and 9,315,512 with no pds,
draining at 500/run — centuries, not backlogs. Batching the worker's phase 1
through getProfiles (25/call) covers ~96% of the handle case, but getProfiles
only knows actors the appview indexed and never returns pds at all. The residual
is exactly the self-hosted-PDS accounts, which is the case that matters most.

The PLC log has both fields on 100% of ops, and fig's Allegedly
(https://tangled.org/@microcosm.blue/Allegedly, the tool behind plc.wtf) makes
it bulk-readable as weekly gzipped bundles instead of paginated HTTP.

This is deliberately NOT on the Cloudflare cron: it pulls ~28GB of bundles and
holds a multi-GB dict, neither of which a Worker can do.

HOST PREREQS
------------
  - `allegedly` on PATH (`cargo install allegedly`), for scraping the weeks the
    public bundle host has not published yet. If it is absent this flow still
    runs — it falls back to published bundles only, and logs what it skipped.
  - a persistent path with ~30GB free (PLC_BUNDLE_DIR). Process flow runs
    execute in an ephemeral /tmp, so this MUST be set to real disk.

Expected env (set by the deployment):
  - TURSO_URL         (block: typeahead-turso-url — full libsql:// URL, NOT stripped;
                       the script rewrites the scheme itself, unlike the indexer)
  - TURSO_AUTH_TOKEN  (block: typeahead-turso-token)
  - PLC_BUNDLE_DIR    (persistent NVMe path for the bundle cache)
  - PLC_APPLY         (=1 to write; anything else is a dry run)
"""

import os
import shutil
import subprocess
from pathlib import Path

from prefect import flow, get_run_logger, task
from prefect.tasks import exponential_backoff

REPO_URL = "https://tangled.org/zzstoatzz.io/typeahead.git"
REPO_URL_FALLBACK = "https://github.com/zzstoatzz/typeahead.git"

WORK_HOME = Path(os.environ.get("PLC_BUNDLE_DIR") or (Path.home() / ".typeahead-plc"))
REPO_DIR = WORK_HOME / "repo"
BUNDLE_DIR = WORK_HOME / "weekly"


def _stream(cmd: list[str], cwd: Path, env: dict, timeout: int) -> None:
    """Run a subprocess, streaming output to the run logger live.

    These stages are long (a cold bundle fetch is tens of GB). Capture-then-dump
    would leave the run silent throughout — stream so progress is visible.
    """
    logger = get_run_logger()
    proc = subprocess.Popen(
        cmd, cwd=str(cwd), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
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
    """Fresh shallow clone of typeahead (tangled, github fallback)."""
    logger = get_run_logger()
    if REPO_DIR.exists():
        shutil.rmtree(REPO_DIR)
    REPO_DIR.parent.mkdir(parents=True, exist_ok=True)
    for url in (REPO_URL, REPO_URL_FALLBACK):
        r = subprocess.run(
            ["git", "clone", "--depth", "1", url, str(REPO_DIR)],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            logger.info(f"cloned {url}")
            return REPO_DIR
        logger.warning(f"clone failed for {url}: {r.stderr.strip()}")
    raise RuntimeError("clone failed from both tangled and github")


@task(retries=1, retry_delay_seconds=60)
def fetch_published_bundles(repo_dir: Path) -> None:
    """Seed the bundle cache from https://plc.t3.storage.dev/plc.directory/.

    Resumable by construction — the script skips weeks already on disk and
    writes via .part + rename, so a killed run never leaves a torn bundle
    looking complete. Measured 2026-07-28: 149 weeks, 27.96GB gz, complete from
    2022-11-17 through 2025-09-18.
    """
    script = repo_dir / "scripts" / "plc-identity-sync.py"
    _stream(
        ["uv", "run", str(script), "--dest", str(BUNDLE_DIR), "--fetch", "--from-dir"],
        repo_dir, {**os.environ}, timeout=4 * 3600,
    )


@task(retries=1, retry_delay_seconds=60)
def scrape_tail() -> bool:
    """`allegedly bundle` the weeks the public host has not published.

    Returns False (and does NOT fail the run) when allegedly is absent: the
    published bundles still carry years of history, and a partial identity fix
    beats no run at all. The skip is logged rather than silent — a missing tail
    means recently-created accounts stay unresolved, which is precisely the
    population this job exists for, so it must be visible.
    """
    logger = get_run_logger()
    if shutil.which("allegedly") is None:
        logger.warning(
            "allegedly not on PATH — skipping the unpublished tail. Published "
            "bundles end 2025-09-18, so accounts created since then will NOT be "
            "resolved by this run. Install with `cargo install allegedly`."
        )
        return False
    _stream(
        ["allegedly", "bundle", "--dest", str(BUNDLE_DIR)],
        WORK_HOME, {**os.environ}, timeout=6 * 3600,
    )
    return True


@task
def apply_identities(repo_dir: Path, tail_ok: bool) -> None:
    """Walk the bundle cache and write resolved handle/pds back to Turso.

    Reads bundles directly rather than piping `allegedly backfill`, so the job
    does not require a Rust toolchain to do its main work. Writes are batched
    into transactions — Turso is single-writer and this shares it with the
    ingester.
    """
    logger = get_run_logger()
    script = repo_dir / "scripts" / "plc-identity-sync.py"
    cmd = ["uv", "run", str(script), "--dest", str(BUNDLE_DIR), "--from-dir"]
    if os.environ.get("PLC_APPLY") == "1":
        cmd.append("--apply")
    else:
        logger.warning("PLC_APPLY != 1 — dry run, no writes")
    if not tail_ok:
        logger.warning("running against published bundles only (no scraped tail)")
    _stream(cmd, repo_dir, {**os.environ}, timeout=6 * 3600)


@flow(name="typeahead-plc-identity")
def typeahead_plc_identity() -> None:
    if not os.environ.get("TURSO_URL"):
        raise RuntimeError("TURSO_URL not set — check the deployment's job_variables.env")
    WORK_HOME.mkdir(parents=True, exist_ok=True)
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    repo = clone_repo()
    fetch_published_bundles(repo)
    tail_ok = scrape_tail()
    apply_identities(repo, tail_ok)


if __name__ == "__main__":
    typeahead_plc_identity()
