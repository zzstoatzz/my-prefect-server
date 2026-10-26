"""Render or verify HeavyPad-only consumers of the canonical secret store."""

import argparse
import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path

REMOTE = "stoat@heavypad"
KUBE = "DOCKER_HOST=unix:///var/run/docker.sock docker exec -i k3d-prefect-home-server-0 kubectl"


def remote(command: str, payload: str | None = None) -> str:
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", REMOTE, command],
        input=payload,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def github_block(auth: str, apply: bool) -> None:
    token = subprocess.run(
        [
            "sops",
            "decrypt",
            "--extract",
            '["prefect"]["blocks"]["github-token"]',
            str(Path.home() / "tangled.org/zzstoatzz.io/secrets/prod.yaml"),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.rstrip("\n")
    os.environ["PREFECT_API_URL"] = "https://heavypad.tailb660b6.ts.net:10443/api"
    os.environ["PREFECT_API_AUTH_STRING"] = auth
    from prefect.blocks.system import Secret
    from prefect.utilities.asyncutils import run_coro_as_sync

    current = run_coro_as_sync(Secret.aload("github-token")).get()
    if current != token:
        if not apply:
            raise RuntimeError("Pilot github-token block drift")
        Secret(value=token).save("github-token", overwrite=True)
        current = run_coro_as_sync(Secret.aload("github-token")).get()
    if current != token:
        raise RuntimeError("Pilot github-token block did not converge")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    credentials = json.loads(
        subprocess.run(
            [
                "sops",
                "decrypt",
                "--output-type",
                "json",
                "--extract",
                '["heavypad_prefect"]',
                str(Path.home() / "tangled.org/zzstoatzz.io/secrets/prod.yaml"),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    values = {
        "api-auth": credentials["api_auth_string"],
        "postgres-password": credentials["postgres_password"],
        "database-url": f"postgresql://prefect:{credentials['postgres_password']}@postgres:5432/prefect",
    }
    env = (
        "PREFECT_API_URL=http://127.0.0.1:14200/api\n"
        f"PREFECT_API_AUTH_STRING={credentials['api_auth_string']}\n"
        "PREFECT_LOCAL_STORAGE_PATH=/home/stoat/.local/share/prefect-home/results\n"
    )
    if args.apply:
        remote(
            KUBE + " apply -f -",
            json.dumps(
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {"name": "server-secrets", "namespace": "prefect-home"},
                    "stringData": values,
                }
            ),
        )
        remote(
            "umask 077; mkdir -p ~/.config/prefect-home; cat > ~/.config/prefect-home/worker.env; chmod 600 ~/.config/prefect-home/worker.env",
            env,
        )
    actual = json.loads(remote(KUBE + " -n prefect-home get secret server-secrets -o json"))["data"]
    for name, value in values.items():
        if (
            hashlib.sha256(base64.b64decode(actual[name])).digest()
            != hashlib.sha256(value.encode()).digest()
        ):
            raise RuntimeError(f"HeavyPad secret drift: {name}")
    digest = remote("sha256sum ~/.config/prefect-home/worker.env").split()[0]
    if digest != hashlib.sha256(env.encode()).hexdigest():
        raise RuntimeError("HeavyPad worker environment drift")
    github_block(credentials["api_auth_string"], args.apply)
    print("HeavyPad consumers already current; no secret values printed")


if __name__ == "__main__":
    main()
