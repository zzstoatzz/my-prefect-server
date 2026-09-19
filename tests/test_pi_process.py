import json
import logging
import sys

import pytest
from mps.pi_process import run_json_process


def event():
    return {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "stopReason": "stop",
            "usage": {"input": 10, "output": 5, "cacheRead": 100, "cacheWrite": 20},
            "content": [{"type": "text", "text": "private answer"}],
        },
    }


def run(tmp_path, monkeypatch, suffix):
    monkeypatch.setenv("LLM_SPEND_LOG_PATH", str(tmp_path / "usage.jsonl"))
    script = f"import time, sys; print({json.dumps(event())!r}, flush=True); {suffix}"
    return run_json_process(
        [sys.executable, "-c", script],
        provider="anthropic",
        model="claude-haiku-4-5",
        timeout_seconds=0.5,
    )


def test_usage_survives_nonzero_exit(tmp_path, monkeypatch, caplog):
    with caplog.at_level(logging.INFO), pytest.raises(RuntimeError, match="exited 3"):
        run(tmp_path, monkeypatch, "sys.exit(3)")
    row = json.loads((tmp_path / "usage.jsonl").read_text())
    assert row["input_tokens"] == 130
    assert row["total_tokens"] == 135
    assert row["total_cost_usd"] > 0
    assert '"outcome": "failed"' in caplog.text
    assert '"usage_complete": false' in caplog.text
    assert "private answer" not in caplog.text
    assert "llm_usage " in caplog.text


def test_usage_survives_timeout(tmp_path, monkeypatch, caplog):
    with caplog.at_level(logging.INFO), pytest.raises(TimeoutError):
        run(tmp_path, monkeypatch, "time.sleep(10)")
    assert json.loads((tmp_path / "usage.jsonl").read_text())["total_tokens"] == 135
    assert '"outcome": "timeout"' in caplog.text


def test_success_returns_text_and_records_coverage(tmp_path, monkeypatch, caplog):
    with caplog.at_level(logging.INFO):
        assert run(tmp_path, monkeypatch, "pass") == "private answer"
    assert '"usage_complete": true' in caplog.text
    assert '"billed_cost_usd": null' in caplog.text


def test_error_placeholder_zero_usage_is_unknown(monkeypatch):
    from mps.pi_process import usage_row

    message = event()["message"]
    message["stopReason"] = "error"
    message["usage"] = dict.fromkeys(("input", "output", "cacheRead", "cacheWrite"), 0)
    assert (
        usage_row(message, provider="aperture", model="openai/gpt-5.6-luna", invocation="x") is None
    )
