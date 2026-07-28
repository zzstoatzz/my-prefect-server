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
  - a persistent path with ~30GB free (PLC_BUNDLE_DIR). Process flow runs execute
    in an ephemeral /tmp, so this MUST be set to real disk.
  - `uv` and `git` on the worker PATH (they already are, via ~/.local/bin).
  - `allegedly` is OPTIONAL. Installed on heavypad 2026-07-28 at
    ~/.local/bin/allegedly. It needs rust >=1.85 (apt ships 1.75, so rustup) and
    vendored openssl to avoid needing libssl-dev + root — see
    typeahead deploy/home-indexer/install.sh. When absent the flow falls back to
    the script's pure-python tail scraper, which emits byte-compatible bundles.

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
from prefect.cache_policies import NONE
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


# Every task here is side-effecting — it clones, downloads, or writes to Turso —
# and none returns a value worth reusing. cache_policy=NONE matches the
# convention in curate.py / pds_records.py and makes that explicit rather than
# inheriting INPUTS + TASK_SOURCE + RUN_ID. It also means nothing persists a
# result, which matters because this deployment's `env:` block replaces the
# *home anchor rather than merging with it, so PREFECT_LOCAL_STORAGE_PATH would
# otherwise be unset and results would land in the run's ephemeral /tmp.
#
# NOTE these stages are deliberately NOT wrapped in `with transaction()`.
# Verified against prefect 3.7.3: task transactions nest as children of an
# enclosing transaction, commit LAZY, and Transaction.reset() propagates a
# child's ROLLED_BACK state to the parent — which then rolls back every
# sibling. So one `with transaction()` around these three stages would mean a
# failure in apply_identities fires on_rollback for the bundle download, i.e.
# discards up to 28GB over a Turso hiccup. Independent commits are correct
# here: every side effect is idempotent and resumable (bundles skip weeks
# already on disk, identity UPDATEs are COALESCE-guarded), so partial progress
# is worth keeping.
#
# Also verified: cache_policy=NONE does NOT suppress rollback hooks. It only
# removes the transaction key, so nothing is staged or read back.
@task(
    cache_policy=NONE,
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


@task(cache_policy=NONE, retries=1, retry_delay_seconds=60)
def fetch_published_bundles(repo_dir: Path) -> None:
    """Seed the bundle cache from https://plc.t3.storage.dev/plc.directory/.

    Resumable by construction — the script skips weeks already on disk and writes
    via .part + rename, so a killed run never leaves a torn bundle looking
    complete. Measured 2026-07-28: 149 weeks, 27.96GB gz, complete from
    2022-11-17 through 2025-09-18.

    Bundles ONLY. An earlier version passed --from-dir here, which also ran the
    whole identity phase — a full keyset scan and bundle parse, discarded,
    before apply_identities did it all again.
    """
    script = repo_dir / "scripts" / "plc-identity-sync.py"
    _stream(
        ["uv", "run", str(script), "--dest", str(BUNDLE_DIR), "--fetch", "--bundles-only"],
        repo_dir, {**os.environ}, timeout=4 * 3600,
    )


@task(cache_policy=NONE, retries=1, retry_delay_seconds=60)
def scrape_tail(repo_dir: Path) -> str:
    """Bundle the weeks the public host has not published.

    Prefers `allegedly bundle` (fig's tool, and the reference implementation).
    Falls back to the script's pure-python --scrape-tail, which emits
    byte-compatible bundles — allegedly needs a rust toolchain and vendored
    openssl, and the unpublished tail is where every recently-created account
    lives, so this must not depend on host setup succeeding.
    """
    logger = get_run_logger()
    script = repo_dir / "scripts" / "plc-identity-sync.py"
    if shutil.which("allegedly"):
        _stream(["allegedly", "bundle", "--dest", str(BUNDLE_DIR)],
                WORK_HOME, {**os.environ}, timeout=6 * 3600)
        return "allegedly"
    logger.warning("allegedly not on PATH — using the python tail scraper")
    _stream(["uv", "run", str(script), "--dest", str(BUNDLE_DIR), "--scrape-tail",
             "--bundles-only"],
            repo_dir, {**os.environ}, timeout=6 * 3600)
    return "python"


@task(cache_policy=NONE)
def apply_identities(repo_dir: Path, tail_via: str) -> None:
    """Walk the bundle cache and write resolved handle/pds back to Turso.

    Reads bundles directly rather than piping `allegedly backfill`, so the main
    work needs no rust toolchain. Writes are paced — Turso is single-writer and
    this shares it with the live ingester.
    """
    logger = get_run_logger()
    logger.info(f"tail bundled via: {tail_via}")
    script = repo_dir / "scripts" / "plc-identity-sync.py"
    cmd = ["uv", "run", str(script), "--dest", str(BUNDLE_DIR), "--from-dir"]
    if os.environ.get("PLC_APPLY") == "1":
        cmd.append("--apply")
    else:
        logger.warning("PLC_APPLY != 1 — dry run, no writes")
    _stream(cmd, repo_dir, {**os.environ}, timeout=6 * 3600)


@flow(name="typeahead-plc-identity")
def typeahead_plc_identity() -> None:
    if not os.environ.get("TURSO_URL"):
        raise RuntimeError("TURSO_URL not set — check the deployment's job_variables.env")
    WORK_HOME.mkdir(parents=True, exist_ok=True)
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    repo = clone_repo()
    fetch_published_bundles(repo)
    tail_via = scrape_tail(repo)
    apply_identities(repo, tail_via)


if __name__ == "__main__":
    typeahead_plc_identity()
