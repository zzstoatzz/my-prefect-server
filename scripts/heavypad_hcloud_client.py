"""Render the builder's existing project credential from canonical SOPS."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from heavypad_gardener_clients import REMOTE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
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
    token = blocks["hetzner-prefect-server-api-token"]
    if not token.isalnum():
        raise ValueError("Unexpected project token encoding")
    value = "HCLOUD_TOKEN=" + token + "\n"
    import shlex

    output = subprocess.check_output(
        ["ssh", "-o", "BatchMode=yes", "stoat@heavypad", "python3 -c " + shlex.quote(REMOTE)],
        input=json.dumps({"files": {"hcloud-builder.env": value}, "apply": args.apply}),
        text=True,
    )
    assert "hcloud-builder.env: " + hashlib.sha256(value.encode()).hexdigest() in output
    print("Verified canonical builder credential; value withheld")


if __name__ == "__main__":
    main()
