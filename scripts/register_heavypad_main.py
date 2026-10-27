"""Register a green main revision from the trusted HeavyPad host."""

import argparse
import fcntl
import json
import os
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path.home() / ".local/share/prefect-home/registration"
REPO = "https://github.com/zzstoatzz/my-prefect-server.git"
CI = "https://spindle.zzstoatzz.io/xrpc/sh.tangled.ci.queryPipelines"
REPO_DID = "did:plc:xrndz6e42qj6ygbclfmruugv"


def run(*args, cwd=None, capture=False, env=None):
    return subprocess.run(
        args, cwd=cwd, env=env, check=True, text=True, capture_output=capture
    ).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / "lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        source = ROOT / "source"
        if not source.exists():
            run("git", "clone", "--quiet", REPO, str(source))
        assert run("git", "remote", "get-url", "origin", cwd=source, capture=True).strip() == REPO
        run("git", "fetch", "--quiet", "origin", "main", cwd=source)
        sha = run("git", "rev-parse", "origin/main", cwd=source, capture=True).strip()
        query = urllib.parse.urlencode(
            {"repo": REPO_DID, "commits": sha, "kinds": "push", "limit": 1}
        )
        with urllib.request.urlopen(CI + "?" + query, timeout=30) as response:
            pipelines = json.load(response)["pipelines"]
        if not pipelines:
            print("Waiting for main CI:", sha)
            return
        pipeline = pipelines[0]
        assert pipeline["commit"] == sha
        assert pipeline["trigger"]["ref"] == "refs/heads/main"
        checks = [w for w in pipeline["workflows"] if w["name"] == "deploy.yml"]
        if len(checks) != 1 or checks[0]["status"] != "success":
            print("Main CI is not green; no registration:", sha)
            return
        receipt = ROOT / "registered-sha"
        if receipt.exists() and receipt.read_text().strip() == sha:
            print("HeavyPad main registration already current:", sha)
            return
        print("Green main revision eligible for registration:", sha)
        if not args.apply:
            return
        assert not run("git", "status", "--porcelain", cwd=source, capture=True).strip()
        run("git", "checkout", "--quiet", "--detach", sha, cwd=source)
        env = os.environ.copy()
        env["MPS_PIN"] = "@" + sha
        uv = str(Path.home() / ".local/bin/uv")
        run(uv, "run", "scripts/assert_heavypad_target.py", cwd=source, env=env)
        run(
            uv,
            "run",
            "scripts/validate_deployments.py",
            "--config",
            "prefect.heavypad.yaml",
            "--release",
            cwd=source,
            env=env,
        )
        run(
            uv,
            "run",
            "--with",
            "prefect",
            "prefect",
            "--no-prompt",
            "deploy",
            "--prefect-file",
            "prefect.heavypad.yaml",
            "--all",
            cwd=source,
            env=env,
        )
        receipt.with_suffix(".tmp").write_text(sha + "\n")
        receipt.with_suffix(".tmp").replace(receipt)
        print("Registered green main revision on HeavyPad:", sha)


if __name__ == "__main__":
    main()
