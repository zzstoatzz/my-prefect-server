"""Preserve existing edge credentials and derive private service files."""

import argparse
import base64
import hashlib
import json
import shlex
import subprocess
from pathlib import Path

import httpx
from prefect_target import target_config

REMOTE = r"""
import hashlib,json,os,pathlib,sys,tempfile
os.umask(0o077)
payload=json.load(sys.stdin)
root=pathlib.Path.home()/'.config/prefect-edge'
root.mkdir(mode=0o700,parents=True,exist_ok=True)
for name,value in payload['files'].items():
    path=root/name
    actual=path.read_text() if path.exists() else None
    if actual!=value and payload['apply']:
        with tempfile.NamedTemporaryFile(mode='w',dir=root,delete=False) as f:
            f.write(value)
            temporary=pathlib.Path(f.name)
        temporary.replace(path)
        actual=path.read_text()
    if actual!=value:raise RuntimeError('Missing or drifted edge consumer: '+name)
    print(name+': '+hashlib.sha256(actual.encode()).hexdigest())
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--import-legacy", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    store = Path.home() / "tangled.org/zzstoatzz.io/secrets/prod.yaml"
    data = json.loads(
        subprocess.check_output(["sops", "decrypt", "--output-type", "json", str(store)], text=True)
    )
    if args.import_legacy:

        def secret(name):
            result = json.loads(
                subprocess.check_output(
                    [
                        "kubectl",
                        "--kubeconfig",
                        str(root / "kubeconfig.yaml"),
                        "-n",
                        "prefect",
                        "get",
                        "secret",
                        name,
                        "-o",
                        "json",
                    ],
                    text=True,
                )
            )
            return {k: base64.b64decode(v).decode() for k, v in result["data"].items()}

        readonly = secret("prefect-auth-ro")
        presence = secret("presence-ingress")
        apns = secret("prefect-push-apns")
        edge = {
            "readonly_auth_string": readonly["auth-string"],
            "presence_webhook_token": presence["webhook-token"],
            "apns_key": apns["key"],
            "apns_key_id": apns["key-id"],
            "apns_team_id": apns["team-id"],
        }
        if "prefect_edge" in data and data["prefect_edge"] != edge:
            raise RuntimeError("Existing canonical edge credentials disagree; refusing overwrite")
        if "prefect_edge" not in data:
            subprocess.run(
                ["sops", "set", "--value-stdin", str(store), '["prefect_edge"]'],
                input=json.dumps(edge),
                text=True,
                check=True,
            )
            data["prefect_edge"] = edge
        print("Existing edge credentials match canonical source; values withheld")
    edge = data["prefect_edge"]
    target = target_config()
    api = target["api_url"]
    with httpx.Client(
        base_url=api + "/", auth=tuple(edge["readonly_auth_string"].split(":", 1)), timeout=30
    ) as client:
        response = client.get("deployments/name/report-presence/phone-presence")
        response.raise_for_status()
        deployment = response.json()["id"]

    def env(values):
        if any("\n" in v for v in values.values()):
            raise ValueError("Invalid environment value")
        return "".join(k + "=" + v + "\n" for k, v in values.items())

    files = {
        "hub.env": env(
            {
                "HOST": "0.0.0.0",
                "PORT": "3000",
                "DUCKDB_PATH": "/analytics/hub.duckdb",
                "CF_ACCESS_ISSUER": "https://zzstoatzz-io.cloudflareaccess.com",
                "CF_ACCESS_AUDIENCE": "87f2a109ecc1f5c82749a973ef356cfe89c52a616521a95e5809c4eb5f3922d5",
                "GARDENER_API_URL": api,
                "GARDENER_API_AUTH_STRING": edge["readonly_auth_string"],
                "PRESENCE_WEBHOOK_TOKEN": edge["presence_webhook_token"],
                "PRESENCE_PREFECT_API_URL": api,
                "PRESENCE_PREFECT_API_AUTH_STRING": data["heavypad_prefect"]["api_auth_string"],
                "PRESENCE_PREFECT_DEPLOYMENT_ID": deployment,
            }
        ),
        "push.env": env(
            {
                "PREFECT_API_URL": api,
                "PREFECT_API_AUTH_STRING": edge["readonly_auth_string"],
                "BASE_PATH": "/push",
                "DB_PATH": "/data/prefect-push.db",
                "PORT": "8080",
                "APNS_TOPIC": "io.zzstoatzz.prefect",
                "APNS_KEY_PATH": "/config/apns.p8",
                "APNS_KEY_ID": edge["apns_key_id"],
                "APNS_TEAM_ID": edge["apns_team_id"],
            }
        ),
        "apns.p8": edge["apns_key"],
    }
    result = subprocess.check_output(
        ["ssh", "-o", "BatchMode=yes", target["ssh_host"], "python3 -c " + shlex.quote(REMOTE)],
        input=json.dumps({"files": files, "apply": args.apply}),
        text=True,
    )
    for name, value in files.items():
        assert name + ": " + hashlib.sha256(value.encode()).hexdigest() in result
        print("Verified canonical edge consumer: " + name + "; value withheld")


if __name__ == "__main__":
    main()
