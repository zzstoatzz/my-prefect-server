"""Move the existing cloud build worker to HeavyPad after its original runs drain."""

import argparse
import json
import shlex
import subprocess
from pathlib import Path

import httpx
from dotenv import dotenv_values

REMOTE = r"""
import base64,datetime,hashlib,json,os,shlex,subprocess,sys,tempfile,urllib.request
from pathlib import Path
os.umask(0o077)
payload=json.load(sys.stdin)
request=urllib.request.Request(payload['values']['PREFECT_API_URL']+'/deployments/a0c0ce9c-5bab-4bdc-8512-39984c322a74',headers={'Authorization':'Basic '+base64.b64encode(payload['values']['PREFECT_API_AUTH_STRING'].encode()).decode()})
with urllib.request.urlopen(request,timeout=20) as response:
    assert json.load(response)['name']=='typeahead-index'
path=Path('/etc/typeahead-cloud-worker.env')
original=path.read_text()
lines=original.splitlines()
for name,value in payload['values'].items():
    assert '\n' not in value and '\x00' not in value
    matches=[i for i,line in enumerate(lines) if line.startswith(name+'=')]
    assert len(matches)==1, 'Expected exactly one setting: '+name
    lines[matches[0]]=name+'='+json.dumps(value)
expected='\n'.join(lines)+'\n'
changed=expected!=original
if payload['apply'] and changed:
    backup=Path('/root/prefect-migration-backups')
    backup.mkdir(mode=0o700,exist_ok=True)
    (backup/('typeahead-worker-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.env')).write_text(original)
    subprocess.run(['systemctl','stop','prefect-typeahead-worker'],check=True)
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as f:
        f.write(expected)
        tmp=Path(f.name)
    os.chmod(tmp,0o600)
    tmp.replace(path)
    subprocess.run(['systemctl','start','prefect-typeahead-worker'],check=True)
actual=path.read_text()
if payload['apply'] or not changed:
    assert actual==expected
    assert subprocess.run(['systemctl','is-active','--quiet','prefect-typeahead-worker']).returncode==0
print(json.dumps({'changed':changed,'applied':payload['apply'],'environment_sha256':hashlib.sha256(actual.encode()).hexdigest()}))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    original = dotenv_values(Path(__file__).resolve().parent.parent / ".env")
    with httpx.Client(
        base_url=f"https://{original['DOMAIN']}/api/",
        auth=tuple(original["AUTH_STRING"].split(":", 1)),
        timeout=30,
    ) as api:
        response = api.get("deployments/5d446811-c098-438e-b16c-f1ccd9da7e97")
        response.raise_for_status()
        assert not any(s["active"] for s in response.json()["schedules"])
        response = api.post(
            "flow_runs/filter",
            json={
                "flow_runs": {
                    "state": {
                        "type": {
                            "any_": ["SCHEDULED", "PENDING", "RUNNING", "PAUSED", "CANCELLING"]
                        }
                    }
                },
                "limit": 500,
            },
        )
        response.raise_for_status()
        runs = response.json()
        assert len(runs) < 500
        assert not any(
            r["deployment_id"] == "5d446811-c098-438e-b16c-f1ccd9da7e97" for r in runs
        ), "Drain original cloud builds first"
    credentials = json.loads(
        subprocess.check_output(
            [
                "sops",
                "decrypt",
                "--output-type",
                "json",
                "--extract",
                '["heavypad_prefect"]',
                str(Path.home() / "tangled.org/zzstoatzz.io/secrets/prod.yaml"),
            ],
            text=True,
        )
    )
    values = {
        "PREFECT_API_URL": "http://100.96.216.23:14200/api",
        "PREFECT_API_AUTH_STRING": credentials["api_auth_string"],
    }
    result = subprocess.check_output(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "root@100.78.52.120",
            "python3 -c " + shlex.quote(REMOTE),
        ],
        input=json.dumps({"apply": args.apply, "values": values}),
        text=True,
    )
    report = json.loads(result)
    print(json.dumps(report))
    if args.apply:
        print(
            "Cloud build worker uses the private HeavyPad API; compute and disks remain in the cloud"
        )


if __name__ == "__main__":
    main()
