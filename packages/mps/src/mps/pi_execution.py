"""Trusted Phi caller for Pi's single Sprite execution path."""

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from mps.inference_bridge import inference_bridge
from mps.pi_sandbox import AGENT_UID, PI_ENTRYPOINT, aperture_models, sandbox_command


def prepare_pi_runtime() -> Path:
    """Install Phi's pinned agent tools once, before entering the agent boundary."""
    import fcntl

    tools = Path("/opt/phi-agent")
    relay = Path(__file__).with_name("pi_relay.py")
    revision = f"v2-node-24.18.0-pi-0.84.4-{hashlib.sha256(relay.read_bytes()).hexdigest()}"
    with Path("/var/lib/phi-agent-install.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        marker = tools / "installed"
        if marker.exists() and marker.read_text() == revision:
            return tools
        architecture = {"x86_64": "x64", "aarch64": "arm64"}.get(platform.machine())
        if architecture is None:
            raise RuntimeError("Unsupported Sprite architecture for Phi")
        environment = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/root",
            "DEBIAN_FRONTEND": "noninteractive",
        }
        subprocess.run(["apt-get", "update", "-qq"], env=environment, check=True, timeout=120)
        subprocess.run(
            ["apt-get", "install", "-y", "--no-install-recommends", "bubblewrap", "socat"],
            env=environment,
            check=True,
            timeout=180,
        )
        previous_umask = os.umask(0o022)
        try:
            tools.mkdir(mode=0o755, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="phi-node-") as directory:
                staging = Path(directory)
                archive_name = f"node-v24.18.0-linux-{architecture}.tar.xz"
                for name in (archive_name, "SHASUMS256.txt"):
                    subprocess.run(
                        [
                            "curl",
                            "-fsSL",
                            "--max-time",
                            "120",
                            f"https://nodejs.org/dist/v24.18.0/{name}",
                            "-o",
                            str(staging / name),
                        ],
                        env=environment,
                        check=True,
                        timeout=125,
                    )
                expected = next(
                    line.split()[0]
                    for line in (staging / "SHASUMS256.txt").read_text().splitlines()
                    if line.split()[-1] == archive_name
                )
                if hashlib.sha256((staging / archive_name).read_bytes()).hexdigest() != expected:
                    raise RuntimeError("Node archive checksum did not match its release")
                with tarfile.open(staging / archive_name) as archive:
                    archive.extractall(staging, filter="data")
                shutil.copytree(
                    staging / archive_name.removesuffix(".tar.xz"),
                    tools / "node",
                    dirs_exist_ok=True,
                )
            environment["PATH"] = f"{tools}/node/bin:" + environment["PATH"]
            subprocess.run(
                [
                    str(tools / "node/bin/node"),
                    str(tools / "node/lib/node_modules/npm/bin/npm-cli.js"),
                    "install",
                    "--prefix",
                    str(tools / "pi"),
                    "--no-audit",
                    "--no-fund",
                    "@earendil-works/pi-coding-agent@0.84.4",
                ],
                env=environment,
                check=True,
                timeout=180,
            )
            shutil.copy2(relay, tools / "pi_relay.py")
            (tools / "pi_relay.py").chmod(0o644)
            marker.write_text(revision)
        finally:
            os.umask(previous_umask)
    return tools


def tree_paths(root: Path):
    yield root
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in [*directories, *files]:
            yield Path(directory) / name


def run_isolated_pi(
    prompt: str,
    *,
    workspace: Path,
    tool_args: list[str],
    thinking: str,
    timeout_seconds: int,
    skills: list[str],
) -> str:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise RuntimeError("Phi Pi execution requires the prepared Sprite runtime")
    upstream = os.environ.get("PHI_INFERENCE_URL", "")
    token = os.environ.get("PHI_INFERENCE_TOKEN", "")
    endpoint = urlsplit(upstream)
    if endpoint.scheme != "https" or not endpoint.hostname or endpoint.username or not token:
        raise RuntimeError("Sprite execution requires an HTTPS inference endpoint and run token")
    workspace = workspace.resolve(strict=True)
    if not workspace.is_dir() or workspace == Path("/"):
        raise ValueError("Pi requires a dedicated scratch directory")
    tools = prepare_pi_runtime()
    owner = workspace.stat()
    with tempfile.TemporaryDirectory(prefix="phi-pi-", dir="/tmp") as temporary:
        root = Path(temporary)
        home = root / "home"
        config = home / ".pi/agent"
        config.mkdir(parents=True)
        (config / "models.json").write_text(json.dumps(aperture_models()))
        skill_args = []
        for index, source in enumerate(skills):
            source_path = Path(source)
            target = home / "skills" / str(index)
            target.parent.mkdir(exist_ok=True)
            if source_path.is_dir():
                shutil.copytree(source_path, target, symlinks=True)
            else:
                target = target.with_suffix(".md")
                shutil.copy2(source_path, target, follow_symlinks=False)
            skill_args += ["--skill", f"/home/agent/skills/{target.name}"]
        for path in tree_paths(home):
            os.chown(path, AGENT_UID, AGENT_UID, follow_symlinks=False)
        try:
            for path in tree_paths(workspace):
                os.chown(path, AGENT_UID, AGENT_UID, follow_symlinks=False)
            with inference_bridge(
                root / "inference.sock",
                upstream=upstream,
                authorization=f"Bearer {token}",
            ):
                command = sandbox_command(
                    workspace=workspace,
                    home=home,
                    tools=tools,
                    inference_socket=root / "inference.sock",
                    command=[
                        "/usr/bin/python3",
                        "/opt/phi-agent/pi_relay.py",
                        "node",
                        PI_ENTRYPOINT,
                        "--print",
                        "--no-session",
                        "--provider",
                        "aperture",
                        "--model",
                        "openai/gpt-5.6-luna",
                        "--thinking",
                        thinking,
                        *tool_args,
                        *skill_args,
                    ],
                )
                result = subprocess.run(
                    command,
                    input=prompt,
                    text=True,
                    capture_output=True,
                    timeout=timeout_seconds,
                    env={"PATH": "/usr/bin:/bin"},
                    check=False,
                )
                if result.returncode:
                    raise RuntimeError(
                        f"Isolated Pi exited {result.returncode}: {result.stderr[-2000:]}"
                    )
                return result.stdout
        finally:
            # Restore trusted caller ownership without following agent symlinks.
            # The checkout remains untrusted input even after ownership changes.
            for path in tree_paths(workspace):
                os.chown(path, owner.st_uid, owner.st_gid, follow_symlinks=False)
