"""Live Pi revision probe; accepts an ephemeral inference grant only on stdin."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/opt/phi-runner")

from mps.pi_execution import run_isolated_pi
from mps.pi_sandbox import AGENT_UID, sandbox_command

payload = json.load(sys.stdin)
os.environ["PHI_INFERENCE_TOKEN"] = payload["token"]
os.environ["PHI_INFERENCE_URL"] = payload["endpoint"]

with tempfile.TemporaryDirectory(prefix="phi-revision-") as temporary:
    root = Path(temporary)
    workspace = root / "repo"
    workspace.mkdir()
    source = workspace / "retry_budget.py"
    source.write_text("def remaining(attempts, limit=6):\n    return limit - attempts\n")
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    subprocess.run(["git", "-C", str(workspace), "add", "."], check=True)
    home = root / "validation-home"
    home.mkdir()
    os.chown(home, AGENT_UID, AGENT_UID)

    def validate():
        # Trusted assertions are passed as code outside the agent-writable tree.
        command = sandbox_command(
            workspace=workspace,
            home=home,
            tools=Path("/opt/phi-agent"),
            command=[
                "python3",
                "-c",
                "from retry_budget import remaining; "
                "assert remaining(0)==6; assert remaining(6)==0; "
                "assert remaining(9)==0; assert remaining(2, 3)==1",
            ],
        )
        return subprocess.run(command, capture_output=True, text=True, check=False).returncode

    before = validate()
    if before == 0:
        raise RuntimeError("Probe requires an initially failing revision")
    run_isolated_pi(
        "Review feedback for retry_budget.py: remaining(9) returns -3, but an "
        "exhausted retry budget must return zero. Fix remaining so it never "
        "returns a negative number. Preserve the default limit of six and "
        "support explicit limits. Run a Python check covering exhaustion and "
        "ordinary cases. Edit only retry_budget.py; do not commit. Reply briefly.",
        workspace=workspace,
        tool_args=[],
        thinking="off",
        timeout_seconds=120,
        skills=[],
    )
    after = validate()
    changed = (
        source.read_text() != "def remaining(attempts, limit=6):\n    return limit - attempts\n"
    )
    git_config = subprocess.run(
        ["git", "-C", str(workspace), "config", "--local", "--get", "core.hooksPath"],
        capture_output=True,
        check=False,
    )
    print(
        json.dumps(
            {
                "initial_validation_failed": before != 0,
                "revised_validation_passed": after == 0,
                "source_changed": changed,
                "git_hooks_unchanged": git_config.returncode == 1,
            }
        )
    )
    if after or not changed:
        raise RuntimeError("Pi revision did not pass independent validation")
