"""Remove idle git worktrees whose work already landed, and report the rest.

Agents (Codex, Claude Code, workflows) create worktrees freely and rarely
remove them. A worktree is removed only when all of these hold:

- it has no tracked changes and no untracked files outside .gitignore
- its ignored files are all regenerable (build output, dependency trees)
- its HEAD is reachable from a remote-tracking ref, or every commit it adds
  is already on the default branch (rebased or squash-merged)
- its HEAD and index have not moved for `min_idle_days`
- no process has its working directory inside it
- it is not locked and is not a repository's primary checkout

Removing a worktree never deletes its branch. Branches are deleted only when
the default branch already contains their tip. Everything else is reported.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

REGENERABLE = {
    ".DS_Store",
    ".astro",
    ".expo",
    ".loq_cache",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".svelte-kit",
    ".turbo",
    ".venv",
    ".wrangler",
    ".zig-cache",
    "__pycache__",
    "android",
    "build",
    "dist",
    "ios",
    "node_modules",
    "target",
    "zig-out",
    "zig-pkg",
}
REGENERABLE_SUFFIXES = (".egg-info", ".pyc", ".so", ".tsbuildinfo")
SKIP_DIRS = REGENERABLE | {".git", "Library", ".Trash"}
DAY = 86400


@dataclass
class Worktree:
    path: str
    repo: str
    branch: str | None
    head: str
    verdict: str = ""
    reason: str = ""
    details: list[str] = field(default_factory=list)


def git(cwd: str | Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        errors="replace",
        check=check,
    )


def find_repos(roots: list[Path], max_depth: int = 5) -> list[Path]:
    repos: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        base = len(root.parts)
        for dirpath, dirnames, _ in os.walk(root):
            here = Path(dirpath)
            if (here / ".git").is_dir():
                repos.append(here)
            if len(here.parts) - base >= max_depth:
                dirnames.clear()
                continue
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
    return sorted(set(repos))


def list_worktrees(repo: Path) -> list[dict[str, str]]:
    out = git(repo, "worktree", "list", "--porcelain").stdout
    entries: list[dict[str, str]] = []
    for block in out.strip().split("\n\n"):
        entry: dict[str, str] = {}
        for line in block.splitlines():
            key, _, value = line.partition(" ")
            entry[key] = value
        if "worktree" in entry:
            entries.append(entry)
    return entries[1:]


def process_cwds() -> list[str]:
    out = subprocess.run(
        ["lsof", "-d", "cwd", "-Fn"], check=False, capture_output=True, text=True
    ).stdout
    return [line[1:] for line in out.splitlines() if line.startswith("n/")]


def admin_dir(path: str) -> Path | None:
    marker = Path(path) / ".git"
    if not marker.is_file():
        return None
    return Path(marker.read_text().removeprefix("gitdir:").strip())


def idle_days(path: str, now: float) -> float:
    admin = admin_dir(path)
    stamps = []
    if admin:
        for name in ("index", "HEAD", "logs/HEAD"):
            with contextlib.suppress(OSError):
                stamps.append((admin / name).stat().st_mtime)
    return (now - max(stamps)) / DAY if stamps else 0.0


def default_ref(path: str) -> str | None:
    head = git(path, "symbolic-ref", "-q", "refs/remotes/origin/HEAD").stdout.strip()
    if head:
        return head
    for ref in ("refs/remotes/origin/main", "refs/remotes/origin/master"):
        if git(path, "rev-parse", "-q", "--verify", ref).returncode == 0:
            return ref
    return None


def is_regenerable(entry: str) -> bool:
    parts = entry.rstrip("/").split("/")
    if any(part in REGENERABLE for part in parts) or "/.husky/_" in f"/{entry}":
        return True
    return parts[-1].endswith(REGENERABLE_SUFFIXES)


def landed(wt: Worktree) -> tuple[bool, str]:
    if git(wt.path, "rev-list", "--count", "HEAD", "--not", "--remotes").stdout.strip() == "0":
        return True, "HEAD is on a remote-tracking ref"
    ref = default_ref(wt.path)
    if not ref:
        return False, "no remote default branch to compare against"
    cherry = git(wt.path, "cherry", ref, "HEAD").stdout.splitlines()
    if cherry and all(line.startswith("-") for line in cherry):
        return True, f"every commit has an equivalent on {ref}"
    merged = git(wt.path, "merge-tree", "--write-tree", ref, "HEAD")
    tree = git(wt.path, "rev-parse", f"{ref}^{{tree}}").stdout.strip()
    if merged.returncode == 0 and merged.stdout.split("\n", 1)[0] == tree:
        return True, f"merging HEAD into {ref} changes nothing"
    unique = [line[2:] for line in cherry if line.startswith("+")]
    wt.details = [git(wt.path, "log", "-1", "--format=%h %s", c).stdout.strip() for c in unique[:5]]
    return False, f"{len(unique)} commit(s) not on {ref}"


def classify(
    wt: Worktree, entry: dict[str, str], cwds: list[str], now: float, min_idle_days: float
) -> None:
    if "locked" in entry:
        wt.verdict, wt.reason = "keep", "locked"
        return
    if any(c == wt.path or c.startswith(wt.path + "/") for c in cwds):
        wt.verdict, wt.reason = "keep", "a process is running inside it"
        return
    idle = idle_days(wt.path, now)
    if idle < min_idle_days:
        wt.verdict, wt.reason = "keep", f"active {idle:.1f} days ago"
        return
    status = git(wt.path, "status", "--porcelain=v1", "--ignored=matching", "-unormal")
    if status.returncode != 0:
        wt.verdict, wt.reason = "report", "git status failed"
        return
    lines = status.stdout.splitlines()
    changes = [line for line in lines if not line.startswith("!!")]
    if changes:
        wt.verdict, wt.reason, wt.details = (
            "report",
            f"{len(changes)} uncommitted change(s)",
            changes[:8],
        )
        return
    kept = [line[3:] for line in lines if line.startswith("!!") and not is_regenerable(line[3:])]
    if kept:
        wt.verdict, wt.reason, wt.details = (
            "report",
            "ignored files that are not build output",
            kept[:8],
        )
        return
    ok, why = landed(wt)
    wt.verdict, wt.reason = ("remove" if ok else "report"), why


def remove(wt: Worktree) -> None:
    result = git(wt.repo, "worktree", "remove", wt.path)
    if result.returncode != 0:
        wt.verdict, wt.reason = "report", f"removal failed: {result.stderr.strip()[:200]}"
        return
    if wt.branch:
        ref = default_ref(wt.repo)
        branch = f"refs/heads/{wt.branch}"
        if ref and git(wt.repo, "merge-base", "--is-ancestor", branch, ref).returncode == 0:
            git(wt.repo, "branch", "-D", wt.branch)
            wt.details.append(f"deleted branch {wt.branch} at {wt.head[:12]}")
    parent = Path(wt.path).parent
    while parent != parent.parent and parent.is_dir() and not any(parent.iterdir()):
        if not str(parent).startswith(str(Path.home() / ".codex" / "worktrees")):
            break
        parent.rmdir()
        parent = parent.parent


def dead_derived_data(root: Path, now: float, min_idle_days: float) -> list[Path]:
    dead = []
    if not root.is_dir():
        return dead
    for child in root.iterdir():
        info = child / "info.plist"
        if not info.is_file():
            continue
        try:
            workspace = plistlib.loads(info.read_bytes()).get("WorkspacePath")
        except Exception:
            continue
        if (
            workspace
            and not Path(workspace).exists()
            and now - child.stat().st_mtime > min_idle_days * DAY
        ):
            dead.append(child)
    return dead


def run(
    roots: list[Path], apply: bool, fetch: bool, min_idle_days: float, derived_data: Path
) -> dict:
    now = time.time()
    cwds = process_cwds()
    results: list[Worktree] = []
    for repo in find_repos(roots):
        git(repo, "worktree", "prune")
        entries = list_worktrees(repo)
        if not entries:
            continue
        if fetch and git(repo, "remote").stdout.strip():
            with contextlib.suppress(subprocess.TimeoutExpired):
                subprocess.run(
                    ["git", "-C", str(repo), "fetch", "--quiet", "--prune", "origin"],
                    check=False,
                    capture_output=True,
                    timeout=120,
                )
        for entry in entries:
            wt = Worktree(
                path=entry["worktree"],
                repo=str(repo),
                branch=entry.get("branch", "").removeprefix("refs/heads/") or None,
                head=entry.get("HEAD", ""),
            )
            classify(wt, entry, cwds, now, min_idle_days)
            if apply and wt.verdict == "remove":
                remove(wt)
            results.append(wt)
    dead = dead_derived_data(derived_data, now, min_idle_days)
    if apply:
        for path in dead:
            shutil.rmtree(path, ignore_errors=True)
    counts: dict[str, int] = {}
    for wt in results:
        counts[wt.verdict] = counts.get(wt.verdict, 0) + 1
    return {
        "applied": apply,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "counts": counts,
        "derived_data": [str(p) for p in dead],
        "worktrees": [asdict(wt) for wt in results],
    }


def main(argv: list[str] | None = None) -> int:
    home = Path.home()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="remove; without it, only report")
    parser.add_argument("--no-fetch", action="store_true", help="skip fetching origin first")
    parser.add_argument("--min-idle-days", type=float, default=3.0)
    parser.add_argument(
        "--report",
        type=Path,
        default=home / "Library/Application Support/mac-worktree-reaper/last.json",
    )
    parser.add_argument(
        "--root",
        type=Path,
        action="append",
        help="directory to search for repositories (repeatable)",
    )
    parser.add_argument(
        "--derived-data", type=Path, default=home / "Library/Developer/Xcode/DerivedData"
    )
    args = parser.parse_args(argv)
    roots = args.root or [
        home / "tangled.org",
        home / "github.com",
        home / "Documents/Codex",
        home / ".codex/worktrees",
        home / ".cache/checkouts",
    ]
    report = run(roots, args.apply, not args.no_fetch, args.min_idle_days, args.derived_data)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=1))
    verb = "removed" if args.apply else "would remove"
    print(
        f"{verb} {report['counts'].get('remove', 0)} worktree(s) and {len(report['derived_data'])} "
        f"DerivedData folder(s); {report['counts'].get('report', 0)} need a look; "
        f"{report['counts'].get('keep', 0)} active. Report: {args.report}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
