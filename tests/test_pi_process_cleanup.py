import json
import os
import sys

import pytest
from mps import pi_process


def test_exited_process_group_does_not_mask_the_inference_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_SPEND_LOG_PATH", str(tmp_path / "usage.jsonl"))
    start = pi_process.subprocess.Popen
    kill_group = os.killpg
    children = []

    def spawn(*args, **kwargs):
        process = start(*args, **kwargs)
        children.append(process)
        return process

    def exited_group(pid, signal):
        kill_group(pid, signal)
        children[0].wait(timeout=2)
        kill_group(pid, signal)

    monkeypatch.setattr(pi_process.subprocess, "Popen", spawn)
    monkeypatch.setattr(os, "killpg", exited_group)
    failed = {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "stopReason": "error",
            "errorMessage": "Provider unavailable",
            "usage": {"input": 10, "output": 5, "cacheRead": 0, "cacheWrite": 0},
            "content": [{"type": "text", "text": "private answer"}],
        },
    }
    script = f"import time; print({json.dumps(failed)!r}, flush=True); time.sleep(60)"
    try:
        with pytest.raises(RuntimeError, match="Pi inference"):
            pi_process.run_json_process(
                [sys.executable, "-c", script],
                provider="openai-codex",
                model="gpt-5.6-sol",
                timeout_seconds=2,
            )
        assert children[0].poll() is not None
    finally:
        for process in children:
            if process.poll() is None:
                kill_group(process.pid, 9)
                process.wait(timeout=2)
