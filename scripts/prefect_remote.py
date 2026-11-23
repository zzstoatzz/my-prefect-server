"""Run a configured control-plane maintenance operation."""

import argparse
import subprocess
from pathlib import Path

from prefect_target import target_config

DR_CONFIG = Path(__file__).parents[1] / "deploy/heavypad-prefect/prefect-dr.toml"

COMMANDS = {
    "backup": "systemctl --user start --wait prefect-home-backup.service",
    "offsite": "systemctl --user start --wait prefect-home-offsite.service",
    "offsite-verify": "systemctl --user start --wait prefect-home-offsite-verify.service"
    " && tail -n 1 ~/.local/state/prefect-home/restore-verifications.log",
    "dr-config": "mkdir -p ~/.config/prefect-dr && umask 022"
    " && cat > ~/.config/prefect-dr/heavypad.toml.new"
    " && mv ~/.config/prefect-dr/heavypad.toml.new ~/.config/prefect-dr/heavypad.toml"
    " && sha256sum ~/.config/prefect-dr/heavypad.toml",
}
STDIN = {"dr-config": DR_CONFIG}

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("operation", choices=COMMANDS)
args = parser.parse_args()
source = STDIN.get(args.operation)
raise SystemExit(
    subprocess.run(
        ["ssh", "-o", "BatchMode=yes", target_config()["ssh_host"], COMMANDS[args.operation]],
        input=source.read_bytes() if source else None,
        check=False,
    ).returncode
)
