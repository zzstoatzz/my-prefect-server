"""Deploy independent builder cleanup using the canonical project credential."""

import json
import os
import subprocess
import tempfile
from pathlib import Path


def main():
    os.umask(0o077)
    root = Path(__file__).resolve().parent.parent
    store = Path.home() / "tangled.org/zzstoatzz.io/secrets/prod.yaml"
    blocks = json.loads(
        subprocess.check_output(
            [
                "sops",
                "decrypt",
                "--output-type",
                "json",
                "--extract",
                '["prefect"]["blocks"]["hetzner-tokens"]',
                str(store),
            ],
            text=True,
        )
    )
    with tempfile.TemporaryDirectory(prefix="builder-reaper-deploy-") as directory:
        secrets = Path(directory) / "secrets.json"
        secrets.write_text(json.dumps({"HCLOUD_TOKEN": blocks["hetzner-prefect-server-api-token"]}))
        subprocess.run(
            ["cf", "deploy", "--secrets-file", str(secrets)],
            cwd=root / "deploy/hcloud-reaper",
            check=True,
        )


if __name__ == "__main__":
    main()
