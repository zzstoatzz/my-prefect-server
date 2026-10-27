"""Run proposed repository tests inside an isolated VM without merge credentials."""

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from mps.pi import minimal_env
from mps.pi_execution import tree_paths
from mps.pi_sandbox import AGENT_UID, sandbox_command
from prefect import flow
from prefect.artifacts import create_table_artifact

from flows.pi_pr import CLONE_URL, OWNER, Repo


def test_script(repo: str) -> str:
    """the repo's own test invocation, as its CI runs it."""
    if repo == "bot":
        return (
            "export PATH=/usr/local/bin:$PATH BLUESKY_HANDLE=ci.invalid BLUESKY_PASSWORD=ci; "
            "/usr/local/bin/uv sync --frozen && /usr/local/bin/uv run pytest -q"
            " && cd web && bun install --frozen-lockfile && bun run check && bun run test"
        )
    if repo == "my-prefect-server":
        return (
            "/usr/local/bin/uv sync --frozen --extra dagster --extra inbox"
            " && /usr/local/bin/uv run --frozen --extra dagster --extra inbox pytest -q"
        )
    return "/usr/local/bin/uv sync && /usr/local/bin/uv run pytest -q"


@flow(name="test-pull-patch", log_prints=True, timeout_seconds=2400)
def test_pull_patch(repo: Repo, base: str, patch: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", base):
        raise ValueError("Tests require an exact base commit")
    if os.geteuid() != 0 or not Path("/usr/local/bin/uv").is_file():
        raise RuntimeError("Tests require the prepared VM runtime")
    if shutil.which("bwrap") is None:
        raise RuntimeError("The test image must include bubblewrap")
    if repo == "bot" and not all(
        Path(f"/usr/local/bin/{tool}").is_file() for tool in ("bun", "node")
    ):
        raise RuntimeError("The bot test image must include Bun and Node")
    with TemporaryDirectory(prefix="patch-test-") as directory:
        root = Path(directory)
        workspace, home, tools = root / "repo", root / "home", root / "tools"
        home.mkdir()
        tools.mkdir()
        env = minimal_env(GIT_COMMITTER_NAME="patch-test", GIT_COMMITTER_EMAIL="patch-test@invalid")
        subprocess.run(
            ["git", "clone", CLONE_URL.format(owner=OWNER, repo=repo), str(workspace)],
            env=env,
            check=True,
            timeout=120,
        )
        subprocess.run(
            ["git", "-C", str(workspace), "checkout", "--detach", base],
            env=env,
            check=True,
            timeout=30,
        )
        subprocess.run(
            ["git", "-C", str(workspace), "am"],
            input=patch,
            text=True,
            env=env,
            check=True,
            timeout=30,
        )
        for path in [*tree_paths(workspace), home]:
            os.chown(path, AGENT_UID, AGENT_UID, follow_symlinks=False)
        # No inference socket, orchestration environment, or merge key is mounted.
        script = test_script(repo)
        command = sandbox_command(
            workspace=workspace,
            home=home,
            tools=tools,
            command=["sh", "-c", script],
            network_access=True,
        )
        result = subprocess.run(
            command,
            env={"PATH": "/usr/bin:/bin"},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=1800,
            check=False,
        )
        tail = result.stdout[-4000:]
        print(tail)
        outcome = {
            "passed": result.returncode == 0,
            "tail": tail,
            "base": base,
            "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
        }
        create_table_artifact(key="pull-test-result", table=[outcome])
        return outcome


@flow(name="test-pull-boundary", log_prints=True, timeout_seconds=300)
def test_pull_boundary() -> dict:
    """Verify the test runner's real filesystem, identity, and environment boundary."""
    subprocess.run(["apt-get", "update", "-qq"], check=True, timeout=120)
    subprocess.run(
        ["apt-get", "install", "-y", "--no-install-recommends", "bubblewrap"],
        check=True,
        timeout=120,
    )
    with TemporaryDirectory(prefix="test-boundary-") as directory:
        root = Path(directory)
        workspace, home, tools = root / "repo", root / "home", root / "tools"
        for path in (workspace, home, tools):
            path.mkdir()
            os.chown(path, AGENT_UID, AGENT_UID)
        canary = root / "merge-key-canary"
        canary.write_text("not-a-real-secret")
        (workspace / "escape").symlink_to(canary)
        assertions = """
import multiprocessing, os, socket, subprocess, tempfile
from pathlib import Path
assert os.getuid() == 2000
assert os.getgroups() == []
assert 'CapEff:\\t0000000000000000' in Path('/proc/self/status').read_text()
assert 'NoNewPrivs:\\t1' in Path('/proc/self/status').read_text()
for key in ('PREFECT_API_AUTH_STRING', 'PREFECT_API_URL', 'PHI_INFERENCE_TOKEN'):
    assert key not in os.environ, key
for path in ('/root', '/.sprite/api.sock', '/var/lib/prefect-sprites', '/workspace/escape'):
    assert not Path(path).exists(), path
try:
    os.setuid(0)
except PermissionError:
    pass
else:
    raise AssertionError('regained root')
assert Path('/etc/resolv.conf').read_text(), 'resolver config must be readable'
assert socket.getaddrinfo('pypi.org', 443)
assert Path('/tmp').stat().st_mode & 0o7777 == 0o1777
assert Path('/dev/shm').stat().st_mode & 0o7777 == 0o1777
with multiprocessing.Semaphore():
    pass
with tempfile.TemporaryDirectory(dir='/tmp') as temp:
    endpoint = socket.socket(socket.AF_UNIX)
    endpoint.bind(str(Path(temp) / 'test.sock'))
    endpoint.close()
assert subprocess.check_output(['jq', '-n', '1 + 1'], text=True).strip() == '2'
Path('/workspace/verified').write_text('ok')
print('test boundary passed; public DNS available; host files and credentials hidden')
"""
        command = sandbox_command(
            workspace=workspace,
            home=home,
            tools=tools,
            command=["/usr/bin/python3", "-c", assertions],
            network_access=True,
        )
        subprocess.run(
            command,
            check=True,
            timeout=30,
            env={"PATH": "/usr/bin:/bin", "PREFECT_API_AUTH_STRING": "fake-canary"},
        )
        assert (workspace / "verified").read_text() == "ok"
        assert canary.read_text() == "not-a-real-secret"
        return {"boundary_verified": True, "public_dns": True}
