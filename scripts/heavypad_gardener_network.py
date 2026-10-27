"""Add the Gardener-only HeavyPad API grant, preserving all existing policy."""

import argparse
import json
import re
import subprocess
from pathlib import Path

import httpx
import json5

GRANT = '{"src": ["tag:gardener-vm"], "dst": ["100.96.216.23"], "ip": ["tcp:14200"]}'
TEST = '{"src": "tag:gardener-vm", "accept": ["100.96.216.23:14200"], "deny": ["100.96.216.23:22", "100.96.216.23:6555", "100.96.216.23:10443"]}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    store = Path.home() / "tangled.org/zzstoatzz.io/secrets/prod.yaml"
    credentials = json.loads(
        subprocess.check_output(
            ["sops", "decrypt", "--output-type", "json", "--extract", '["tailscale"]', str(store)],
            text=True,
        )
    )
    with httpx.Client(timeout=30) as api:
        response = api.post(
            "https://api.tailscale.com/api/v2/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": credentials["operator_oauth_client_id"],
                "client_secret": credentials["operator_oauth_client_secret"],
            },
        )
        response.raise_for_status()
        api.headers["Authorization"] = "Bearer " + response.json()["access_token"]
        endpoint = "https://api.tailscale.com/api/v2/tailnet/-/acl"
        response = api.get(endpoint, headers={"Accept": "application/hujson"})
        response.raise_for_status()
        baseline = response.text
        etag = response.headers["etag"]
        updated = baseline
        parsed = json5.loads(baseline)
        if json.loads(GRANT) not in parsed["grants"]:
            updated, count = re.subn(
                r'("grants"\s*:\s*\[)', lambda m: m[1] + "\n\t\t" + GRANT + ",", updated, count=1
            )
            assert count == 1
        if json.loads(TEST) not in parsed["tests"]:
            updated, count = re.subn(
                r'("tests"\s*:\s*\[)', lambda m: m[1] + "\n\t\t" + TEST + ",", updated, count=1
            )
            assert count == 1
        if args.apply and updated != baseline:
            response = api.post(
                endpoint,
                content=updated,
                headers={"If-Match": etag, "Content-Type": "application/hujson"},
            )
            response.raise_for_status()
            current = api.get(endpoint, headers={"Accept": "application/hujson"})
            current.raise_for_status()
            verified = json5.loads(current.text)
            assert json.loads(GRANT) in verified["grants"]
            assert json.loads(TEST) in verified["tests"]
            updated = current.text
        Path("infra/tailnet-policy.hujson").write_text(updated)
        print(
            "Gardener HeavyPad API grant "
            + ("verified live" if args.apply else "prepared; not applied")
        )
        print("Existing policy retained; only tag:gardener-vm -> 100.96.216.23:14200 added")


if __name__ == "__main__":
    main()
