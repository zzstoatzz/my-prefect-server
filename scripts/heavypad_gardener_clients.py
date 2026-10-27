"""Derive the Gardener clients and enrollment credential from canonical SOPS."""

import argparse
import hashlib
import json
import shlex
import subprocess
from pathlib import Path

REMOTE = r"""
import hashlib,json,os,sys,tempfile
from pathlib import Path
os.umask(0o077)
payload=json.load(sys.stdin)
for name, expected in payload['files'].items():
    path=Path.home()/'.config/prefect-home'/name
    actual=path.read_text() if path.exists() else None
    if actual != expected and payload['apply']:
        path.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as f:
            f.write(expected)
            tmp=Path(f.name)
        os.chmod(tmp,0o600)
        tmp.replace(path)
        actual=path.read_text()
    if actual != expected:
        raise RuntimeError('Missing or drifted consumer: '+name)
    print(name+': '+hashlib.sha256(actual.encode()).hexdigest())
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    store = Path.home() / "tangled.org/zzstoatzz.io/secrets/prod.yaml"

    def extract(key):
        return json.loads(
            subprocess.check_output(
                ["sops", "decrypt", "--output-type", "json", "--extract", key, str(store)],
                text=True,
            )
        )

    prefect = extract('["heavypad_prefect"]')
    tailscale = extract('["tailscale"]')
    files = {
        "client.env": "PREFECT_API_URL=https://heavypad.tailb660b6.ts.net:10443/api\n"
        + "PREFECT_API_AUTH_STRING="
        + prefect["api_auth_string"]
        + "\n",
        "gardener-tailnet.json": json.dumps(
            {
                "client_id": tailscale["gardener_oauth_client_id"],
                "client_secret": tailscale["gardener_oauth_client_secret"],
                "target": "100.96.216.23:14200",
            },
            sort_keys=True,
        )
        + "\n",
    }
    output = subprocess.check_output(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "stoat@heavypad",
            "python3 -c " + shlex.quote(REMOTE),
        ],
        input=json.dumps({"files": files, "apply": args.apply}),
        text=True,
    )
    for name, value in files.items():
        assert name + ": " + hashlib.sha256(value.encode()).hexdigest() in output
        print("Verified canonical consumer: " + name + "; value withheld")


if __name__ == "__main__":
    main()
