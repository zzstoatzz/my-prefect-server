"""Exercise baked skill delivery through Pi inside its real Linux sandbox."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from mps.pi_execution import tree_paths
from mps.pi_sandbox import PI_ENTRYPOINT, sandbox_command
from mps.pi_skills import configure_skills


def probe(sources: list[str], expected: set[str]):
    with tempfile.TemporaryDirectory(prefix="gardener-skills-probe-") as temporary:
        root = Path(temporary)
        workspace, home = root / "workspace", root / "home"
        workspace.mkdir()
        configure_skills(home, sources)
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
        assert {item["name"] for item in skills} == expected, skills
        assert "/remote-skills/" in output, output
        for skill in skills:
            path = skill["sourceInfo"]["path"]
            local = home / Path(path).relative_to("/home/agent")
            assert local.read_text().startswith("---\n")
        print(json.dumps({"verified": sorted(expected), "source": "MCP", "uid": 2000}))


def main():
    probe([], {"skill:gardener-history", "skill:pr-body"})
    with tempfile.TemporaryDirectory(prefix="gardener-skill-input-") as temporary:
        root = Path(temporary)
        skill = root / "SKILL.md"
        skill.write_text(
            "---\nname: selected-skill\ndescription: Test explicitly selected input.\n---\nRead references/details.md.\n"
        )
        (root / "references").mkdir()
        (root / "references/details.md").write_text("Selected supporting material.\n")
        probe([str(root)], {"skill:selected-skill"})
        probe([str(skill)], {"skill:selected-skill"})


if __name__ == "__main__":
    main()
