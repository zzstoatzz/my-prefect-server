"""Render or check the phone ingress's isolated HeavyPad backend."""

import argparse
import base64
import json
import subprocess
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
TARGET = "https://heavypad.tailb660b6.ts.net:10443/api"


def kubectl(*args, payload=None):
    return subprocess.run(
        ["kubectl", "--kubeconfig", str(ROOT / "kubeconfig.yaml"), "-n", "prefect", *args],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
    )


def main():
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
    with httpx.Client(
        base_url=TARGET + "/", auth=tuple(credentials["api_auth_string"].split(":", 1)), timeout=30
    ) as client:
        response = client.get("deployments/name/report-presence/phone-presence")
        response.raise_for_status()
        deployment = response.json()
        assert deployment["work_pool_name"] == "heavypad-pool"
    values = {
        "prefect-api-url": TARGET,
        "prefect-api-auth": credentials["api_auth_string"],
        "deployment-id": deployment["id"],
    }
    original = kubectl("get", "secret", "presence-ingress", "-o", "json")
    assert original.returncode == 0, "Original ingress secret unavailable"
    before = json.loads(original.stdout)["data"]
    if args.apply:
        body = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": "presence-heavypad", "namespace": "prefect"},
            "stringData": values,
        }
        result = kubectl("apply", "-f", "-", payload=json.dumps(body))
        assert result.returncode == 0, "Could not apply isolated presence backend"
    current = kubectl("get", "secret", "presence-heavypad", "-o", "json")
    assert current.returncode == 0, "HeavyPad presence backend missing"
    actual = json.loads(current.stdout)["data"]
    for key, value in values.items():
        assert base64.b64decode(actual[key]).decode() == value, f"Presence backend drift: {key}"
    after = kubectl("get", "secret", "presence-ingress", "-o", "json")
    assert after.returncode == 0 and json.loads(after.stdout)["data"] == before, (
        "Original ingress/Gardener configuration changed"
    )
    print("HeavyPad presence backend current; original credential and Gardener backend preserved")


if __name__ == "__main__":
    main()
