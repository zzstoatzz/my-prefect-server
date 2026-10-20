from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from prefect import flow, get_run_logger, task


def run_command(command: list[str], directory: Path, timeout: int = 900) -> None:
    logger = get_run_logger()
    logger.info("Running %s", " ".join(command))
    environment = dict(os.environ)
    for name in ("PREFECT_API_KEY", "PREFECT_API_AUTH_STRING", "GH_TOKEN", "GITHUB_TOKEN"):
        environment.pop(name, None)
    result = subprocess.run(
        command,
        cwd=directory,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    logger.info("%s", result.stdout[-24000:])
    result.check_returncode()


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
def install_tools(directory: Path) -> None:
    downloads = (
        (
            "uv",
            "https://github.com/astral-sh/uv/releases/download/0.11.14/",
            "uv-x86_64-unknown-linux-gnu.tar.gz",
            "uv-x86_64-unknown-linux-gnu.tar.gz.sha256",
        ),
        (
            "gitleaks",
            "https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/",
            "gitleaks_8.30.1_linux_x64.tar.gz",
            "gitleaks_8.30.1_checksums.txt",
        ),
        (
            "node",
            "https://nodejs.org/dist/v22.16.0/",
            "node-v22.16.0-linux-x64.tar.xz",
            "SHASUMS256.txt",
        ),
    )
    directory.mkdir(exist_ok=True)
    for executable, prefix, filename, checksums in downloads:
        if shutil.which(executable):
            continue
        with urllib.request.urlopen(prefix + checksums, timeout=60) as response:
            lines = response.read().decode().splitlines()
        expected = next(
            line.split()[0]
            for line in lines
            if line.strip() and (len(line.split()) == 1 or line.split()[-1].lstrip("*") == filename)
        )
        with urllib.request.urlopen(prefix + filename, timeout=120) as response:
            content = response.read()
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError(f"Checksum mismatch for {filename}")
        archive = directory / filename
        archive.write_bytes(content)
        with tarfile.open(archive) as bundle:
            bundle.extractall(directory, filter="data")
    bins = [directory, directory / "uv-x86_64-unknown-linux-gnu"]
    bins.append(directory / "node-v22.16.0-linux-x64" / "bin")
    os.environ["PATH"] = os.pathsep.join(map(str, bins)) + os.pathsep + os.environ["PATH"]
    for executable in ("uv", "gitleaks", "node", "npx"):
        if not shutil.which(executable):
            raise RuntimeError(f"Missing required executable: {executable}")


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
def fetch_source(directory: Path, base_sha: str) -> None:
    if not (directory / ".git").exists():
        run_command(["git", "init", str(directory)], directory.parent)
        run_command(
            ["git", "remote", "add", "origin", "https://github.com/PrefectHQ/fastmcp.git"],
            directory,
        )
    run_command(["git", "fetch", "--depth=1", "origin", base_sha], directory)
    run_command(["git", "checkout", "--detach", "FETCH_HEAD"], directory)


@flow(name="fastmcp-validate-patch", timeout_seconds=2400)
def validate_patch(base_sha: str, patch: str) -> dict[str, str]:
    if re.fullmatch(r"[0-9a-f]{40}", base_sha) is None:
        raise ValueError("Expected a full Git commit SHA")
    if not patch.strip() or len(patch.encode()) > 200_000:
        raise ValueError("Expected a nonempty patch of at most 200 KB")
    digest = hashlib.sha256(patch.encode()).hexdigest()
    get_run_logger().info("Validating base=%s patch_sha256=%s", base_sha, digest)
    with tempfile.TemporaryDirectory(prefix="fastmcp-validation-") as temporary:
        root = Path(temporary)
        install_tools(root / "tools")
        repository = root / "fastmcp"
        fetch_source(repository, base_sha)
        patch_file = root / "candidate.patch"
        patch_file.write_text(patch)
        run_command(["git", "apply", "--recount", "--check", str(patch_file)], repository)
        run_command(["git", "apply", "--recount", str(patch_file)], repository)
        run_command(["git", "add", "--intent-to-add", "."], repository)
        changed = (
            subprocess.check_output(["git", "diff", "--name-only", "-z"], cwd=repository)
            .decode()
            .split("\0")
        )
        paths = [path for path in changed if path]
        allowed = ("docs/", "tests/", "fastmcp_slim/", "fastmcp_remote/", "fastmcp_tasks/")
        if not paths or any(not path.startswith(allowed) for path in paths):
            raise ValueError("Patch must only change FastMCP source, tests, or docs")
        run_command(["git", "diff", "--check"], repository)
        before = subprocess.check_output(["git", "diff", "--binary"], cwd=repository)
        run_command(["uv", "sync"], repository)
        run_command(["uv", "run", "pytest", "-n", "auto"], repository, timeout=1200)
        run_command(["uv", "run", "prek", "run", "--all-files"], repository)
        if any(path.startswith("docs/") for path in paths):
            run_command(["uv", "run", "pytest", "tests/docs", "-n", "0"], repository)
            run_command(["npx", "--yes", "mint@latest", "broken-links"], repository / "docs")
        run_command(["git", "diff", "--check"], repository)
        after = subprocess.check_output(["git", "diff", "--binary"], cwd=repository)
        if after != before:
            raise ValueError("Validation changed the candidate; submit the corrected patch")
    return {"base_sha": base_sha, "patch_sha256": digest, "status": "passed"}
