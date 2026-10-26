"""Verify or sync explicitly selected canonical Secret blocks to a recovery target."""

import argparse
import json
import subprocess
from contextlib import ExitStack
from pathlib import Path

from prefect.blocks.system import Secret
from prefect.context import use_profile
from prefect.exceptions import ObjectNotFound
from prefect.settings import (
    PREFECT_API_AUTH_STRING,
    PREFECT_API_KEY,
    PREFECT_API_URL,
    temporary_settings,
)

STORE = Path.home() / "tangled.org/zzstoatzz.io/secrets/prod.yaml"
CLOUD = "https://api.prefect.cloud/api/accounts/12242a57-9f05-4bf5-8853-9bff595d4bab/workspaces/cafa2ffa-f6cc-4ed6-ab76-eaa4ba1ad40e"
PROVIDER_BLOCKS = {"fly-api-token", "cloudflare-costs-token", "neon-api-key", "hetzner-tokens"}
PUBLICATION_BLOCKS = {
    "mcp-atlas-ingest-token",
    "bisk-r2-endpoint",
    "bisk-r2-access-key-id",
    "bisk-r2-secret-access-key",
}


def extract(path):
    return subprocess.run(
        ["sops", "decrypt", "--output-type", "json", "--extract", path, str(STORE)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=["heavypad", "cloud"])
    parser.add_argument(
        "--block",
        action="append",
        choices=sorted(
            PROVIDER_BLOCKS | PUBLICATION_BLOCKS | {"logfire-read-token", "operator-atproto-creds"}
        ),
        required=True,
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    canonical = json.loads(extract('["prefect"]["blocks"]'))
    with ExitStack() as contexts:
        if args.target == "cloud":
            contexts.enter_context(use_profile("bot"))
            settings = {PREFECT_API_URL: CLOUD, PREFECT_API_AUTH_STRING: None}
        else:
            auth = json.loads(extract('["heavypad_prefect"]'))["api_auth_string"]
            settings = {
                PREFECT_API_URL: "https://heavypad.tailb660b6.ts.net:10443/api",
                PREFECT_API_AUTH_STRING: auth,
                PREFECT_API_KEY: None,
            }
        contexts.enter_context(temporary_settings(settings))
        for name in args.block:
            expected = canonical[name]
            target_name = (
                "recovery-" + name if args.target == "cloud" and name in PROVIDER_BLOCKS else name
            )
            try:
                actual = Secret.load(target_name).get()
            except (ObjectNotFound, ValueError):
                actual = None
            if actual != expected:
                if not args.apply:
                    raise RuntimeError(f"Secret block missing or drifted: {target_name}")
                Secret(value=expected).save(target_name, overwrite=True)
            if Secret.load(target_name).get() != expected:
                raise RuntimeError(f"Secret block did not converge: {target_name}")
            print(f"Verified {args.target} Secret block: {target_name}; no value printed")


if __name__ == "__main__":
    main()
