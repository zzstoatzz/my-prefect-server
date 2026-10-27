"""Run a configured control-plane maintenance operation."""

import argparse
import subprocess

from prefect_target import target_config

COMMANDS = {
    "backup": "~/.local/share/prefect-home/backup.sh",
    "offsite": "systemctl --user start --wait prefect-home-offsite.service",
    "offsite-verify": "systemctl --user start --wait prefect-home-offsite-verify.service"
    " && tail -n 1 ~/.local/state/prefect-home/restore-verifications.log",
}

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("operation", choices=COMMANDS)
args = parser.parse_args()
raise SystemExit(
    subprocess.run(
        ["ssh", "-o", "BatchMode=yes", target_config()["ssh_host"], COMMANDS[args.operation]],
        check=False,
    ).returncode
)
