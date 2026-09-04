"""diagnostic flow — liveness canary plus host resource telemetry.

Originally just printed system info to prove the worker pulls code and
executes. Still does that, so it keeps working as a canary.

It now also samples host resources, because the home box (heavypad) is
load-bearing for spindle CI, the process worker, and several containers,
and nothing was recording whether it trends toward trouble.

Everything here is stdlib and read-only. No new dependency, because every
dep is installed from git on each flow run — the exact cost this flow
exists to measure.

Two metrics deserve a note, since both look alarming and are not:

  - The worker cgroup's memory.current reads ~9G while its processes hold
    under 1G. The rest is reclaimable page cache and dentry/inode slab,
    charged here only because the worker's children touched those files
    first. Read `anon` for real usage; `file` and `slab` are the kernel's.
  - memory.events `high` climbs into the hundreds of thousands. That counts
    cache trimming against MemoryHigh, not starvation. `oom_kill` is the
    number that matters.

It also attributes CPU to processes and goes `Completed(name="Degraded")`
plus a `diagnostics.sustained-burn` event when something outside the
prefect workload has held a core for hours — a forgotten simulator once ran
two days at 100% before anyone noticed, because host graphs can say "hot"
but not "who".

`scan_cache_size` defaults to False on purpose: the uv cache is tens of GB
and walking it takes minutes while evicting the dentry cache — a probe that
degrades the thing it measures. Enable it on an infrequent schedule only.
"""

import datetime
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

from prefect import flow, get_run_logger
from prefect.artifacts import create_markdown_artifact
from prefect.events import emit_event
from prefect.states import Completed

UV_CACHE_DIR = Path(os.environ.get("UV_CACHE_DIR", "~/.cache/uv")).expanduser()

# one readdir each — cheap regardless of how much they hold
CACHE_SUBDIRS = ("archive-v0", "environments-v2", "git-v0", "built-wheels-v3")


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text()
    except OSError:
        return None


def _proc_meminfo() -> dict[str, int]:
    """MemTotal/MemAvailable/etc in bytes."""
    raw = _read("/proc/meminfo")
    if not raw:
        return {}
    out: dict[str, int] = {}
    for line in raw.splitlines():
        key, _, rest = line.partition(":")
        fields = rest.split()
        if fields and fields[0].isdigit():
            out[key] = int(fields[0]) * 1024
    return out


def _pressure() -> dict[str, float]:
    """PSI 'some' avg60 per resource. Absent on non-PSI kernels."""
    out: dict[str, float] = {}
    for resource in ("cpu", "memory", "io"):
        raw = _read(f"/proc/pressure/{resource}")
        if not raw:
            continue
        for line in raw.splitlines():
            if not line.startswith("some "):
                continue
            for field in line.split():
                name, _, value = field.partition("=")
                if name == "avg60":
                    out[resource] = float(value)
    return out


def _own_cgroup_path() -> Path | None:
    """Resolve our own cgroup v2 dir, so this works under the process
    worker and under k8s without hardcoding either layout."""
    raw = _read("/proc/self/cgroup")
    if not raw:
        return None
    for line in raw.splitlines():
        # cgroup v2 lines look like "0::/system.slice/some.service"
        if line.startswith("0::"):
            path = Path("/sys/fs/cgroup") / line[3:].strip().lstrip("/")
            return path if path.is_dir() else None
    return None


def _cgroup_memory() -> dict[str, Any]:
    cg = _own_cgroup_path()
    if cg is None:
        return {}
    out: dict[str, Any] = {"path": str(cg)}

    current = _read(str(cg / "memory.current"))
    if current:
        out["current"] = int(current.strip())

    stat = _read(str(cg / "memory.stat"))
    if stat:
        fields = {
            parts[0]: int(parts[1])
            for line in stat.splitlines()
            if len(parts := line.split()) == 2 and parts[1].lstrip("-").isdigit()
        }
        for key in ("anon", "file", "slab", "slab_reclaimable"):
            if key in fields:
                out[key] = fields[key]

    events = _read(str(cg / "memory.events"))
    if events:
        for line in events.splitlines():
            name, _, value = line.partition(" ")
            if name in ("high", "oom", "oom_kill"):
                out[f"events_{name}"] = int(value)
    return out


def _flow_runs_in_flight() -> int | None:
    try:
        result = subprocess.run(
            ["pgrep", "-fc", "flow-run execute"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    # pgrep exits 1 with no matches, which is a count of zero, not an error
    return int(result.stdout.strip() or 0) if result.returncode in (0, 1) else None


def _process_samples() -> list[dict[str, Any]]:
    """One `ps` snapshot with *cumulative* cpu seconds per process.

    cputimes/etimes gives a duty cycle from a single sample, so "has been
    burning a core for hours" needs no state between runs. procps-only
    columns; on a platform without them (macOS) this returns [].
    """
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,etimes=,cputimes=,pmem=,args=", "--sort=-cputimes"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return parse_ps(result.stdout)


def parse_ps(stdout: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        fields = line.split(None, 4)
        if len(fields) < 5 or not fields[0].isdigit():
            continue
        pid, etimes, cputimes, pmem, args = fields
        out.append(
            {
                "pid": int(pid),
                "age_s": int(etimes),
                "cpu_s": int(cputimes),
                "mem_pct": float(pmem),
                "args": args,
            }
        )
    return out


# our own workload, and kernel threads — never flagged. flow subprocesses
# (dbt etc.) are children of `flow-run execute` with their own args, but the
# age floor below outlives any legitimate flow run's timeout.
EXPECTED_SUBSTRINGS = ("prefect worker", "flow-run execute")

BURNER_MIN_AGE_S = 2 * 3600
BURNER_MIN_CPU_S = 3600
BURNER_MIN_DUTY = 0.5


def sustained_burners(procs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Processes that have held a core for hours and are still holding it.

    The bar is deliberately high — this exists to catch a forgotten load
    generator or runaway daemon, not to page about a busy afternoon.
    """
    out = []
    for p in procs:
        if p["args"].startswith("["):
            continue
        if any(s in p["args"] for s in EXPECTED_SUBSTRINGS):
            continue
        if p["age_s"] < BURNER_MIN_AGE_S or p["cpu_s"] < BURNER_MIN_CPU_S:
            continue
        if p["cpu_s"] / p["age_s"] < BURNER_MIN_DUTY:
            continue
        out.append(p)
    return sorted(out, key=lambda p: p["cpu_s"], reverse=True)


def _cache_entry_counts() -> dict[str, int]:
    """Entry counts per cache subdir — one readdir each, no recursion.

    archive-v0 is the interesting one: it holds unpacked wheels, so it grows
    a fresh entry every time --refresh-package re-resolves the package.
    """
    out: dict[str, int] = {}
    for name in CACHE_SUBDIRS:
        try:
            with os.scandir(UV_CACHE_DIR / name) as entries:
                out[name] = sum(1 for _ in entries)
        except OSError:
            continue
    return out


def _cache_size_bytes(timeout_seconds: int) -> int | None:
    """Total uv cache size. Expensive — see the module docstring."""
    try:
        result = subprocess.run(
            ["du", "-sxb", str(UV_CACHE_DIR)],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return int(result.stdout.split()[0])


def _gib(value: int | None) -> str:
    return "n/a" if value is None else f"{value / 1024**3:.2f} GiB"


@flow(name="diagnostics", log_prints=True, timeout_seconds=300)
def diagnostics(scan_cache_size: bool = False, cache_scan_timeout: int = 120):
    logger = get_run_logger()
    now = datetime.datetime.now(datetime.UTC)

    print(f"time:     {now.isoformat()}")
    print(f"hostname: {platform.node()}")
    print(f"python:   {platform.python_version()}")
    print(f"platform: {platform.platform()}")
    print(f"pid:      {os.getpid()}")
    print(f"cwd:      {os.getcwd()}")

    loadavg = (_read("/proc/loadavg") or "").split()
    cpus = os.cpu_count() or 0
    mem = _proc_meminfo()
    pressure = _pressure()
    cgroup = _cgroup_memory()
    disk = shutil.disk_usage("/")
    in_flight = _flow_runs_in_flight()
    procs = _process_samples()
    burners = sustained_burners(procs)
    cache_counts = _cache_entry_counts()
    cache_size = _cache_size_bytes(cache_scan_timeout) if scan_cache_size else None

    load1 = float(loadavg[0]) if loadavg else None
    if load1 is not None and cpus:
        print(f"load:     {load1} over {cpus} cpus ({load1 / cpus:.0%})")
    print(f"mem avail: {_gib(mem.get('MemAvailable'))} of {_gib(mem.get('MemTotal'))}")
    print(f"disk free: {_gib(disk.free)} of {_gib(disk.total)}")
    print(f"in flight: {in_flight}")
    if cgroup:
        print(f"cgroup:   current={_gib(cgroup.get('current'))} anon={_gib(cgroup.get('anon'))}")
    if cache_counts:
        print(f"uv cache entries: {cache_counts}")
    if scan_cache_size:
        print(f"uv cache size: {_gib(cache_size)}")
        if cache_size is None:
            logger.warning(
                "uv cache size scan exceeded %ss — cache large or disk busy",
                cache_scan_timeout,
            )

    if oom_kills := cgroup.get("events_oom_kill", 0):
        logger.warning("cgroup has %s oom kill(s) — real memory pressure", oom_kills)

    rows = [
        ("load (1m)", f"{load1} over {cpus} cpus" if load1 is not None else "n/a"),
        ("mem available", _gib(mem.get("MemAvailable"))),
        ("mem total", _gib(mem.get("MemTotal"))),
        ("swap used", _gib(mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)) if mem else "n/a"),
        ("psi cpu avg60", f"{pressure.get('cpu', 0):.2f}%" if pressure else "n/a"),
        ("psi memory avg60", f"{pressure.get('memory', 0):.2f}%" if pressure else "n/a"),
        ("psi io avg60", f"{pressure.get('io', 0):.2f}%" if pressure else "n/a"),
        ("disk free", f"{_gib(disk.free)} of {_gib(disk.total)}"),
        ("flow runs in flight", str(in_flight)),
        ("cgroup memory.current", _gib(cgroup.get("current"))),
        ("cgroup anon (real)", _gib(cgroup.get("anon"))),
        ("cgroup file (cache)", _gib(cgroup.get("file"))),
        ("cgroup slab", _gib(cgroup.get("slab"))),
        ("cgroup high events", str(cgroup.get("events_high", "n/a"))),
        ("cgroup oom kills", str(cgroup.get("events_oom_kill", "n/a"))),
        ("uv cache size", _gib(cache_size) if scan_cache_size else "not scanned"),
    ]
    rows.extend((f"uv cache/{name}", f"{count} entries") for name, count in cache_counts.items())
    rows.extend(
        (
            f"top cpu pid {p['pid']}",
            f"{p['cpu_s'] / 60:.0f} cpu-min over {p['age_s'] / 60:.0f} min — {p['args'][:80]}",
        )
        for p in procs[:5]
    )

    create_markdown_artifact(
        key="host-diagnostics",
        markdown="\n".join(
            [
                f"# host diagnostics — {platform.node()}",
                f"sampled {now.isoformat()}",
                "",
                "| metric | value |",
                "| --- | --- |",
                *(f"| {label} | {value} |" for label, value in rows),
                "",
                "`cgroup memory.current` counts reclaimable page cache and slab, "
                "not just process memory — read `cgroup anon` for real usage.",
            ]
        ),
        description="host resource sample from the diagnostics canary",
    )

    if burners:
        described = [
            f"pid {p['pid']} ({p['args'][:60]}): {p['cpu_s'] / 3600:.1f} cpu-h "
            f"over {p['age_s'] / 3600:.1f} h"
            for p in burners
        ]
        for line in described:
            logger.warning("sustained cpu burn: %s", line)
        emit_event(
            event="diagnostics.sustained-burn",
            resource={
                "prefect.resource.id": f"diagnostics.host.{platform.node()}",
                "prefect.resource.name": platform.node(),
            },
            payload={"burners": burners},
        )
        return Completed(name="Degraded", message="; ".join(described))

    return {
        "hostname": platform.node(),
        "load1": load1,
        "cpus": cpus,
        "mem_available": mem.get("MemAvailable"),
        "disk_free": disk.free,
        "in_flight": in_flight,
        "cgroup": cgroup,
        "uv_cache_counts": cache_counts,
        "uv_cache_size": cache_size,
    }


if __name__ == "__main__":
    diagnostics()
