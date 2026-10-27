"""Forward arguments unchanged to the configured production cluster."""

import shlex
import subprocess
import sys

from prefect_target import target_config

target = target_config()

command = [
    "env",
    "DOCKER_HOST=unix:///var/run/docker.sock",
    "docker",
    "exec",
    "-i",
    target["kubectl_container"],
    "kubectl",
    *sys.argv[1:],
]
raise SystemExit(
    subprocess.run(
        ["ssh", "-o", "BatchMode=yes", target["ssh_host"], shlex.join(command)],
        check=False,
    ).returncode
)
