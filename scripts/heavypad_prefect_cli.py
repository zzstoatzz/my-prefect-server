"""Run the Prefect CLI against the isolated HeavyPad pilot."""

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    result = subprocess.run(
        [
            "sops",
            "decrypt",
            "--output-type",
            "json",
            "--extract",
            '["heavypad_prefect"]',
            str(Path.home() / "tangled.org/zzstoatzz.io/secrets/prod.yaml"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    credentials = json.loads(result.stdout)
    env = {
        **os.environ,
        "PREFECT_API_URL": "https://heavypad.tailb660b6.ts.net:10443/api",
        "PREFECT_API_AUTH_STRING": credentials["api_auth_string"],
    }
    return subprocess.run(
        ["uv", "run", "--with", "prefect==3.8.2", "prefect", *sys.argv[1:]],
        env=env,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
