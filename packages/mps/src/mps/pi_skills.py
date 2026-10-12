"""Pinned MCP skill delivery shared by Gardener's isolated Pi runs."""

import json
import shutil
import subprocess
from pathlib import Path

ADAPTER_REVISION = "6b3ded1c6788b1a47fb01e301de95a7801af95e0"
SKILLS_REVISION = "a34ed13d8711fc1befbab180ef3220dbfe5d5b9f"
ADAPTER_PACKAGE = f"git+https://github.com/zzstoatzz/pi-mcp-adapter.git#{ADAPTER_REVISION}"
SKILLS_PACKAGE = f"git+https://tangled.org/zzstoatzz.io/skills@{SKILLS_REVISION}"


def install_skill_server(tools: Path, environment: dict[str, str]) -> None:
    subprocess.run(
        ["uv", "tool", "install", "--force", "--python", "/usr/bin/python3", SKILLS_PACKAGE],
        env={
            **environment,
            "UV_TOOL_DIR": str(tools / "skills"),
            "UV_TOOL_BIN_DIR": str(tools / "bin"),
            "UV_PYTHON_DOWNLOADS": "never",
        },
        check=True,
        timeout=300,
    )


def configure_skills(home: Path, sources: list[str]) -> None:
    directories = []
    for index, source in enumerate(sources):
        source_path = Path(source)
        target = home / "skill-sources" / str(index)
        target.parent.mkdir(exist_ok=True)
        if source_path.is_dir():
            shutil.copytree(source_path, target, symlinks=True)
        else:
            target.mkdir()
            shutil.copy2(source_path, target / "SKILL.md", follow_symlinks=False)
        directories += ["--directory", f"/home/agent/skill-sources/{index}"]
    config = home / ".pi/agent"
    config.mkdir(parents=True, exist_ok=True)
    (config / "settings.json").write_text(
        json.dumps(
            {
                "packages": ["/opt/phi-agent/pi/node_modules/pi-mcp-adapter"],
                "extensions": ["-builtin:mcp"],
            }
        )
    )
    (config / "mcp-adapter.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "gardener-skills": {
                        "command": "/opt/phi-agent/bin/stoat-skills",
                        "args": directories or ["--profile", "gardener"],
                        "skills": True,
                        "protocolVersion": "auto",
                        "exposeResources": False,
                    }
                },
                "settings": {"scriptMode": False},
            }
        )
    )
