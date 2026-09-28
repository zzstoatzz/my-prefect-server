import asyncio
import json
import os
import shlex
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest
from mps.deployment_steps import cached_checkout
from prefect.deployments.steps.core import run_step


@pytest.fixture
def source(tmp_path):
    repo = tmp_path / "remote"
    repo.mkdir()

    def git(*args):
        return (
            subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.PIPE)
            .decode()
            .strip()
        )

    git("init", "--quiet")
    git("config", "user.name", "Cache test")
    git("config", "user.email", "cache@example.invalid")
    commits = []
    for value in ("one", "two", "three"):
        (repo / "payload.txt").write_text(value)
        git("add", "payload.txt")
        git("commit", "--quiet", "-m", value)
        commits.append(git("rev-parse", "HEAD"))
    return repo, commits


def invoke(root, repository, commit, name, **kwargs):
    workspace = Path(root) / name
    workspace.mkdir()
    previous = Path.cwd()
    old_pin = os.environ.get("MPS_PIN")
    try:
        os.chdir(workspace)
        os.environ["MPS_PIN"] = "@" + commit
        return cached_checkout(
            repository=str(repository), cache_root=str(Path(root) / "cache"), **kwargs
        )
    finally:
        os.chdir(previous)
        if old_pin is None:
            os.environ.pop("MPS_PIN", None)
        else:
            os.environ["MPS_PIN"] = old_pin


def test_prefect_dispatch_and_offline_isolated_hit(tmp_path, source, monkeypatch):
    repo, commits = source
    workspace = tmp_path / "run"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("MPS_PIN", "@" + commits[0])
    cold = asyncio.run(
        run_step(
            {
                "mps.deployment_steps.cached_checkout": {
                    "repository": str(repo),
                    "cache_root": str(tmp_path / "cache"),
                }
            }
        )
    )
    assert cold["commit"] == commits[0]
    assert cold["cache_hit"] is False
    assert (Path(cold["directory"]) / "payload.txt").read_text() == "one"
    repo.rename(tmp_path / "offline")
    warm = invoke(tmp_path, repo, commits[0], "warm")
    assert warm["cache_hit"] is True
    (Path(cold["directory"]) / "payload.txt").write_text("changed by first run")
    assert (Path(warm["directory"]) / "payload.txt").read_text() == "one"
    with pytest.raises(RuntimeError):
        invoke(tmp_path, repo, commits[1], "missing")
    assert not (tmp_path / "missing/my-prefect-server").exists()
    assert not list((tmp_path / "cache").glob("*/staging-*"))


def test_parallel_misses_fetch_once_and_pruning_preserves_runs(tmp_path, source):
    repo, commits = source
    with ProcessPoolExecutor(max_workers=4) as executor:
        runs = [
            executor.submit(invoke, tmp_path, repo, commits[0], f"parallel{i}", keep=2)
            for i in range(4)
        ]
        results = [run.result() for run in runs]
    assert sum(not result["cache_hit"] for result in results) == 1
    for i, commit in enumerate(commits[1:]):
        invoke(tmp_path, repo, commit, f"later{i}", keep=2)
    assert len(list((tmp_path / "cache").glob("*/*/source.bundle"))) == 2
    assert not list((tmp_path / "cache").glob(f"*/{commits[0]}"))
    assert (Path(results[0]["directory"]) / "payload.txt").read_text() == "one"
    rollback = invoke(tmp_path, repo, commits[0], "rollback", keep=2)
    assert rollback["cache_hit"] is False
    assert (Path(rollback["directory"]) / "payload.txt").read_text() == "one"


def test_corrupt_cache_is_refetched_and_byte_budget_is_bounded(tmp_path, source):
    repo, commits = source
    invoke(tmp_path, repo, commits[0], "first")
    archive = next((tmp_path / "cache").glob("*/*/source.bundle"))
    archive.write_bytes(b"broken")
    repaired = invoke(tmp_path, repo, commits[0], "repaired")
    assert repaired["cache_hit"] is False
    assert (Path(repaired["directory"]) / "payload.txt").read_text() == "one"
    evicted = invoke(tmp_path, repo, commits[1], "tiny-budget", max_bytes=1)
    assert not list((tmp_path / "cache").glob("*/*/source.bundle"))
    assert (Path(evicted["directory"]) / "payload.txt").read_text() == "two"


def test_fallback_still_requires_exact_commit(tmp_path, source):
    repo, commits = source
    result = invoke(
        tmp_path, tmp_path / "unavailable", commits[0], "fallback", fallback_repository=str(repo)
    )
    assert result["commit"] == commits[0]
    with pytest.raises(RuntimeError):
        invoke(tmp_path, repo, "0" * 40, "nonexistent")
    assert not (tmp_path / "nonexistent/my-prefect-server").exists()


@pytest.mark.parametrize("pin", ["", "main", "@main", "@abc", "a" * 40])
def test_moving_or_invalid_pin_is_rejected(tmp_path, monkeypatch, pin):
    monkeypatch.setenv("MPS_PIN", pin)
    with pytest.raises(ValueError, match="MPS_PIN"):
        cached_checkout(repository="unavailable", cache_root=str(tmp_path / "cache"))
    assert not (tmp_path / "cache").exists()


def test_existing_destination_is_untouched(tmp_path, source, monkeypatch):
    repo, commits = source
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MPS_PIN", "@" + commits[0])
    target = tmp_path / "my-prefect-server"
    target.mkdir()
    (target / "valuable.txt").write_text("keep")
    with pytest.raises(ValueError, match="destination"):
        cached_checkout(repository=str(repo), cache_root=str(tmp_path / "cache"))
    assert (target / "valuable.txt").read_text() == "keep"


def test_only_diagnostics_overrides_source_pull():
    import yaml

    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((root / "prefect.yaml").read_text())
    using_cache = [
        d
        for d in config["deployments"]
        if "mps.deployment_steps.cached_checkout" in json.dumps(d.get("pull"))
    ]
    assert [d["name"] for d in using_cache] == ["diagnostics"]
    assert using_cache[0]["schedules"] == [{"cron": "37 * * * *", "active": True}]


def test_fetch_timeout_stops_transport_children(tmp_path, monkeypatch):
    started = tmp_path / "transport-started"
    survived = tmp_path / "transport-survived"
    child = (
        "import time; from pathlib import Path; time.sleep(2); Path("
        + repr(str(survived))
        + ").write_text('leaked')"
    )
    transport = tmp_path / "transport.py"
    transport.write_text(
        "import subprocess, sys, time\nfrom pathlib import Path\n"
        + "subprocess.Popen([sys.executable, '-c', "
        + repr(child)
        + "])\n"
        + "Path("
        + repr(str(started))
        + ").write_text('started')\n"
        + "time.sleep(30)\n"
    )
    monkeypatch.setenv(
        "GIT_SSH_COMMAND", shlex.quote(sys.executable) + " " + shlex.quote(str(transport))
    )
    monkeypatch.setenv("GIT_SSH_VARIANT", "ssh")
    with pytest.raises(subprocess.TimeoutExpired):
        invoke(tmp_path, "ssh://unused.invalid/repo", "a" * 40, "timed-out", timeout=0.5)
    assert started.exists()
    time.sleep(2.2)
    assert not survived.exists()
    assert not list((tmp_path / "cache").glob("*/staging-*"))


def test_checkout_preserves_git_history_tags_and_export_ignored_files(tmp_path, source):
    repo, commits = source

    def git(path, *args):
        return (
            subprocess.check_output(["git", "-C", str(path), *args], stderr=subprocess.PIPE)
            .decode()
            .strip()
        )

    git(repo, "tag", "-a", "v1", commits[0], "-m", "first release")
    (repo / ".gitattributes").write_text("payload.txt export-ignore\n")
    git(repo, "add", ".gitattributes")
    git(repo, "commit", "--quiet", "-m", "export attributes")
    commit = git(repo, "rev-parse", "HEAD")
    result = invoke(tmp_path, repo, commit, "checkout", max_bytes=1)
    checkout = Path(result["directory"])
    assert (checkout / "payload.txt").read_text() == "three"
    assert git(checkout, "rev-parse", "HEAD") == commit
    assert git(checkout, "describe") == git(repo, "describe")
    assert git(checkout, "rev-list", "--count", "HEAD") == "4"
    assert git(checkout, "remote", "get-url", "origin") == str(repo)
    assert not (checkout / ".git/objects/info/alternates").exists()
    assert not list((tmp_path / "cache").glob("*/*/source.bundle"))
    git(checkout, "fsck", "--full")
    git(checkout, "checkout", "--quiet", commits[0])
    assert (checkout / "payload.txt").read_text() == "one"
    assert git(checkout, "status", "--porcelain") == ""
