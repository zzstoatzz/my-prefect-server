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
import signal
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path

from prefect import flow, get_run_logger, task
from prefect.tasks import exponential_backoff

REPO_URL = "https://tangled.org/zzstoatzz.io/typeahead.git"
REPO_URL_FALLBACK = "https://github.com/zzstoatzz/typeahead.git"

WEEK = 604800

WORK_HOME = Path(os.environ.get("PLC_BUNDLE_DIR") or (Path.home() / ".typeahead-plc"))
REPO_DIR = WORK_HOME / "repo"
BUNDLE_DIR = WORK_HOME / "weekly"


def _heartbeat(stop: threading.Event, label: str, every: int = 300) -> None:
    """Log bundle-cache size while a silent stage runs.

    `allegedly bundle` writes a week's file only when that week completes and
    prints nothing in between, so a multi-hour tail scrape produces ZERO log
    lines. On 2026-07-28 that made a healthy run look orphaned — 2h17m with no
    output — and it took a manual check of /proc/<pid>/stat to prove otherwise.
    Streaming stdout is not enough when the subprocess has nothing to say.
    """
    logger = get_run_logger()
    while not stop.wait(every):
        try:
            files = list(BUNDLE_DIR.glob("*.jsonl.gz"))
            mb = sum(f.stat().st_size for f in files) / 1e6
            partial = sum(f.stat().st_size for f in BUNDLE_DIR.glob("*.part")) / 1e6
            logger.info(
                f"[{label}] alive — {len(files)} bundles, {mb/1000:.1f} GB"
                + (f" (+{partial:.0f} MB in flight)" if partial else "")
            )
        except Exception as e:  # never let the heartbeat kill the stage
            logger.warning(f"[{label}] heartbeat failed: {e}")


def _stream(cmd: list[str], cwd: Path, env: dict, timeout: int, label: str = "") -> None:
    """Run a subprocess, streaming output to the run logger live.

    These stages are long (a cold bundle fetch is tens of GB). Capture-then-dump
    would leave the run silent throughout — stream so progress is visible. A
    heartbeat covers the stages that stream nothing at all.
    """
    logger = get_run_logger()
    stop = threading.Event()
    hb = threading.Thread(target=_heartbeat, args=(stop, label or cmd[0]), daemon=True)
    hb.start()
    # start_new_session puts the child in its own process group so we can kill
    # the WHOLE tree. Without it, a Prefect cancel SIGTERMs only this flow
    # process: the child (allegedly, or uv->python) survives as an orphan and
    # keeps working — observed repeatedly, including an `allegedly bundle` that
    # ran for hours after its run was cancelled, and a scraper still holding the
    # bundle dir. It also made the flow die uncleanly, which the worker reports
    # as Crashed rather than Cancelled, so every cancel paged Discord.
    proc = subprocess.Popen(
        cmd, cwd=str(cwd), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        start_new_session=True,
    )

    def _kill_tree(sig=signal.SIGTERM):
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            logger.info(line.rstrip())
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(signal.SIGKILL)
        raise RuntimeError(f"{cmd[0]} exceeded {timeout}s timeout")
    except BaseException:
        # covers cancellation (SIGTERM -> KeyboardInterrupt/SystemExit) as well
        # as ordinary errors: the child must never outlive the run.
        logger.warning(f"terminating child process group for {cmd[0]}")
        _kill_tree()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            _kill_tree(signal.SIGKILL)
        raise
    finally:
        stop.set()
    if code != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])} ... exited {code}")


# Deliberately NOT wrapped in `with transaction()`. Task transactions nest as
# children and a rolled-back child rolls back its siblings, so one shared
# transaction would let a failure in apply_identities discard the 28GB bundle
# download. Every side effect here is idempotent and resumable, so partial
# progress is worth keeping.
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
        repo_dir, {**os.environ}, timeout=4 * 3600, label="fetch bundles",
    )


@task(retries=1, retry_delay_seconds=60)
def scrape_tail(repo_dir: Path) -> str:
    """Bundle the weeks the public host has not published, via the python scraper.

    NOT `allegedly bundle`, deliberately. allegedly sets no read timeout and hung
    indefinitely on the same call twice — dc4cce22 for 3h07m, a8af8f5e for 7.5h,
    both at 0.15% CPU with zero bytes written. Our subprocess timeout was 6h,
    far too coarse to catch it, so a run sat dead for most of a day after its
    useful work had already completed.

    The python path sets an explicit per-request timeout with retries and writes
    .part + rename, so a kill cannot leave a file that looks complete (allegedly
    streams gzip straight into the final name, which is how we ended up deleting
    truncated bundles by hand). Output is byte-compatible, so switching back is a
    one-line change if allegedly grows a timeout.

    Runs LAST: everything above it has already delivered this run's value, and
    this stage only improves the next one.
    """
    logger = get_run_logger()
    script = repo_dir / "scripts" / "plc-identity-sync.py"
    weeks = sorted(
        int(p.stem.split(".")[0])
        for p in BUNDLE_DIR.glob("*.jsonl.gz")
        if p.stem.split(".")[0].isdigit()
    )
    logger.info(f"tail scrape starting — {len(weeks)} weeks on disk")
    _stream(
        ["uv", "run", str(script), "--dest", str(BUNDLE_DIR), "--scrape-tail",
         "--verify-bundles", "--bundles-only"],
        repo_dir, {**os.environ}, timeout=4 * 3600, label="tail scrape",
    )
    return "python"


@task
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


@task
def reconcile_handles(repo_dir: Path) -> None:
    """Repair handles we hold that are WRONG, as opposed to missing.

    Every other identity path selects on absence, so a rename we miss is
    permanent — the actor stays searchable only under a name they no longer use.
    Measured 2026-07-30: ~1% of stored handles, roughly 100k actors.

    Detection is free here because the bundles are already on disk from the
    stages above. Only the candidates cost an API call, and only the appview's
    answer is written — PLC's alsoKnownAs is a claim, not a verified handle.
    """
    logger = get_run_logger()
    script = repo_dir / "scripts" / "plc-identity-sync.py"
    cmd = ["uv", "run", str(script), "--dest", str(BUNDLE_DIR), "--from-dir",
           "--reconcile-handles"]
    # Gated SEPARATELY from PLC_APPLY, and off by default.
    #
    # The other stages fill in blanks: worst case they write a handle where
    # there was none. This one OVERWRITES handles that are already populated,
    # across an estimated ~100k rows, and had never run against real data. A
    # dry run prints the candidate count and a sample of before/after pairs;
    # flip PLC_RECONCILE_APPLY=1 once that number looks like the ~1% the
    # sampling predicted, and not like "most of the corpus".
    if os.environ.get("PLC_RECONCILE_APPLY") == "1":
        cmd.append("--apply")
    else:
        logger.warning("PLC_RECONCILE_APPLY != 1 — dry run, reporting candidates only")
    _stream(cmd, repo_dir, {**os.environ}, timeout=6 * 3600, label="reconcile handles")


@flow(name="typeahead-plc-identity")
def typeahead_plc_identity() -> None:
    if not os.environ.get("TURSO_URL"):
        raise RuntimeError("TURSO_URL not set — check the deployment's job_variables.env")
    WORK_HOME.mkdir(parents=True, exist_ok=True)
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    repo = clone_repo()
    fetch_published_bundles(repo)
    # Identity BEFORE the tail scrape, deliberately.
    #
    # The published bundles already cover 2022-11-17 -> 2025-09-18, which is
    # where almost every unresolved DID was last touched. The tail is ~43 weeks
    # scraped from plc.directory, which self-rate-limits to 500 req/5min (600ms
    # between pages) — ~3.5 hours during which the run delivers nothing.
    #
    # Bundles are additive and this pass is idempotent, so resolving against
    # whatever is on disk gets the value now, and the tail scraped here improves
    # the NEXT run. Ordering it the other way made a 3.5h prerequisite out of
    # the least valuable stage.
    apply_identities(repo, "published bundles only")
    # after the fill pass: it walks the same bundles, and a handle just filled
    # in is not one this needs to second-guess
    reconcile_handles(repo)
    scrape_tail(repo)


if __name__ == "__main__":
    typeahead_plc_identity()
