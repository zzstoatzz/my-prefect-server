"""VM submission and inspection independent of a worker process lifetime."""

import asyncio
import base64
import json
import logging
import shlex
import time
from importlib.resources import files
from pathlib import Path

from .client import VM, ExeClient

DIRECTORY = "/var/lib/prefect-exe"
SERVICE = "prefect-flow"

UNIT = f"""[Unit]
Description=Prefect flow run supervisor

[Service]
Type=exec
ExecStart={DIRECTORY}/venv/bin/python {DIRECTORY}/runtime.py
"""

# Source, environment and the user command travel on stdin, never in argv.
# Files live behind a root-only directory, outside an agent's eventual workspace.
INSTALL = r"""
import base64, fcntl, hashlib, json, os, pathlib, subprocess, sys, time
os.umask(0o077)
root = pathlib.Path("/var/lib/prefect-exe")
root.mkdir(mode=0o700, parents=True, exist_ok=True)
payload = json.load(sys.stdin)
timings = {}
def measured_run(stage, *args, **kwargs):
    started = time.monotonic()
    try:
        return subprocess.run(*args, **kwargs)
    finally:
        timings[stage] = time.monotonic() - started
        (root / "bootstrap-timings.json").write_text(json.dumps(timings))
with (root / "install.lock").open("w") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    marker = root / "installed"
    if marker.exists():
        if marker.read_text() != digest:
            raise RuntimeError("VM already contains a different execution payload")
    else:
        uv = pathlib.Path("/usr/local/bin/uv")
        expected = "uv 0.12.12"
        if not uv.exists() or not subprocess.check_output([str(uv), "--version"], text=True).startswith(expected + " "):
            installer = root / "uv-install.sh"
            try:
                measured_run("uv_download",
                    ["curl", "--fail", "--silent", "--show-error", "--location",
                     "--max-time", "30", "https://astral.sh/uv/0.12.12/install.sh",
                     "--output", str(installer)], check=True,
                )
                measured_run("uv_install",
                    ["sh", str(installer)], check=True,
                    env={**os.environ, "UV_UNMANAGED_INSTALL": "/usr/local/bin"},
                )
            finally:
                installer.unlink(missing_ok=True)
        environment = root / "venv"
        measured_run("python_environment",
            [str(uv), "venv", "--clear", "--python", "3.13.7", str(environment)], check=True,
        )
        wheels = root / "wheels"
        wheels.mkdir(exist_ok=True)
        package_paths = []
        for package in payload["packages"]:
            name = package["name"]
            if pathlib.Path(name).name != name or not name.endswith(".whl"):
                raise ValueError("Invalid wheel filename")
            path = wheels / name
            path.write_bytes(base64.b64decode(package["data"], validate=True))
            package_paths.append(str(path))
        if payload["config"]["requirements"] or package_paths:
            measured_run("dependency_install",
                [str(uv), "pip", "install", "--python", str(environment / "bin/python"),
                 *payload["config"]["requirements"], *package_paths],
                check=True,
            )
        (root / "runtime.py").write_text(payload["runtime"])
        (root / "config.json").write_text(json.dumps(payload["config"]))
        marker.write_text(digest)
print("installed")
"""


def _prepare_payload(config: dict, local_packages: list[str] | None) -> bytes:
    packages = []
    names = set()
    for source in local_packages or []:
        path = Path(source)
        if path.suffix != ".whl" or not path.is_file() or path.name in names:
            raise ValueError("Local packages must be wheel files with unique filenames")
        names.add(path.name)
        packages.append({"name": path.name, "data": base64.b64encode(path.read_bytes()).decode()})
    payload = {
        "bootstrap_revision": "uv-0.12.12-python-3.13.7",
        "config": config,
        "runtime": files("prefect_exe").joinpath("runtime.py").read_text(),
        "packages": packages,
    }
    return json.dumps(payload).encode()


async def install(
    client: ExeClient, vm: VM, config: dict, *, local_packages: list[str] | None = None
) -> None:
    started = time.monotonic()
    payload = await asyncio.to_thread(_prepare_payload, config, local_packages)
    logger = logging.getLogger("prefect.worker.exe")
    logger.info("VM payload prepared in %.3fs (%d bytes)", time.monotonic() - started, len(payload))
    started = time.monotonic()
    await client.run(vm, f"sudo -n python3 -c {shlex.quote(INSTALL)}", stdin=payload, timeout=300)
    logger.info("VM transfer and bootstrap completed in %.3fs", time.monotonic() - started)


async def inspect(client: ExeClient, vm: VM) -> dict:
    state_path = shlex.quote(f"{DIRECTORY}/state.json")
    result = await client.run(
        vm, f"sudo -n sh -c 'if [ -e {state_path} ]; then cat {state_path}; fi'"
    )
    if not result.strip():
        return {"phase": "prepared"}
    state = json.loads(result)
    if state.get("phase") not in {"prepared", "running", "exited"}:
        raise ValueError("VM returned an invalid execution phase")
    return state


async def start(client: ExeClient, vm: VM) -> None:
    """Install the supervisor unit and start it without waiting for the flow."""
    unit = shlex.quote(f"/etc/systemd/system/{SERVICE}.service")
    await client.run(
        vm,
        f"sudo -n sh -c 'cat > {unit} && systemctl daemon-reload"
        f" && systemctl start --no-block {SERVICE}'",
        stdin=UNIT.encode(),
    )


async def restart(client: ExeClient, vm: VM) -> None:
    await client.run(vm, f"sudo -n systemctl start --no-block {SERVICE}")


async def stop(client: ExeClient, vm: VM, grace_seconds: int) -> None:
    await client.run(vm, f"sudo -n systemctl stop {SERVICE}", timeout=grace_seconds + 30)


async def service_status(client: ExeClient, vm: VM) -> str | None:
    """The supervisor unit's state, or None when it was never installed."""
    output = await client.run(
        vm, f"systemctl show {SERVICE} --property=LoadState --property=ActiveState"
    )
    properties = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    if properties["LoadState"] == "not-found":
        return None
    return {
        "active": "running",
        "reloading": "running",
        "activating": "starting",
        "deactivating": "stopping",
        "inactive": "stopped",
        "failed": "failed",
    }[properties["ActiveState"]]
