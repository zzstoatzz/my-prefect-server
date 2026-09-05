"""Exercise the declared pull step against a local Git repository."""

import subprocess
from pathlib import Path

import yaml
from prefect.deployments.steps import run_shell_script


async def test_presence_pull_uses_requested_revision(tmp_path):
    source = tmp_path / "source"
    source.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(source), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "first",
    )
    revision = git("rev-parse", "HEAD")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "second",
    )
    config = yaml.safe_load((Path(__file__).parents[1] / "deploy/presence.yaml").read_text())
    step = config["pull"][0]["prefect.deployments.steps.run_shell_script"]
    await run_shell_script(
        **step,
        directory=str(tmp_path),
        env={"PRESENCE_SOURCE_URL": str(source), "PRESENCE_SOURCE_REVISION": revision},
    )
    actual = subprocess.run(
        ["git", "-C", str(tmp_path / "presence-source"), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert actual == revision
