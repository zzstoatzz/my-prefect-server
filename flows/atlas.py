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

import os
import subprocess
import tempfile
from pathlib import Path

from prefect import flow, get_run_logger, task

REPO_URL = "https://github.com/zzstoatzz/leaflet-search.git"
CF_ACCOUNT_ID = "3e9ba01cd687b3c4d29033908177072e"
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
    # Pin BUN_INSTALL so we know exactly where the installer puts the
    # binary, then invoke it by absolute path — don't trust PATH lookup
    # across subprocess boundaries.
    bun_install = "/tmp/bun"
    bun_bin = f"{bun_install}/bin/bun"
    env = {
        **os.environ,
        "CLOUDFLARE_ACCOUNT_ID": CF_ACCOUNT_ID,
        "BUN_INSTALL": bun_install,
        "PATH": f"{bun_install}/bin:{os.environ.get('PATH', '')}",
    }

    # install bun if missing — single self-contained binary
    if not Path(bun_bin).is_file():
        install = subprocess.run(
            ["bash", "-c", "curl -fsSL https://bun.sh/install | bash"],
            env=env, capture_output=True, text=True, timeout=180, check=True,
        )
        if not Path(bun_bin).is_file():
            logger.error(f"installer stdout:\n{install.stdout}")
            logger.error(f"installer stderr:\n{install.stderr}")
            raise RuntimeError(
                f"bun installer ran but binary missing at {bun_bin} "
                f"(BUN_INSTALL={bun_install}); see installer output above"
            )

    # install site dependencies (workers-og + wrangler from package.json)
    subprocess.run(
        [bun_bin, "install"],
        cwd=str(site_dir),
        env=env, capture_output=True, text=True, timeout=180, check=True,
    )

    # `bun x wrangler` runs wrangler with bun as its runtime
    result = subprocess.run(
        [bun_bin, "x", "wrangler", "pages", "deploy", ".",
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
