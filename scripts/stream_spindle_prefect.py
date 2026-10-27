"""Sync Stream credentials or retire the unused MPS CI credentials."""

import argparse
import importlib.util
import json
import shlex
import subprocess
from pathlib import Path

STORE = Path.home() / "tangled.org/zzstoatzz.io/secrets"
REPO = "did:plc:3esqa6ltzsdck6qhsvyusldu"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--repo", choices=["stream", "my-prefect-server"], default="stream")
    args = parser.parse_args()
    repo_id = REPO if args.repo == "stream" else "did:plc:xrndz6e42qj6ygbclfmruugv"
    spec = importlib.util.spec_from_file_location("secret_model", STORE / "bin/model.py")
    assert spec and spec.loader
    model = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(model)
    document = json.loads(
        subprocess.run(
            ["sops", "decrypt", "--output-type", "json", str(STORE / "prod.yaml")],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    wanted = {
        key: value
        for repo, key, _, value in model.spindle_rows(document)
        if repo == repo_id and key in {"PREFECT_API_URL", "PREFECT_API_AUTH_STRING"}
    }
    if args.repo == "my-prefect-server":
        assert not wanted, "MPS registration runs on HeavyPad, not in the CI guest"
    else:
        assert len(wanted) == 2
        assert wanted["PREFECT_API_URL"] == "https://heavypad.tailb660b6.ts.net:10443/api", (
            "Canonical spindle target must be HeavyPad"
        )
        assert (
            wanted["PREFECT_API_AUTH_STRING"] == document["heavypad_prefect"]["api_auth_string"]
        ), "Canonical spindle auth must be HeavyPad"
    assert not document["spindle"][repo_id].get("_inject_prefect"), (
        "Do not inject original credentials"
    )
    program = """
import json,sqlite3,sys
payload=json.load(sys.stdin)
with sqlite3.connect('/var/lib/spindle/spindle.db') as db:
    drift=[]
    for key in ('PREFECT_API_URL','PREFECT_API_AUTH_STRING'):
        if key not in payload['wanted']:
            current=db.execute('SELECT 1 FROM secrets WHERE repo=? AND key=?',(payload['repo'],key)).fetchone()
            if current:
                drift.append(key)
                if payload['apply']:
                    db.execute('DELETE FROM secrets WHERE repo=? AND key=?',(payload['repo'],key))
            continue
        value=payload['wanted'][key]
        current=db.execute('SELECT value FROM secrets WHERE repo=? AND key=?',(payload['repo'],key)).fetchone()
        assert current is not None, 'Missing existing spindle consumer: '+key
        if current[0]!=value:
            drift.append(key)
            if payload['apply']:
                db.execute('UPDATE secrets SET value=? WHERE repo=? AND key=?',(value,payload['repo'],key))
    if drift and not payload['apply']:
        print('Scoped spindle credential drift: '+', '.join(drift))
        sys.exit(2)
    for key in ('PREFECT_API_URL','PREFECT_API_AUTH_STRING'):
        if key not in payload['wanted']:
            assert db.execute('SELECT 1 FROM secrets WHERE repo=? AND key=?',(payload['repo'],key)).fetchone() is None
    for key,value in payload['wanted'].items():
        assert db.execute('SELECT value FROM secrets WHERE repo=? AND key=?',(payload['repo'],key)).fetchone()[0]==value
print('Scoped spindle Prefect consumers current; changed='+str(len(drift))+'; no values printed')
"""
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", model.HEAVYPAD, "python3 -c " + shlex.quote(program)],
        input=json.dumps({"repo": repo_id, "wanted": wanted, "apply": args.apply}),
        capture_output=True,
        text=True,
        check=False,
    )
    print(result.stdout.strip())
    if result.returncode:
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
