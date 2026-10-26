"""Copy selected monitoring notification dependencies into HeavyPad; never send a test message."""

import argparse
import copy
import hashlib
import json
import subprocess
from pathlib import Path

import httpx
from dotenv import dotenv_values
from prefect.blocks.notifications import DiscordWebhook
from prefect.settings import (
    PREFECT_API_AUTH_STRING,
    PREFECT_API_KEY,
    PREFECT_API_URL,
    temporary_settings,
)

TARGET = "https://heavypad.tailb660b6.ts.net:10443/api"
NAMES = {"sustained burn -> discord", "flow-run failure -> discord", "fleet unhealthy -> discord"}


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    original = dotenv_values(Path(__file__).resolve().parent.parent / ".env")
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
    with (
        httpx.Client(
            base_url=f"https://{original['DOMAIN']}/api/",
            auth=tuple(original["AUTH_STRING"].split(":", 1)),
        ) as old,
        httpx.Client(
            base_url=TARGET + "/",
            auth=tuple(credentials["api_auth_string"].split(":", 1)),
            timeout=30,
        ) as new,
    ):

        def request(client, method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json() if response.content else None

        wanted = [
            a for a in request(old, "POST", "automations/filter", json={}) if a["name"] in NAMES
        ]
        assert {a["name"] for a in wanted} == NAMES
        block_ids = {action["block_document_id"] for a in wanted for action in a["actions"]}
        assert len(block_ids) == 1
        block = request(
            old, "GET", f"block_documents/{block_ids.pop()}", params={"include_secrets": "true"}
        )
        assert block["block_type"]["slug"] == "discord-webhook"
        if args.apply:
            with temporary_settings(
                {
                    PREFECT_API_URL: TARGET,
                    PREFECT_API_AUTH_STRING: credentials["api_auth_string"],
                    PREFECT_API_KEY: None,
                }
            ):
                DiscordWebhook(**block["data"]).save(block["name"], overwrite=True)
        target = request(
            new,
            "GET",
            f"block_types/slug/discord-webhook/block_documents/name/{block['name']}",
            params={"include_secrets": "true"},
        )
        assert digest(block["data"]) == digest(target["data"]), "Notification block differs"
        existing = {a["name"]: a for a in request(new, "POST", "automations/filter", json={})}
        for automation in wanted:
            body = {
                k: copy.deepcopy(automation[k])
                for k in ("name", "description", "enabled", "trigger", "actions")
            }
            body["trigger"].pop("id", None)
            for action in body["actions"]:
                action["block_document_id"] = target["id"]
                for key in ("body", "subject"):
                    if key in action:
                        action[key] = action[key].replace(
                            f"https://{original['DOMAIN']}", TARGET.removesuffix("/api")
                        )
            if args.apply:
                if body["name"] in existing:
                    request(new, "PUT", f"automations/{existing[body['name']]['id']}", json=body)
                else:
                    request(new, "POST", "automations/", json=body)
            else:
                current = existing.get(body["name"])
                assert current is not None, f"Missing automation: {body['name']}"
                current_trigger = copy.deepcopy(current["trigger"])
                current_trigger.pop("id", None)
                assert current_trigger == body["trigger"], (
                    f"Automation trigger drift: {body['name']}"
                )
                for key in ("enabled", "actions"):
                    assert current[key] == body[key], f"Automation drift: {body['name']} {key}"
            print(f"Verified notification dependency: {body['name']}")
        print("Notification block matches by hash; no message sent")


if __name__ == "__main__":
    main()
