import base64
import fcntl
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time


def install(payload, root, baked=pathlib.Path("/opt/gardener/venv")):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    timings = {}

    def measured(stage, command, **kwargs):
        started = time.monotonic()
        try:
            return subprocess.run(command, check=True, **kwargs)
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
            return
        environment = root / "venv"
        if payload["config"].get("environment_mode") == "image":
            if payload["packages"] or payload["config"]["requirements"]:
                raise ValueError("Image environments cannot install packages at runtime")
            measured("image_verify", [str(baked / "bin/python"), "-c", "import prefect, mps"])
            if not environment.exists():
                environment.symlink_to(baked, target_is_directory=True)
            if environment.resolve() != baked.resolve():
                raise RuntimeError("Execution environment does not match the baked image")
        else:
            uv = pathlib.Path("/usr/local/bin/uv")
            expected = "uv 0.12.23"
            if not uv.exists() or not subprocess.check_output(
                [str(uv), "--version"], text=True
            ).startswith(expected + " "):
                installer = root / "uv-install.sh"
                try:
                    measured(
                        "uv_download",
                        [
                            "curl",
                            "--fail",
                            "--silent",
                            "--show-error",
                            "--location",
                            "--max-time",
                            "30",
                            "https://astral.sh/uv/0.12.23/install.sh",
                            "--output",
                            str(installer),
                        ],
                    )
                    measured(
                        "uv_install",
                        ["sh", str(installer)],
                        env={**os.environ, "UV_UNMANAGED_INSTALL": "/usr/local/bin"},
                    )
                finally:
                    installer.unlink(missing_ok=True)
            wheels = root / "wheels"
            wheels.mkdir(exist_ok=True)
            requirements = list(payload["config"]["requirements"])
            for package in payload["packages"]:
                name = package["name"]
                if pathlib.Path(name).name != name or not name.endswith(".whl"):
                    raise ValueError("Invalid wheel filename")
                path = wheels / name
                path.write_bytes(base64.b64decode(package["data"], validate=True))
                requirements.append(f"{name.split('-')[0]} @ {path.as_uri()}")
            project = root / "environment"
            project.mkdir(exist_ok=True)
            (project / "pyproject.toml").write_text(
                '[project]\nname = "prefect-exe-attempt"\nversion = "0.0.0"\n'
                'requires-python = "==3.13.*"\ndependencies = ' + json.dumps(requirements) + "\n"
            )
            measured(
                "dependency_install",
                [
                    str(uv),
                    "sync",
                    "--project",
                    str(project),
                    "--python",
                    "3.13.7",
                    "--no-dev",
                    "--no-editable",
                ],
                env={
                    **os.environ,
                    "UV_PROJECT_ENVIRONMENT": str(environment),
                    "UV_LINK_MODE": "copy",
                },
            )
        (root / "runtime.py").write_text(payload["runtime"])
        (root / "config.json").write_text(json.dumps(payload["config"]))
        marker.write_text(digest)


if __name__ == "__main__":
    os.umask(0o077)
    install(json.load(sys.stdin), pathlib.Path("/var/lib/prefect-exe"))
    print("installed")
