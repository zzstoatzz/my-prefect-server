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
INSTALL = files("prefect_exe").joinpath("bootstrap.py").read_text()


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
        "bootstrap_revision": "uv-project-0.12.23-python-3.13.7-image-v1",
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
