"""Per-attempt userspace tailnet access for the trusted Prefect engine."""

import json
import os
import shlex
from pathlib import Path

import httpx
from prefect_exe.client import ExeClient, NotFound

INSTALL = r"""
import json, os, subprocess, sys, time, urllib.request
from pathlib import Path
os.umask(0o077)
config=json.load(sys.stdin)
root=Path('/var/lib/gardener-tailnet')
root.mkdir(mode=0o700, exist_ok=True)
key=root/'auth.env'
key.write_text('TS_AUTHKEY='+config['key']+'\n')
unit=Path('/etc/systemd/system/gardener-tailnet.service')
unit.write_text('''[Unit]
Description=Private Prefect transport for this execution attempt
[Service]
Type=exec
EnvironmentFile=/var/lib/gardener-tailnet/auth.env
ExecStart=/usr/local/bin/ts-unplug -dir /var/lib/gardener-tailnet/state -hostname '''+config['name']+''' -port 18080 '''+config['target']+'''
Restart=on-failure
RestartSec=2
RuntimeMaxSec='''+str(config['timeout']+600)+'''
StandardOutput=append:/var/lib/gardener-tailnet/transport.log
StandardError=append:/var/lib/gardener-tailnet/transport.log
UMask=0077
''')
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','start','gardener-tailnet'],check=True)
url='http://localhost:18080/api/health'
for attempt in range(60):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            if response.status==200:
                print('Private Prefect transport ready')
                break
    except urllib.error.HTTPError as exc:
        if exc.code==401:
            print('Private Prefect transport ready; authentication required')
            break
    except (OSError, TimeoutError):
        pass
    time.sleep(1)
else:
    raise RuntimeError('Private Prefect transport did not become ready')
"""


class TailnetEnvironment:
    def __init__(self, config: Path, identity_file: Path | None):
        self.config = config
        self.identity_file = identity_file

    async def prepare(self, name: str, timeout: int) -> dict[str, str]:
        config = json.loads(self.config.read_text())
        async with httpx.AsyncClient(timeout=30) as api:
            response = await api.post(
                "https://api.tailscale.com/api/v2/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": config["client_id"],
                    "client_secret": config["client_secret"],
                },
            )
            response.raise_for_status()
            token = response.json()["access_token"]
            response = await api.post(
                "https://api.tailscale.com/api/v2/tailnet/-/keys",
                headers={"Authorization": "Bearer " + token},
                json={
                    "expirySeconds": 600,
                    "description": name,
                    "capabilities": {
                        "devices": {
                            "create": {
                                "reusable": False,
                                "ephemeral": True,
                                "preauthorized": True,
                                "tags": ["tag:gardener-vm"],
                            }
                        }
                    },
                },
            )
            response.raise_for_status()
            key = response.json()["key"]
        async with ExeClient(self.identity_file) as client:
            vm = await client.get(name)
            await client.run(
                vm,
                "sudo -n python3 -c " + shlex.quote(INSTALL),
                stdin=json.dumps(
                    {"key": key, "name": name, "target": config["target"], "timeout": timeout}
                ).encode(),
                timeout=150,
            )
        return {"PREFECT_API_URL": "http://localhost:18080/api"}

    async def release(self, name: str) -> None:
        async with ExeClient(self.identity_file) as client:
            try:
                vm = await client.get(name)
            except NotFound:
                return
            await client.run(
                vm,
                "sudo -n systemctl stop gardener-tailnet.service; "
                "sudo -n rm -f /var/lib/gardener-tailnet/auth.env",
            )


def configured_tailnet() -> TailnetEnvironment | None:
    config = os.environ.get("GARDENER_TAILNET_CONFIG")
    identity = os.environ.get("EXE_IDENTITY_FILE")
    return (
        TailnetEnvironment(Path(config), Path(identity) if identity else None) if config else None
    )
