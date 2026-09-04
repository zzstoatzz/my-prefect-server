"""adopt an upstream release into its downstream repos, tests-first.

the local dependabot: for each downstream, re-pin the dependency with
`zig fetch --save`, run the repo's test suite, and only if green publish
the pin as a tangled PR. red suites produce a report and nothing else —
no PR, no dirty state anywhere, the throwaway clone is discarded.

same security shape as pi-pr, minus the agent: everything is a throwaway
clone with a from-scratch env, and only the flow holds the credential
that publishes the pull record. runs on home-pool (heavypad), where the
zig toolchain already lives for stream-admission.

deterministic on purpose. a version bump needs no LLM; if the tests fail,
the failure report is the handoff to a human or an agent, not this flow's
problem.
"""

import argparse
import subprocess
import tempfile
from typing import Any, Literal

from mps.blocks import secret_sync
from mps.pi import minimal_env
from mps.tangled import build_patch, create_pull
from prefect import flow

Repo = Literal["jetstream", "zlay", "stream", "zds"]

OWNER = "zat.dev"
CLONE_URL = "https://tangled.sh/{owner}/{repo}.git"
PUSH_URL = "git@tangled.org:{owner}/{repo}"
URL_TEMPLATE = "https://tangled.org/{owner}/{dep}/archive/{version}.tar.gz"


def _run(argv: list[str], cwd: str, env: dict[str, str]) -> tuple[bool, str]:
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, env=env, check=False)
    return result.returncode == 0, (result.stdout + result.stderr)[-4000:]


@flow(name="dep-bump", log_prints=True, timeout_seconds=3600)
def dep_bump(
    dep: str,
    version: str,
    repos: list[Repo],
    url_template: str = URL_TEMPLATE,
    dry_run: bool = False,
    push_to_main: bool = False,
) -> dict[str, Any]:
    """re-pin `dep` to `version` in each downstream; land the ones whose tests pass.

    green suites become a tangled PR by default; with `push_to_main` the pin
    commit is pushed straight to the repo's default branch instead. pushing a
    service repo deploys it — push-to-main is the deploy gate in this family
    of repos, which is exactly the point: an adopted upstream release ships.
    requires the worker's ssh key to be authorized on tangled.
    """
    url = url_template.format(owner=OWNER, dep=dep, version=version)
    env = minimal_env()
    handle = password = None
    if not dry_run and not push_to_main:
        # PRs are authored by gardener, the maintenance identity, not the operator
        handle = secret_sync("gardener-handle")
        password = secret_sync("gardener-password")

    results: dict[str, Any] = {}
    for repo in repos:
        cwd = tempfile.mkdtemp(prefix=f"dep-bump-{repo}-")
        print(f"{repo}: cloning into {cwd}")
        subprocess.run(
            ["git", "clone", "--depth", "1", CLONE_URL.format(owner=OWNER, repo=repo), cwd],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, check=True
        ).stdout.strip()

        fetched, fetch_out = _run(["zig", "fetch", f"--save={dep}", url], cwd, env)
        if not fetched:
            print(f"{repo}: fetch FAILED\n{fetch_out}")
            results[repo] = {"tested": False, "pull_uri": None, "error": "fetch failed"}
            continue

        print(f"{repo}: zig build test")
        tested, test_out = _run(["zig", "build", "test"], cwd, env)
        if not tested:
            print(f"{repo}: tests FAILED\n{test_out}")
            results[repo] = {"tested": False, "pull_uri": None, "error": "tests failed"}
            continue

        title = f"deps: {dep} {version}"
        patch = build_patch(cwd, base, title, "gardener", email="gardener@zat.dev")
        if not patch:
            print(f"{repo}: already on {version} — nothing to propose")
            results[repo] = {"tested": True, "pull_uri": None, "error": None}
            continue

        if dry_run:
            print(f"{repo}: ok (dry run, patch {len(patch)} bytes)")
            results[repo] = {"tested": True, "pull_uri": None, "patch_bytes": len(patch)}
            continue

        if push_to_main:
            # build_patch already committed the pin on the default branch
            pushed, push_out = _run(
                [
                    "git",
                    "-c",
                    "core.sshCommand=ssh -o StrictHostKeyChecking=accept-new",
                    "push",
                    PUSH_URL.format(owner=OWNER, repo=repo),
                    "HEAD",
                ],
                cwd,
                env,
            )
            if not pushed:
                print(f"{repo}: push FAILED\n{push_out}")
                results[repo] = {"tested": True, "pull_uri": None, "error": "push failed"}
                continue
            print(f"{repo}: pushed pin to main")
            results[repo] = {"tested": True, "pull_uri": None, "pushed": True, "error": None}
            continue

        body = f"automated pin bump: `{dep}` -> `{version}`. tests passed at `{base[:9]}`."
        if handle is None or password is None:
            raise RuntimeError("gardener credentials are required to open a pull")
        pull = create_pull(OWNER, repo, title, patch, body, handle, password)
        print(f"{repo}: pull created: {pull['uri']}")
        results[repo] = {"tested": True, "pull_uri": pull["uri"], "error": None}

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dep", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--repos", nargs="+", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--push-to-main", action="store_true")
    args = parser.parse_args()
    print(
        dep_bump(
            args.dep,
            args.version,
            args.repos,
            dry_run=args.dry_run,
            push_to_main=args.push_to_main,
        )
    )
