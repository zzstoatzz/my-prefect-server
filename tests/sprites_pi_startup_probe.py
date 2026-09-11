"""Verify the installed Pi CLI and custom model inside the real agent boundary."""

import json
import os
import runpy
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    sandbox = runpy.run_path(sys.argv[1])
    with tempfile.TemporaryDirectory(prefix="phi-pi-startup-") as temporary:
        root = Path(temporary)
        workspace, home = root / "workspace", root / "home"
        workspace.mkdir()
        agent_config = home / ".pi/agent"
        agent_config.mkdir(parents=True)
        (agent_config / "models.json").write_text(json.dumps(sandbox["aperture_models"]()))
        for path in [workspace, home, *home.rglob("*")]:
            os.chown(path, 2000, 2000)
        for flags in (["--version"], ["--list-models", "aperture"]):
            result = subprocess.run(
                sandbox["sandbox_command"](
                    workspace=workspace,
                    home=home,
                    tools=Path("/opt/phi-agent"),
                    command=["node", sandbox["PI_ENTRYPOINT"], *flags],
                ),
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if flags[0] == "--version":
                assert "0.84.4" in result.stdout, result.stdout
            else:
                assert "openai/gpt-5.6-luna" in result.stdout, result.stdout
            print(result.stdout.strip())


if __name__ == "__main__":
    main()
