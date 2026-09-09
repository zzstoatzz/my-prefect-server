"""Sprite submission and inspection independent of a worker process lifetime."""

import base64
import io
import json
from importlib.resources import files
from pathlib import Path

from sprites import AsyncSprite
from sprites.exceptions import APIError, NotFoundError

DIRECTORY = "/var/lib/prefect-sprites"
SERVICE = "prefect-flow"

# Neither source, environment nor a user command appears in exec URL parameters.
# Files live behind a root-only directory, outside an agent's eventual workspace.
INSTALL = r"""
import base64, fcntl, hashlib, json, os, pathlib, subprocess, sys
os.umask(0o077)
root = pathlib.Path("/var/lib/prefect-sprites")
root.mkdir(mode=0o700, parents=True, exist_ok=True)
payload = json.load(sys.stdin)
with (root / "install.lock").open("w") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    marker = root / "installed"
    if marker.exists():
        if marker.read_text() != digest:
            raise RuntimeError("Sprite already contains a different execution payload")
    else:
        uv = pathlib.Path("/usr/local/bin/uv")
        expected = "uv 0.12.12"
        if not uv.exists() or not subprocess.check_output([str(uv), "--version"], text=True).startswith(expected + " "):
            installer = root / "uv-install.sh"
            try:
                subprocess.run(
                    ["curl", "--fail", "--silent", "--show-error", "--location",
                     "--max-time", "30", "https://astral.sh/uv/0.12.12/install.sh",
                     "--output", str(installer)], check=True,
                )
                subprocess.run(
                    ["sh", str(installer)], check=True,
                    env={**os.environ, "UV_UNMANAGED_INSTALL": "/usr/local/bin"},
                )
            finally:
                installer.unlink(missing_ok=True)
        environment = root / "venv"
        subprocess.run(
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
        subprocess.run(
            [str(uv), "pip", "install", "--python", str(environment / "bin/python"),
             *payload["config"]["requirements"], *package_paths],
            check=True,
        )
        (root / "runtime.py").write_text(payload["runtime"])
        (root / "config.json").write_text(json.dumps(payload["config"]))
        marker.write_text(digest)
print("installed")
"""

INSPECT = r"""
import json, pathlib
root = pathlib.Path("/var/lib/prefect-sprites")
path = root / "state.json"
state = json.loads(path.read_text()) if path.exists() else {"phase": "prepared"}
log = root / "output.log"
if state["phase"] == "exited" and log.exists():
    with log.open("rb") as file:
        file.seek(max(0, log.stat().st_size - 65536))
        state["log_tail"] = file.read().decode(errors="replace")
print(json.dumps(state))
"""


async def install(
    sprite: AsyncSprite, config: dict, *, local_packages: list[str] | None = None
) -> None:
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
        "runtime": files("prefect_sprites").joinpath("runtime.py").read_text(),
        "packages": packages,
    }
    await sprite.command(
        "sudo",
        "-n",
        "python3",
        "-c",
        INSTALL,
        stdin=io.BytesIO(json.dumps(payload).encode()),
        timeout=300,
    ).output()


async def inspect(sprite: AsyncSprite) -> dict:
    result = await sprite.command("sudo", "-n", "python3", "-c", INSPECT, timeout=30).output()
    state = json.loads(result)
    if state.get("phase") not in {"prepared", "running", "exited"}:
        raise ValueError("Sprite returned an invalid execution phase")
    return state


async def start(sprite: AsyncSprite) -> None:
    events = await sprite.create_service(
        SERVICE,
        cmd="sudo",
        args=["-n", f"{DIRECTORY}/venv/bin/python", f"{DIRECTORY}/runtime.py"],
        duration=0.1,
    )
    for event in events:
        if event.type == "error":
            raise RuntimeError("Sprite rejected the Prefect runtime service")


async def get_service(sprite: AsyncSprite):
    """Normalize missing services across the SDK's HTTP client surfaces."""
    try:
        return await sprite.get_service(SERVICE)
    except NotFoundError:
        return None
    except APIError as exc:
        if exc.status_code == 404:
            return None
        raise
