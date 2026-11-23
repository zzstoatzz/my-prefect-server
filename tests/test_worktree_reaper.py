import os
import subprocess
import time
from pathlib import Path

import pytest
from mps import worktree_reaper as reaper


def sh(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def age(worktree: Path, days: float) -> None:
    admin = reaper.admin_dir(str(worktree))
    assert admin is not None
    stamp = time.time() - days * reaper.DAY
    for name in ("index", "HEAD", "logs/HEAD"):
        path = admin / name
        if path.exists():
            os.utime(path, (stamp, stamp))


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@example.com")
    monkeypatch.setattr(reaper, "process_cwds", lambda: [])
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    repo = tmp_path / "roots" / "repo"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True, capture_output=True)
    (repo / ".gitignore").write_text("node_modules/\n.env\n")
    sh(repo, "add", ".")
    sh(repo, "commit", "-qm", "init")
    sh(repo, "push", "-q", "origin", "main")
    sh(repo, "remote", "set-head", "origin", "main")
    return tmp_path


def add(home: Path, name: str, *, branch: bool = True) -> Path:
    repo = home / "roots" / "repo"
    path = home / "wt" / name
    args = ["worktree", "add", "-q"]
    args += (
        ["-b", name, str(path), "origin/main"] if branch else ["--detach", str(path), "origin/main"]
    )
    sh(repo, *args)
    return path


def commit(path: Path, name: str) -> None:
    (path / name).write_text(name)
    sh(path, "add", name)
    sh(path, "commit", "-qm", name)


def verdicts(home: Path, apply: bool) -> dict[str, tuple[str, str]]:
    report = reaper.run([home / "roots"], apply, False, 3.0, home / "DerivedData")
    return {Path(w["path"]).name: (w["verdict"], w["reason"]) for w in report["worktrees"]}


def test_reaps_only_landed_idle_clean_worktrees(home: Path) -> None:
    repo = home / "roots" / "repo"
    landed = add(home, "landed")
    commit(landed, "a")
    sh(repo, "merge", "-q", "--ff-only", "landed")
    sh(repo, "push", "-q", "origin", "main")
    sh(repo, "fetch", "-q")

    squashed = add(home, "squashed")
    commit(squashed, "b")
    commit(squashed, "c")
    sh(repo, "merge", "-q", "--squash", "squashed")
    sh(repo, "commit", "-qm", "squash")
    sh(repo, "push", "-q", "origin", "main")
    sh(repo, "fetch", "-q")

    unmerged = add(home, "unmerged")
    commit(unmerged, "d")
    dirty = add(home, "dirty")
    (dirty / "notes.txt").write_text("wip")
    secret = add(home, "secret")
    (secret / ".env").write_text("TOKEN=x")
    deps = add(home, "deps")
    (deps / "node_modules").mkdir()
    (deps / "node_modules" / "x.js").write_text("")
    detached = add(home, "detached", branch=False)
    commit(detached, "e")
    fresh = add(home, "fresh")
    for path in (landed, squashed, unmerged, dirty, secret, deps, detached):
        age(path, 10)

    seen = verdicts(home, apply=True)

    assert seen["landed"][0] == "remove"
    assert seen["squashed"][0] == "remove"
    assert seen["deps"][0] == "remove"
    assert seen["unmerged"] == ("report", "1 commit(s) not on refs/remotes/origin/main")
    assert seen["dirty"] == ("report", "1 uncommitted change(s)")
    assert seen["secret"] == ("report", "ignored files that are not build output")
    assert seen["detached"] == ("report", "1 commit(s) not on refs/remotes/origin/main")
    assert seen["fresh"][0] == "keep" and seen["fresh"][1].startswith("active")
    for gone in (landed, squashed, deps):
        assert not gone.exists()
    for kept in (unmerged, dirty, secret, detached, fresh):
        assert kept.exists()
    branches = sh(repo, "branch", "--format=%(refname:short)").split()
    assert "landed" not in branches
    assert "deps" not in branches
    assert "squashed" in branches
    assert "unmerged" in branches


def test_dry_run_changes_nothing(home: Path) -> None:
    path = add(home, "idle")
    age(path, 10)
    assert verdicts(home, apply=False)["idle"][0] == "remove"
    assert path.exists()
    assert "idle" in sh(home / "roots" / "repo", "branch", "--format=%(refname:short)").split()


def test_keeps_worktrees_with_a_process_inside(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = add(home, "busy")
    age(path, 10)
    monkeypatch.setattr(reaper, "process_cwds", lambda: [str(path / "sub")])
    assert verdicts(home, apply=True)["busy"] == ("keep", "a process is running inside it")
    assert path.exists()


def test_derived_data_for_missing_workspaces(tmp_path: Path) -> None:
    import plistlib

    root = tmp_path / "DerivedData"
    for name, workspace in (("gone", tmp_path / "missing.xcworkspace"), ("live", tmp_path)):
        (root / name).mkdir(parents=True)
        (root / name / "info.plist").write_bytes(plistlib.dumps({"WorkspacePath": str(workspace)}))
        stamp = time.time() - 10 * reaper.DAY
        os.utime(root / name, (stamp, stamp))
    assert reaper.dead_derived_data(root, time.time(), 3.0) == [root / "gone"]
