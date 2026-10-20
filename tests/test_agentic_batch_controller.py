import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/agentic_batch.sh"


def init(directory, *issues):
    return subprocess.run(
        ["bash", str(SCRIPT), "init", str(directory), *map(str, issues)],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("issues", [[], [1, 1], list(range(1, 14)), [-1], [1.5]])
def test_invalid_queue_never_creates_a_runnable_ledger(tmp_path, issues):
    directory = tmp_path / "test-batch"
    result = init(directory, *issues)
    assert result.returncode != 0
    assert not (directory / "ledger.json").exists()


@pytest.mark.parametrize(
    "expression, expected",
    [
        (".policy.expires_at=0", "deadline_reached"),
        (".consecutive_failures=3", "failure_limit"),
        (".input_tokens=8000000", "token_limit"),
        (".output_tokens=160000", "token_limit"),
        (".prs=[{number:1},{number:2},{number:3}]", "target_reached"),
        (".attempts=[{issue:1}]", "queue_exhausted"),
    ],
)
def test_stop_conditions_finish_without_submitting_any_run(tmp_path, expression, expected):
    directory = tmp_path / "test-batch"
    assert init(directory, 1).returncode == 0
    ledger = directory / "ledger.json"
    updated = subprocess.check_output(["jq", expression, str(ledger)])
    ledger.write_bytes(updated)
    result = subprocess.run(
        ["bash", str(SCRIPT), "run", str(directory)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    status = subprocess.check_output(["jq", "-r", ".status", str(ledger)], text=True).strip()
    assert status == expected
    assert not list(directory.glob("issue-*"))
    assert not (directory / "lock").exists()


def test_existing_batch_cannot_be_reinitialized(tmp_path):
    directory = tmp_path / "test-batch"
    assert init(directory, 1).returncode == 0
    before = (directory / "policy.json").read_bytes()
    assert init(directory, 2).returncode != 0
    assert (directory / "policy.json").read_bytes() == before


def test_live_controller_lock_prevents_second_process(tmp_path):
    directory = tmp_path / "test-batch"
    assert init(directory, 1).returncode == 0
    (directory / "lock").mkdir()
    (directory / "lock/pid").write_text(str(os.getpid()))
    result = subprocess.run(
        ["bash", str(SCRIPT), "run", str(directory)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    assert "already running" in result.stderr
    assert not list(directory.glob("issue-*"))
