"""albedo-drift-audit — a cheap model audits heavypad against this repo.

Weekly, on the home-pool worker (heavypad). The flow snapshots the user units
heavypad runs (names, states, descriptions and unit-file paths; never unit
contents, which can carry credentials), clones this repo fresh, and runs
albedo with DeepSeek inside the bwrap sandbox in `deploy/albedo-sandbox/`.
The model writes `report.md`: units with no source under `deploy/`, units
the repo defines that are absent, and doc claims the snapshot contradicts.

The report is archived under the analytics dir, published as an artifact,
and handed to phi's `drift-audit` slot as material. A phi that does not
have that slot yet answers 404; the run then completes as Degraded, since
the audit itself did its job.
"""

import datetime
import os
import shutil
import subprocess
from pathlib import Path

import httpx
from prefect import flow, get_run_logger, task
from prefect.artifacts import acreate_markdown_artifact
from prefect.states import Completed

from flows.phi_trigger import phi_trigger

REPO = Path(__file__).resolve().parents[1]
SANDBOX_SCRIPT = REPO / "deploy" / "albedo-sandbox" / "sandbox.sh"
PROMPT = REPO / "deploy" / "albedo-sandbox" / "drift-audit.md"
SANDBOX = Path(os.environ.get("ALBEDO_SANDBOX", "~/albedo-sandbox")).expanduser()
WORK = SANDBOX / "work"
ARCHIVE = Path(
    os.environ.get("ALBEDO_AUDIT_ARCHIVE", "~/prefect-analytics/albedo-drift-audit")
).expanduser()
SOURCES = (
    "https://tangled.org/zzstoatzz.io/my-prefect-server.git",
    "https://github.com/zzstoatzz/my-prefect-server.git",
)


def _user_systemctl(*args: str) -> str:
    # the worker is a system unit running as stoat, so the user manager is
    # only reachable once its runtime dir and bus are named explicitly
    runtime = f"/run/user/{os.getuid()}"
    env = {
        **os.environ,
        "XDG_RUNTIME_DIR": runtime,
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus",
    }
    return subprocess.run(
        ["systemctl", "--user", *args],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@task
def snapshot_units() -> tuple[str, str]:
    """Unit names, states and descriptions, plus where each unit file lives."""
    listing = _user_systemctl(
        "list-units", "--all", "--type=service,timer", "--no-pager", "--plain", "--no-legend"
    )
    rows, names = [], []
    for line in listing.splitlines():
        fields = line.split(None, 4)
        if len(fields) < 4:
            continue
        name, load, active, sub = fields[:4]
        rows.append("\t".join([name, load, active, sub, fields[4] if len(fields) > 4 else ""]))
        names.append(name)
    paths = _user_systemctl("show", "-p", "Id", "-p", "FragmentPath", *names)
    path_rows, unit = [], ""
    for line in paths.splitlines():
        key, _, value = line.partition("=")
        if key == "Id":
            unit = value
        elif key == "FragmentPath":
            path_rows.append(f"{unit}\t{value}")
    return "\n".join(rows) + "\n", "\n".join(path_rows) + "\n"


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
def prepare_workspace(units: str, unit_paths: str) -> None:
    """A fresh work dir: a shallow clone, the snapshot, and the prompt."""
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    errors = []
    for source in SOURCES:
        result = subprocess.run(
            ["git", "clone", "--quiet", "--depth", "1", source, str(WORK / "my-prefect-server")],
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            break
        errors.append(f"{source}: {result.stderr.strip()}")
        shutil.rmtree(WORK / "my-prefect-server", ignore_errors=True)
    else:
        raise RuntimeError("clone failed: " + "; ".join(errors))
    (WORK / "units.tsv").write_text(units)
    (WORK / "unit-paths.tsv").write_text(unit_paths)
    (WORK / "PROMPT.md").write_text(PROMPT.read_text())


@task
def run_audit(timeout: str) -> str:
    """Run the prompt in the sandbox and return the report it wrote."""
    logger = get_run_logger()
    try:
        result = subprocess.run(
            [str(SANDBOX_SCRIPT), "albedo", "--prompt", PROMPT.read_text(), "--timeout", timeout],
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        subprocess.run(
            [str(SANDBOX_SCRIPT), "albedo", "daemon", "--stop"], check=False, capture_output=True
        )
    logger.info(
        "albedo exited %s: %s", result.returncode, result.stdout[-600:] + result.stderr[-600:]
    )
    report = WORK / "report.md"
    if result.returncode != 0 or not report.exists():
        raise RuntimeError(
            f"albedo exited {result.returncode} without a report: {result.stderr[-600:]}"
        )
    return report.read_text()


@task
def archive_report(report: str) -> Path:
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    path = ARCHIVE / f"{datetime.date.today().isoformat()}.md"
    path.write_text(report)
    return path


@flow(name="albedo-drift-audit", log_prints=True, timeout_seconds=1800)
async def albedo_drift_audit(timeout: str = "15m", notify_phi: bool = True):
    """Audit heavypad's user units against this repo and hand the report to phi."""
    units, unit_paths = snapshot_units()
    prepare_workspace(units, unit_paths)
    report = run_audit(timeout)
    path = archive_report(report)
    await acreate_markdown_artifact(
        key="albedo-drift-audit", markdown=report, description=str(path)
    )
    print(f"report: {path}")
    if not notify_phi:
        return {"report": str(path)}
    try:
        await phi_trigger(slot="drift-audit", material=report)
    except httpx.HTTPStatusError as error:
        if error.response.status_code != 404:
            raise
        return Completed(name="Degraded", message=f"phi has no drift-audit slot; report at {path}")
    return {"report": str(path)}


if __name__ == "__main__":
    import asyncio

    asyncio.run(albedo_drift_audit(notify_phi=False))
