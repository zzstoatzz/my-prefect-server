from __future__ import annotations

import subprocess

from mps.tangled import build_patch


def _git_repo(tmp_path) -> tuple[str, str]:
    cwd = str(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=cwd, check=True)
    (tmp_path / "pin.txt").write_text("v1\n")
    subprocess.run(["git", "add", "-A"], cwd=cwd, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"],
        cwd=cwd,
        check=True,
    )
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()
    return cwd, base


def test_build_patch_renders_the_change(tmp_path) -> None:
    cwd, base = _git_repo(tmp_path)
    (tmp_path / "pin.txt").write_text("v2\n")
    patch = build_patch(cwd, base, "deps: lib v2", "dep-bump")
    assert "deps: lib v2" in patch
    assert "-v1" in patch and "+v2" in patch
    assert "From:" in patch  # format-patch shape, what create_pull uploads


def test_build_patch_empty_when_nothing_changed(tmp_path) -> None:
    cwd, base = _git_repo(tmp_path)
    assert build_patch(cwd, base, "deps: lib v2", "dep-bump") == ""
