"""Exercise baked skill delivery through Pi inside its real Linux sandbox."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from mps.pi_execution import tree_paths
from mps.pi_sandbox import PI_ENTRYPOINT, sandbox_command
from mps.pi_skills import configure_skills


def main():
    with tempfile.TemporaryDirectory(prefix="gardener-skills-probe-") as temporary:
        root = Path(temporary)
        workspace, home = root / "workspace", root / "home"
        workspace.mkdir()
        configure_skills(home, [])
        for path in [workspace, *tree_paths(home)]:
            os.chown(path, 2000, 2000, follow_symlinks=False)
        command = sandbox_command(
            workspace=workspace,
            home=home,
            tools=Path("/opt/phi-agent"),
            command=[
                "node",
                PI_ENTRYPOINT,
                "--no-session",
                "--mode",
                "rpc",
            ],
        )
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            output, errors = process.communicate(
                json.dumps({"type": "get_commands"}) + "\n",
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            process.kill()
            output, errors = process.communicate()
            raise RuntimeError(f"Pi did not exit: {errors}") from None
        replies = [json.loads(line) for line in output.splitlines()]
        reply = next(item for item in replies if item.get("command") == "get_commands")
        assert reply["success"], reply
        skills = [item for item in reply["data"]["commands"] if item["source"] == "skill"]
        assert {item["name"] for item in skills} == {"skill:gardener-history", "skill:pr-body"}, (
            skills
        )
        assert "/remote-skills/" in output, output
        print(output)


if __name__ == "__main__":
    main()
