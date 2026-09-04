"""
Rebuild the atlas (2D semantic map) and deploy to Cloudflare Pages.

Clones pub-search, runs the build-atlas script (UMAP + HDBSCAN),
then deploys the site to Cloudflare Pages via wrangler.

Secrets are injected into the pod environment via the deployment's
``job_variables.env`` in prefect.yaml; the values are sourced from Prefect
Secret blocks and resolved at ``prefect deploy`` time. Flow code never
touches the Secret API directly — subprocesses just inherit the env.

Expected env vars (set by the deployment):
  - TURBOPUFFER_API_KEY  (block: tpuf-token)
  - CLOUDFLARE_API_TOKEN (block: cloudflare-api-token)
  - ANTHROPIC_API_KEY    (block: anthropic-api-key)
  - TURSO_URL            (block: turso-url, optional)
  - TURSO_TOKEN          (block: turso-token, optional)
"""

import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from prefect import flow, get_run_logger, task
from prefect.tasks import exponential_backoff

REPO_URL = "https://github.com/zzstoatzz/pub-search.git"
CF_ACCOUNT_ID = "3e9ba01cd687b3c4d29033908177072e"
CF_PROJECT = "leaflet-search"

# node distribution — nodejs.org/dist. used to drive wrangler. tried bun
# initially (single binary, no curl needed) but bun ≥1.2 on linux-x64
# silently mangles argv when invoking node-shim scripts like wrangler's,
# producing 0-exit no-op deploys. node has no such problem.
NODE_VERSION = "22.11.0"
_NODE_ARCH_MAP = {
    ("Linux", "x86_64"): "linux-x64",
    ("Linux", "aarch64"): "linux-arm64",
    ("Darwin", "x86_64"): "darwin-x64",
    ("Darwin", "arm64"): "darwin-arm64",
}


def _install_node(node_install: Path) -> Path:
    """Install node by downloading the official tarball.

    Doesn't depend on curl/wget — the prefect worker image (debian-slim)
    ships neither. Uses Python stdlib only.
    """
    key = (platform.system(), platform.machine())
    arch = _NODE_ARCH_MAP.get(key)
    if arch is None:
        raise RuntimeError(f"unsupported platform for node: {key}")

    name = f"node-v{NODE_VERSION}-{arch}"
    url = f"https://nodejs.org/dist/v{NODE_VERSION}/{name}.tar.xz"
    archive_path = node_install / f"{name}.tar.xz"
    node_install.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as r, archive_path.open("wb") as dst:
        while chunk := r.read(1024 * 1024):
            dst.write(chunk)

    # tarfile + lzma both in stdlib; extract straight to node_install
    with tarfile.open(archive_path, "r:xz") as tar:
        tar.extractall(node_install)
    archive_path.unlink()

    node_bin = node_install / name / "bin" / "node"
    if not node_bin.is_file():
        raise RuntimeError(f"node extracted but binary missing at {node_bin}")
    return node_bin


@task(
    retries=2,
    retry_delay_seconds=exponential_backoff(backoff_factor=10),
    retry_jitter_factor=1,
)
def clone_repo(dest: Path) -> Path:
    """Shallow-clone pub-search to get site files + build script."""
    # idempotent across retries: git clone refuses a non-empty destination
    if dest.exists():
        shutil.rmtree(dest)
    subprocess.run(
        ["git", "clone", "--depth", "1", REPO_URL, str(dest)],
        check=True,
        capture_output=True,
    )
    return dest


@task(
    retries=2,
    retry_delay_seconds=exponential_backoff(backoff_factor=30),
    retry_jitter_factor=1,
)
def build_atlas(repo_dir: Path) -> Path:
    """Run the build-atlas script. Returns path to atlas.json.

    The script reads TURBOPUFFER_API_KEY, ANTHROPIC_API_KEY, and (optionally)
    TURSO_URL / TURSO_TOKEN from the inherited environment.
    """
    logger = get_run_logger()
    output = repo_dir / "site" / "atlas.json.gz"

    result = subprocess.run(
        [
            "uv",
            "run",
            "--script",
            str(repo_dir / "scripts" / "build-atlas"),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
        # headroom: this one budget absorbs the heavy uv dep install
        # (numpy/scipy/llvmlite/numba), the turbopuffer export, and the
        # UMAP/HDBSCAN compute. 300s was too tight when turbopuffer is slow.
        timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"build-atlas failed:\n{result.stderr}")

    for line in result.stdout.strip().splitlines():
        logger.info(line)

    logger.info(f"atlas.json.gz: {output.stat().st_size / 1024:.0f} KB")
    return output


@task(retries=1, retry_delay_seconds=30)
def build_facts(repo_dir: Path) -> Path:
    """Regenerate site/facts.json (corpus factoids for the wrapped page).

    Runs pub-search's scripts/build-facts, which reads TURSO_URL /
    TURSO_TOKEN from the inherited environment and writes display-ready
    factoids next to the site files, so the subsequent Pages deploy ships
    fresh facts. Paced queries — well under the atlas build's cost.
    """
    logger = get_run_logger()
    output = repo_dir / "site" / "facts.json"
    result = subprocess.run(
        ["uv", "run", "--script", str(repo_dir / "scripts" / "build-facts")],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"build-facts failed:\n{result.stderr}")
    for line in result.stdout.strip().splitlines():
        logger.info(line)
    logger.info(f"facts.json: {output.stat().st_size / 1024:.1f} KB")
    return output


@task(
    retries=2,
    retry_delay_seconds=exponential_backoff(backoff_factor=15),
    retry_jitter_factor=1,
)
def deploy_to_pages(site_dir: Path) -> str:
    """Deploy site/ to Cloudflare Pages via wrangler.

    Uses wrangler because the site has Pages Functions (functions/ dir)
    that must be compiled into a _worker.bundle. The raw Direct Upload API
    doesn't handle function bundling, and deploying without it causes 500s.

    Wrangler is run under node — apt's node is too old (v20 on bookworm,
    wrangler needs ≥v22) and bun ≥1.2 on linux-x64 silently drops argv
    when invoking wrangler's node-shim entry, producing 0-exit no-op
    deploys. `_install_node` fetches the official tarball via stdlib.

    Reads CLOUDFLARE_API_TOKEN from the inherited environment.
    """
    logger = get_run_logger()
    node_install = Path("/tmp/node")
    arch = _NODE_ARCH_MAP[(platform.system(), platform.machine())]
    node_bin_dir = node_install / f"node-v{NODE_VERSION}-{arch}" / "bin"
    node_bin = node_bin_dir / "node"
    npm_bin = node_bin_dir / "npm"
    env = {
        **os.environ,
        "CLOUDFLARE_ACCOUNT_ID": CF_ACCOUNT_ID,
        # put node's bin dir first so wrangler's `#!/usr/bin/env node`
        # shebang resolves correctly when invoked from node_modules/.bin.
        "PATH": f"{node_bin_dir}:{os.environ.get('PATH', '')}",
    }

    if not node_bin.is_file():
        logger.info(f"installing node v{NODE_VERSION} -> {node_bin}")
        _install_node(node_install)

    # install site dependencies (workers-og + wrangler from package.json)
    subprocess.run(
        [str(npm_bin), "install", "--no-audit", "--no-fund"],
        cwd=str(site_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
        check=True,
    )

    wrangler_bin = site_dir / "node_modules" / ".bin" / "wrangler"
    result = subprocess.run(
        [
            str(node_bin),
            str(wrangler_bin),
            "pages",
            "deploy",
            ".",
            f"--project-name={CF_PROJECT}",
            "--branch=main",
            "--commit-dirty=true",
        ],
        cwd=str(site_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    for line in result.stdout.strip().splitlines():
        logger.info(line)
    # always surface stderr — wrangler exits 0 on some failure modes and
    # writes the actual progress/error narrative to stderr regardless.
    if result.stderr.strip():
        for line in result.stderr.strip().splitlines():
            logger.info(f"[stderr] {line}")
    if result.returncode != 0:
        raise RuntimeError(f"wrangler deploy failed (exit {result.returncode})")

    # detect silent-no-op: wrangler must report a successful upload+deploy.
    # without this, a wrangler version banner with no further activity gets
    # reported as Completed even though nothing reached cloudflare.
    combined = result.stdout + "\n" + result.stderr
    if "Deployment complete" not in combined:
        raise RuntimeError(
            "wrangler exited 0 but produced no 'Deployment complete' marker — "
            "nothing reached cloudflare. see stderr above."
        )

    # extract deployment URL from wrangler output
    for line in reversed(result.stdout.strip().splitlines()):
        if "https://" in line:
            url = line.split("https://", 1)[1].split()[0]
            return f"https://{url}"
    return ""


@flow(name="leaflet-atlas", log_prints=True, timeout_seconds=7200)
def rebuild_atlas():
    """Rebuild the 2D semantic map and deploy to Cloudflare Pages."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = clone_repo(Path(tmpdir) / "repo")
        build_atlas(repo_dir)
        # facts are a garnish: a failure shouldn't block the atlas deploy —
        # the repo checkout already carries the last committed facts.json
        try:
            build_facts(repo_dir)
        except Exception as e:
            get_run_logger().warning(
                f"build-facts failed, deploying with committed facts.json: {e}"
            )
        deploy_to_pages(repo_dir / "site")


if __name__ == "__main__":
    rebuild_atlas()
