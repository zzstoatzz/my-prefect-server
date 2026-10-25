"""Forward arguments unchanged to the isolated pilot's embedded kubectl."""

import shlex
import subprocess
import sys

command = [
    "env",
    "DOCKER_HOST=unix:///var/run/docker.sock",
    "docker",
    "exec",
    "-i",
    "k3d-prefect-home-server-0",
    "kubectl",
    *sys.argv[1:],
]
raise SystemExit(
    subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "stoat@heavypad", shlex.join(command)],
        check=False,
    ).returncode
)
