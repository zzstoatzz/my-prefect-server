"""
Build the typeahead prefix-index snapshot (the offline `MODE=indexer` job) on
the home box and publish it to R2.

This is the Zig snapshot builder lifted off Fly. It's a pure batch job — reads
the actor corpus from Turso, builds the prefix index on local disk, uploads it
to R2, rewrites `latest.json`, and exits. No server, no ingress. App B
(typeahead-search) promotes the new snapshot on its own. If this run is late or
fails, the last good snapshot keeps serving, so there's no SLA here.

The flow is a thin shim around the Zig binary: clone typeahead → `zig build`
(native, host toolchain installed by typeahead `deploy/home-indexer/install.sh`)
→ run the binary with the publish env. Zig + rclone must be on PATH (they are,
under ~/.local/bin, for the home-pool worker unit).

Secrets are injected as env vars from Secret blocks via the deployment's
`job_variables.env` in prefect.yaml; subprocesses just inherit the env. Flow
code never touches the Secret API.

Expected env (set by the deployment):
  - TURSO_URL                     (block: turso-url — BARE host, no scheme)
  - TURSO_AUTH_TOKEN              (block: turso-token)
  - INDEX_R2_ENDPOINT/_BUCKET/_ACCESS_KEY_ID/_SECRET_ACCESS_KEY  (R2 creds)
  - INDEX_CHANNEL                 (prod | staging | local; default local=no upload)
  - INDEX_ALLOW_PROD             (=1 to arm prod publish)
  - INDEX_BUILD_ROOT             (persistent NVMe path; NOT /tmp — build peaks ~65GB)
"""

import os
import shutil
import subprocess
from pathlib import Path

from prefect import flow, get_run_logger, task
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
INDEXER_HOME = Path(
    os.environ.get("INDEXER_HOME") or (Path.home() / ".typeahead-index")
)
REPO_DIR = INDEXER_HOME / "repo"


def _stream(cmd: list[str], cwd: Path, env: dict, timeout: int) -> None:
    """Run a subprocess, streaming stdout+stderr to the run logger live.

    The build/run are long (a full snapshot is the better part of an hour);
    capture-then-dump would leave the run silent the whole time. Stream so
    progress is visible (see the typeahead 'progress indicators' lesson).
    """
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
    """Fresh shallow clone of typeahead (tangled, github fallback)."""
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
def build_binary(repo_dir: Path) -> Path:
    """`zig build -Doptimize=ReleaseSafe`, with the github-mirror dep fallback.

    Mirrors the Dockerfile: if the primary dep fetch (tangled.org) fails, swap
    in build.zig.zon.gh and retry. ~3min cold on the box; warm zig cache faster.
    """
    services = repo_dir / "services"
    binary = services / "zig-out" / "bin" / "typeahead-ingester"
    env = {**os.environ}
    try:
        _stream(["zig", "build", "-Doptimize=ReleaseSafe"], services, env, timeout=900)
    except RuntimeError:
        get_run_logger().warning(
            "primary dep fetch failed; retrying via github mirrors"
        )
        shutil.copy(services / "build.zig.zon.gh", services / "build.zig.zon")
        _stream(["zig", "build", "-Doptimize=ReleaseSafe"], services, env, timeout=900)
    if not binary.is_file():
        raise RuntimeError(f"build reported success but binary missing at {binary}")
    return binary


@task
def run_indexer(binary: Path) -> None:
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
    _stream([str(binary)], binary.parent, env, timeout=7200)


@flow(name="typeahead-index", log_prints=True, timeout_seconds=14400)
def typeahead_index():
    repo = clone_repo()
    binary = build_binary(repo)
    run_indexer(binary)
