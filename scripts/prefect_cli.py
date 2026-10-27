"""Run the Prefect CLI against the configured operator target."""

import json
import os
import subprocess
import sys
from pathlib import Path

from prefect_target import target_config


def main() -> int:
    target = target_config()
    if "auth_environment" in target:
        auth = os.environ[target["auth_environment"]]
    else:
        result = subprocess.run(
            [
                "sops",
                "decrypt",
                "--output-type",
                "json",
                "--extract",
                json.dumps([target["auth_sops_section"]]),
                str(Path.home() / "tangled.org/zzstoatzz.io/secrets/prod.yaml"),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        auth = json.loads(result.stdout)[target["auth_sops_field"]]
    env = {
        **os.environ,
        "PREFECT_API_URL": target["api_url"],
        "PREFECT_API_AUTH_STRING": auth,
    }
    args = sys.argv[1:]
    command = ["python", *args[1:]] if args[:1] == ["--script"] else ["prefect", *args]
    return subprocess.run(
        ["uv", "run", "--with", "prefect==3.8.2", *command],
        env=env,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
