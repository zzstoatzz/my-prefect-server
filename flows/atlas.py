"""
Rebuild the atlas (2D semantic map) and deploy to Cloudflare Pages.

Clones leaflet-search, runs the build-atlas script (UMAP + HDBSCAN),
then deploys the site to Cloudflare Pages via the Direct Upload API.

Requires:
  - Secret block "tpuf-token" (turbopuffer API key)
  - Secret block "cloudflare-api-token" (Pages edit permission)
"""

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

import httpx
from prefect import flow, get_run_logger, task
from prefect.blocks.system import Secret

REPO_URL = "https://github.com/zzstoatzz/leaflet-search.git"
CF_API = "https://api.cloudflare.com/client/v4"
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
    """Deploy site/ to Cloudflare Pages via Direct Upload API."""
    logger = get_run_logger()
    headers = {"Authorization": f"Bearer {api_token}"}

    # build manifest: path -> truncated SHA-256
    manifest: dict[str, str] = {}
    content_by_hash: dict[str, bytes] = {}

    for path in sorted(site_dir.rglob("*")):
        if path.is_dir() or path.name.startswith("."):
            continue
        content = path.read_bytes()
        h = hashlib.sha256(content).hexdigest()[:32]
        rel = "/" + str(path.relative_to(site_dir))
        manifest[rel] = h
        content_by_hash[h] = content

    logger.info(f"deploying {len(manifest)} files")

    # create deployment — CF expects multipart/form-data (-F fields in curl)
    # httpx: files={(name, (None, value))} sends multipart form fields
    resp = httpx.post(
        f"{CF_API}/accounts/{CF_ACCOUNT_ID}/pages/projects/{CF_PROJECT}/deployments",
        headers=headers,
        files={
            "manifest": (None, json.dumps(manifest), "application/json"),
            "branch": (None, "main"),
        },
        timeout=60,
    )
    if not resp.is_success:
        logger.error(f"create deployment failed ({resp.status_code}): {resp.text[:500]}")
        resp.raise_for_status()
    deployment = resp.json()["result"]
    jwt = deployment["jwt"]
    logger.info(f"deployment {deployment['id']} created")

    # check which files need uploading
    jwt_headers = {"Authorization": f"Bearer {jwt}"}
    resp = httpx.post(
        f"{CF_API}/pages/assets/check-missing",
        headers=jwt_headers,
        json={"hashes": list(content_by_hash.keys())},
        timeout=30,
    )
    if not resp.is_success:
        logger.error(f"check-missing failed ({resp.status_code}): {resp.text[:500]}")
        resp.raise_for_status()
    missing = set(resp.json())
    logger.info(f"{len(missing)} files to upload ({len(manifest) - len(missing)} cached)")

    # upload missing files
    if missing:
        batch: list[tuple[str, tuple[str, bytes, str]]] = []
        for h in missing:
            batch.append((h, ("blob", content_by_hash[h], "application/octet-stream")))
            if len(batch) >= 50:
                r = httpx.post(
                    f"{CF_API}/pages/assets/upload",
                    headers=jwt_headers,
                    files=batch,
                    timeout=120,
                )
                if not r.is_success:
                    logger.error(f"upload failed ({r.status_code}): {r.text[:500]}")
                    r.raise_for_status()
                batch = []
        if batch:
            r = httpx.post(
                f"{CF_API}/pages/assets/upload",
                headers=jwt_headers,
                files=batch,
                timeout=120,
            )
            if not r.is_success:
                logger.error(f"upload failed ({r.status_code}): {r.text[:500]}")
                r.raise_for_status()

    url = deployment.get("url", "")
    logger.info(f"deployed: {url}")
    return url


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
