"""
Rebuild the atlas (2D semantic map) and deploy to Cloudflare Pages.

Clones leaflet-search, runs the build-atlas script (UMAP + HDBSCAN),
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

import io
import os
import platform
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from prefect import flow, get_run_logger, task

REPO_URL = "https://github.com/zzstoatzz/leaflet-search.git"
CF_ACCOUNT_ID = "3e9ba01cd687b3c4d29033908177072e"
CF_PROJECT = "leaflet-search"

# bun release archives — github.com/oven-sh/bun/releases
_BUN_ARCH_MAP = {
    ("Linux", "x86_64"): "linux-x64",
    ("Linux", "aarch64"): "linux-aarch64",
    ("Darwin", "x86_64"): "darwin-x64",
    ("Darwin", "arm64"): "darwin-aarch64",
}


def _install_bun(bun_install: Path) -> Path:
    """Install bun by downloading the release zip directly.

    Doesn't depend on curl/wget — the prefect worker image (debian-slim)
    ships neither. Uses Python stdlib only.
    """
    key = (platform.system(), platform.machine())
    arch = _BUN_ARCH_MAP.get(key)
    if arch is None:
        raise RuntimeError(f"unsupported platform for bun: {key}")

    url = (
        "https://github.com/oven-sh/bun/releases/latest/download/"
        f"bun-{arch}.zip"
    )
    with urllib.request.urlopen(url, timeout=60) as r:
        zip_bytes = r.read()

    bin_dir = bun_install / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        # archive layout: bun-{arch}/bun
        member = f"bun-{arch}/bun"
        with zf.open(member) as src, (bin_dir / "bun").open("wb") as dst:
            dst.write(src.read())

    bun_bin = bin_dir / "bun"
    bun_bin.chmod(0o755)
    return bun_bin


@task
def clone_repo(dest: Path) -> Path:
    """Shallow-clone leaflet-search to get site files + build script."""
    subprocess.run(
        ["git", "clone", "--depth", "1", REPO_URL, str(dest)],
        check=True,
        capture_output=True,
    )
    return dest


@task
def build_atlas(repo_dir: Path) -> Path:
    """Run the build-atlas script. Returns path to atlas.json.

    The script reads TURBOPUFFER_API_KEY, ANTHROPIC_API_KEY, and (optionally)
    TURSO_URL / TURSO_TOKEN from the inherited environment.
    """
    logger = get_run_logger()
    output = repo_dir / "site" / "atlas.json"

    result = subprocess.run(
        ["uv", "run", "--script", str(repo_dir / "scripts" / "build-atlas"),
         "--output", str(output)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(f"build-atlas failed:\n{result.stderr}")

    for line in result.stdout.strip().splitlines():
        logger.info(line)

    logger.info(f"atlas.json: {output.stat().st_size / 1024:.0f} KB")
    return output


@task
def deploy_to_pages(site_dir: Path) -> str:
    """Deploy site/ to Cloudflare Pages via wrangler.

    Uses wrangler because the site has Pages Functions (functions/ dir)
    that must be compiled into a _worker.bundle. The raw Direct Upload API
    doesn't handle function bundling, and deploying without it causes 500s.

    Wrangler is invoked via bun (single self-contained binary) instead of
    Debian's apt-pinned node — apt only ships node v20 on bookworm and
    current wrangler requires node >=v22. Bun ships its own runtime and
    matches the JS toolchain convention used elsewhere in the repo.

    Reads CLOUDFLARE_API_TOKEN from the inherited environment.
    """
    logger = get_run_logger()
    bun_install = Path("/tmp/bun")
    bun_bin = bun_install / "bin" / "bun"
    env = {
        **os.environ,
        "CLOUDFLARE_ACCOUNT_ID": CF_ACCOUNT_ID,
        "BUN_INSTALL": str(bun_install),
        "PATH": f"{bun_install}/bin:{os.environ.get('PATH', '')}",
    }

    if not bun_bin.is_file():
        logger.info(f"installing bun -> {bun_bin}")
        _install_bun(bun_install)

    # install site dependencies (workers-og + wrangler from package.json)
    subprocess.run(
        [str(bun_bin), "install"],
        cwd=str(site_dir),
        env=env, capture_output=True, text=True, timeout=180, check=True,
    )

    # `bun x wrangler` runs wrangler with bun as its runtime
    result = subprocess.run(
        [str(bun_bin), "x", "wrangler", "pages", "deploy", ".",
         f"--project-name={CF_PROJECT}", "--branch=main", "--commit-dirty=true"],
        cwd=str(site_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    for line in result.stdout.strip().splitlines():
        logger.info(line)
    if result.returncode != 0:
        logger.error(result.stderr)
        raise RuntimeError(f"wrangler deploy failed:\n{result.stderr}")

    # extract deployment URL from wrangler output
    for line in reversed(result.stdout.strip().splitlines()):
        if "https://" in line:
            url = line.split("https://", 1)[1].split()[0]
            return f"https://{url}"
    return ""


@flow(name="rebuild-atlas", log_prints=True)
def rebuild_atlas():
    """Rebuild the 2D semantic map and deploy to Cloudflare Pages."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = clone_repo(Path(tmpdir) / "repo")
        build_atlas(repo_dir)
        deploy_to_pages(repo_dir / "site")


if __name__ == "__main__":
    rebuild_atlas()
