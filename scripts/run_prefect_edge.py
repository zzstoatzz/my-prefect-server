"""Run the migrated app service on a loopback-only Docker port."""

import argparse
import os
import socket
import subprocess
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

from prefect_target import target_config

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("service", choices=["hub", "push"])
args = parser.parse_args()
root = Path(__file__).resolve().parent.parent
service = tomllib.loads((root / "deploy/prefect-edge/services.toml").read_text())[args.service]
home = Path.home()
config = home / ".config/prefect-edge"
state = home / ".local/share/prefect-edge"
host = urlsplit(target_config()["api_url"]).hostname
address = socket.gethostbyname(host)
command = [
    "docker",
    "run",
    "--rm",
    "--name",
    "prefect-" + args.service,
    "--publish",
    f"127.0.0.1:{service['port']}:{service['container_port']}",
    "--add-host",
    host + ":" + address,
    "--env-file",
    str(config / (args.service + ".env")),
    "--memory",
    service["memory"],
    "--cpus",
    "0.5",
]
if args.service == "hub":
    command += ["--pull=never", "--volume", str(home / "prefect-analytics") + ":/analytics:ro"]
else:
    if not (state / "push-data/prefect-push.db").is_file():
        raise RuntimeError("Preserved push database must be installed before startup")
    command += [
        "--volume",
        str(state / "push-data") + ":/data",
        "--volume",
        str(state / "push-src" / service["source_revision"]) + ":/app/src:ro",
        "--volume",
        str(config) + ":/config:ro",
    ]
command += [service["image"]]
if args.service == "push":
    command += ["bun", "/app/src/main.ts"]
os.environ["DOCKER_HOST"] = "unix:///var/run/docker.sock"
raise SystemExit(subprocess.run(command, check=False).returncode)
