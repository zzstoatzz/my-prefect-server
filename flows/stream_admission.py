"""Run stream's admission gate on bare metal (heavypad), not in a microVM.

Why this exists: stream's gate is 21 suites plus a native amd64 image build.
Running it inside spindle's microVM works but costs ~8.5 min of fixed setup per
run and ~2x on execution (2026-08-09: the lifecycle oracle took 2533s on the
stock 2-vCPU guest, 900s on 12 vCPU, ~420s on bare metal). home-pool is a
*process* pool whose worker runs directly on heavypad, so a flow run here is
the bare-metal job — with Prefect's logs, retries and history for free.

Trigger it from anywhere that can make a REST call, spindle included:

    POST {PREFECT_API}/deployments/{id}/create_flow_run
    {"parameters": {"sha": "<commit>"}}

The gate itself is unchanged: `scripts/admit run` still requires a clean tree,
still refuses to write a receipt if any suite fails, and `admit verify` still
rejects a receipt whose suites are not all "pass". This flow only chooses
*where* it runs and *how much* of it runs.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path

from prefect import flow, get_run_logger, task

REPO_URL = "https://knot1.tangled.sh/did:plc:mkqt76xvfgxuemlwlx6ruc3w/stream"
WORKTREE = Path(os.environ.get("STREAM_GATE_DIR", "/home/stoat/stream-gate"))
UPSTREAM = Path(os.environ.get("STREAM_UPSTREAM_REPO", "/home/stoat/jetstream"))
UPSTREAM_URL = "https://github.com/bluesky-social/jetstream"
SIMULATOR_PORT = 7777

# The process worker runs with a bare PATH (/home/stoat/.local/bin:/usr/local/
# bin:/usr/bin:/bin), so go and just — which live in the shared nix profile —
# are invisible to it and the gate dies looking for them. Set this once, for
# every subprocess, rather than per call site.
GATE_PATH = os.environ.get(
    "STREAM_GATE_PATH",
    "/home/stoat/.local/bin:/nix/var/nix/profiles/default/bin:"
    "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
)


def _apply_path() -> None:
    """Prepend the toolchain PATH so child processes inherit it."""
    current = os.environ.get("PATH", "")
    if GATE_PATH not in current:
        os.environ["PATH"] = f"{GATE_PATH}:{current}" if current else GATE_PATH

# every suite admit knows about; `only` is expressed as a skip-list of the rest
# so the receipt still records each one explicitly (skipped is not omitted).
ALL_SUITES = [
    "lifecycle-oracle", "powerloss-oracle", "unit-debug", "unit-releasesafe",
    "differential-oracle", "listener-contract", "shutdown-contract",
    "logging-contract", "environment-contract", "dashboard-test",
    "process-metrics-contract", "http-metrics-contract", "archive-contract",
    "plan-config-contract", "cursor-lookback-contract",
    "compaction-config-contract", "retry-config-contract",
    "subscribe-config-contract", "subscribe-read-batch-contract",
    "status-contract", "seam-handoff-contract",
]


def _run(cmd: str, cwd: Path | None = None, env: dict | None = None, timeout: int = 3600):
    """Run a shell command, streaming nothing but returning everything."""
    return subprocess.run(
        cmd, shell=True, cwd=cwd, text=True, timeout=timeout,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env={**os.environ, **(env or {})},
    )


@task
def checkout(sha: str | None) -> str:
    """Clone or update the gate worktree and check out `sha` (default main).

    A separate worktree from any interactive clone: the gate demands a clean
    tree, and sharing one with a human guarantees a dirty-tree failure sooner
    or later.
    """
    log = get_run_logger()
    if not (WORKTREE / ".git").exists():
        WORKTREE.parent.mkdir(parents=True, exist_ok=True)
        r = _run(f"git clone {shlex.quote(REPO_URL)} {shlex.quote(str(WORKTREE))}")
        if r.returncode != 0:
            raise RuntimeError(f"clone failed:\n{r.stdout}")

    target = sha or "origin/main"
    for cmd in ("git fetch --tags --prune origin", f"git checkout --force {shlex.quote(target)}",
                "git reset --hard", "git clean -fdx -e receipts -e .zig-cache"):
        r = _run(cmd, cwd=WORKTREE)
        if r.returncode != 0:
            raise RuntimeError(f"`{cmd}` failed:\n{r.stdout}")

    resolved = _run("git rev-parse --short HEAD", cwd=WORKTREE).stdout.strip()
    log.info("gate worktree at %s (%s)", resolved, target)
    return resolved


@task
def ensure_upstream() -> str:
    """The oracles run against the pinned upstream simulator."""
    log = get_run_logger()
    pin_src = (WORKTREE / "docs/upstream-harness.md").read_text()
    pin = re.search(r"f29815c[0-9a-f]*", pin_src)
    if not pin:
        raise RuntimeError("could not find the upstream pin in docs/upstream-harness.md")
    if not (UPSTREAM / ".git").exists():
        r = _run(f"git clone {shlex.quote(UPSTREAM_URL)} {shlex.quote(str(UPSTREAM))}")
        if r.returncode != 0:
            raise RuntimeError(f"upstream clone failed:\n{r.stdout}")
    _run("git fetch --tags origin", cwd=UPSTREAM)
    r = _run(f"git checkout --force {pin.group(0)}", cwd=UPSTREAM)
    if r.returncode != 0:
        raise RuntimeError(f"upstream checkout failed:\n{r.stdout}")
    log.info("upstream pinned at %s", pin.group(0))
    return pin.group(0)


@task
def ensure_simulator() -> bool:
    """Start the pinned simulator if nothing is serving :7777.

    Left running between flow runs on purpose — it is the same standing world
    the interactive gate expects, and starting it costs ~2.5 min.
    """
    log = get_run_logger()
    probe = _run(f"curl -fsS --max-time 3 http://127.0.0.1:{SIMULATOR_PORT}/ >/dev/null", timeout=30)
    if probe.returncode == 0:
        log.info("simulator already serving :%d", SIMULATOR_PORT)
        return False
    log.info("starting the pinned simulator on :%d", SIMULATOR_PORT)
    subprocess.Popen(
        "nohup go run ./cmd/simulator serve --reset --accounts=100 --commits-per-sec=20"
        " >/tmp/stream-gate-sim.log 2>&1 &",
        shell=True, cwd=UPSTREAM, start_new_session=True, env=dict(os.environ),
    )
    # a cold start also downloads and builds the simulator's go modules, which
    # took longer than a 3-minute window on this box's first run
    for attempt in range(300):
        if _run(f"curl -fsS --max-time 2 http://127.0.0.1:{SIMULATOR_PORT}/ >/dev/null", timeout=15).returncode == 0:
            log.info("simulator up after %ds", attempt * 2)
            return True
        time.sleep(2)
    raise RuntimeError("simulator did not come up on :7777; see /tmp/stream-gate-sim.log")


@flow(name="stream-admission", log_prints=True)
def stream_admission(
    sha: str | None = None,
    only: list[str] | None = None,
    skip: list[str] | None = None,
    build_image: bool = True,
    timeout_s: int = 4 * 3600,
) -> dict:
    """Gate a stream commit on heavypad.

    sha:         commit to gate (default: origin/main).
    only:        run just these suites — everything else is recorded "skipped",
                 so the receipt cannot masquerade as a full run.
    skip:        suites to skip, if you would rather subtract than select.
    build_image: False runs the suites and builds nothing, so no receipt is
                 written. Use it to exercise this path cheaply; a receipt with
                 no image is exactly the thing admission exists to prevent.
    """
    log = get_run_logger()
    _apply_path()
    log.info("PATH: %s", os.environ["PATH"].split(":")[:4])

    resolved = checkout(sha)
    ensure_upstream()
    ensure_simulator()

    skips = set(skip or [])
    if only:
        unknown = set(only) - set(ALL_SUITES)
        if unknown:
            raise ValueError(f"unknown suites: {sorted(unknown)}")
        skips |= set(ALL_SUITES) - set(only)

    env = {"STREAM_ADMIT_SKIP": ",".join(sorted(skips))} if skips else {}
    if not build_image:
        env["STREAM_ADMIT_NO_BUILD"] = "1"
    log.info(
        "running the gate on %s (%d suites, %d skipped, build_image=%s)",
        resolved, len(ALL_SUITES) - len(skips), len(skips), build_image,
    )
    started = time.monotonic()
    result = _run("./scripts/admit run", cwd=WORKTREE, env=env, timeout=timeout_s)
    elapsed = round(time.monotonic() - started)

    for line in result.stdout.splitlines():
        print(line)

    receipt_path = WORKTREE / "receipts" / f"{resolved}.json"
    receipt = None
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())

    summary = {
        "sha": resolved,
        "ok": result.returncode == 0,
        "elapsed_s": elapsed,
        "skipped": sorted(skips),
        "receipt_written": receipt is not None,
        "suites": (receipt or {}).get("suites"),
        "image": (receipt or {}).get("image"),
    }
    log.info("gate finished in %ss: %s", elapsed, "PASS" if summary["ok"] else "FAIL")
    if not summary["ok"]:
        raise RuntimeError(f"admission failed after {elapsed}s (see logs above)")
    return summary
