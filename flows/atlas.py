"""
Rebuild the atlas (2D semantic map) and deploy to Cloudflare Pages.

Clones leaflet-search, runs the build-atlas script (UMAP + HDBSCAN),
then deploys the site to Cloudflare Pages via wrangler.

Requires:
  - Secret block "tpuf-token" (turbopuffer API key)
  - Secret block "cloudflare-api-token" (Pages edit permission)
"""

import os
import subprocess
import tempfile
from pathlib import Path

from prefect import flow, get_run_logger, task
from prefect.blocks.system import Secret

REPO_URL = "https://github.com/zzstoatzz/leaflet-search.git"
CF_ACCOUNT_ID = "8feb33b5fb57ce2bc093bc6f4141f40a"
CF_PROJECT = "leaflet-search"


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
def build_atlas(repo_dir: Path, tpuf_key: str) -> Path:
    """Run the build-atlas script. Returns path to atlas.json."""
    logger = get_run_logger()
    output = repo_dir / "site" / "atlas.json"

    result = subprocess.run(
        ["uv", "run", "--script", str(repo_dir / "scripts" / "build-atlas"),
         "--output", str(output)],
        env={**os.environ, "TURBOPUFFER_API_KEY": tpuf_key},
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
def deploy_to_pages(site_dir: Path, api_token: str) -> str:
    """Deploy site/ to Cloudflare Pages via wrangler.

    Uses wrangler because the site has Pages Functions (functions/ dir)
    that must be compiled into a _worker.bundle. The raw Direct Upload API
    doesn't handle function bundling, and deploying without it causes 500s.
    """
    logger = get_run_logger()
    env = {
        **os.environ,
        "CLOUDFLARE_API_TOKEN": api_token,
        "CLOUDFLARE_ACCOUNT_ID": CF_ACCOUNT_ID,
    }

    # install node + wrangler if not already available
    subprocess.run(
        ["bash", "-c",
         "command -v npx >/dev/null 2>&1 || "
         "(apt-get update -qq && apt-get install -y -qq nodejs npm >/dev/null 2>&1)"],
        env=env, capture_output=True, timeout=120,
    )
    subprocess.run(
        ["npm", "install", "--global", "wrangler"],
        env=env, capture_output=True, text=True, timeout=120,
    )

    result = subprocess.run(
        ["wrangler", "pages", "deploy", ".",
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
    tpuf_key = Secret.load("tpuf-token").get()
    cf_token = Secret.load("cloudflare-api-token").get()

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = clone_repo(Path(tmpdir) / "repo")
        build_atlas(repo_dir, tpuf_key)
        deploy_to_pages(repo_dir / "site", cf_token)


if __name__ == "__main__":
    rebuild_atlas()
