import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_inherited_git_dir_leaves_the_caller_repository_alone(tmp_path):
    caller = tmp_path / "caller"
    subprocess.run(["git", "init", "--quiet", str(caller)], check=True)
    env = {**os.environ, "GIT_DIR": str(caller / ".git"), "GIT_WORK_TREE": str(caller)}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/test_deployment_steps.py::test_checkout_preserves_git_history_tags_and_export_ignored_files",
            "tests/test_agentic_skills.py::test_loads_exact_pinned_content_and_stage_specific_skills",
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    config = (caller / ".git" / "config").read_text()
    assert "bare = false" in config
    assert "[user]" not in config
    assert result.returncode == 0, result.stdout[-2000:]
