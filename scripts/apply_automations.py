#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "pyyaml"]
# ///
"""Apply deploy/automations.yaml to the Prefect server, idempotently.

Standalone automations have no declarative home: prefect.yaml's deployment
`triggers:` only generates run-deployment automations bound to that deployment,
so a send-notification automation can only be made against the API. That is how
`flow-run failure -> discord` ended up existing solely as a database row with
nothing in git to reproduce it.

Name is identity. An automation in the file whose name already exists on the
server is updated in place; one that does not is created. Automations on the
server that are *not* in the file are left alone and reported — `prefect deploy`
owns the `*__automation_1` ones, and deleting them here would fight it.

`deployment: <name>` in an action is resolved to a deployment_id at apply time,
so the file does not carry UUIDs that differ per environment.

usage:
    PREFECT_API_URL=... PREFECT_API_AUTH_STRING=... ./scripts/apply_automations.py
    ./scripts/apply_automations.py --dry-run
"""

import os
import sys
from pathlib import Path

import httpx
import yaml

SPEC = Path(__file__).parent.parent / "deploy" / "automations.yaml"


def auth() -> httpx.BasicAuth | None:
    raw = os.environ.get("PREFECT_API_AUTH_STRING")
    if not raw:
        return None
    user, sep, password = raw.partition(":")
    if not sep:
        raise RuntimeError("PREFECT_API_AUTH_STRING must be user:password")
    return httpx.BasicAuth(user, password)


def resolve_deployments(client: httpx.Client, base: str) -> dict[str, str]:
    r = client.post(f"{base}/deployments/filter", json={"limit": 500})
    r.raise_for_status()
    return {d["name"]: d["id"] for d in r.json()}


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    base = os.environ.get("PREFECT_API_URL", "http://localhost:4200/api").rstrip("/")

    spec = yaml.safe_load(SPEC.read_text())
    wanted = spec.get("automations") or []
    if not wanted:
        print(f"no automations in {SPEC}")
        return 1

    with httpx.Client(auth=auth(), timeout=30.0) as client:
        deployments = resolve_deployments(client, base)

        r = client.post(f"{base}/automations/filter", json={})
        r.raise_for_status()
        existing = {a["name"]: a for a in r.json()}

        for item in wanted:
            body = {k: v for k, v in item.items() if k != "description"}
            body.setdefault("description", (item.get("description") or "").strip())

            for action in body.get("actions", []):
                name = action.pop("deployment", None)
                if name is None:
                    continue
                if name not in deployments:
                    print(f"✗ {item['name']}: no deployment named {name!r} — deploy it first")
                    return 1
                action["deployment_id"] = deployments[name]

            current = existing.get(item["name"])
            verb = "update" if current else "create"
            if dry_run:
                print(f"would {verb}: {item['name']}")
                continue

            if current:
                resp = client.put(f"{base}/automations/{current['id']}", json=body)
            else:
                resp = client.post(f"{base}/automations/", json=body)
            if resp.status_code >= 300:
                print(f"✗ {verb} {item['name']} -> {resp.status_code}: {resp.text[:300]}")
                return 1
            print(f"✓ {verb}d: {item['name']}")

        unmanaged = sorted(set(existing) - {i["name"] for i in wanted})
        if unmanaged:
            print(f"\nleft alone ({len(unmanaged)}, owned by `prefect deploy`):")
            for name in unmanaged:
                print(f"  - {name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
