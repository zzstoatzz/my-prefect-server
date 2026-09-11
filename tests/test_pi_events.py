import json
import logging

import pytest
from mps.pi_execution import read_pi_events


def test_usage_and_tool_events_do_not_log_contents(caplog):
    events = [
        {"type": "message_update", "usage": {"input": 999}},
        {
            "type": "tool_execution_end",
            "toolName": "read",
            "isError": False,
            "result": "private-file",
        },
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "stopReason": "stop",
                "usage": {"input": 9, "cacheRead": 6302, "cacheWrite": 0, "output": 4},
                "content": [{"type": "text", "text": "final-answer"}],
            },
        },
    ]
    with caplog.at_level(logging.INFO):
        assert read_pi_events("\n".join(map(json.dumps, events))) == "final-answer"
    assert '"cacheRead": 6302' in caplog.text
    assert '"tool": "read"' in caplog.text
    assert "private-file" not in caplog.text
    assert "final-answer" not in caplog.text
    assert "999" not in caplog.text


def test_failed_inference_cannot_return_earlier_text():
    event = {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "stopReason": "error",
            "content": [{"type": "text", "text": "unfinished"}],
        },
    }
    with pytest.raises(RuntimeError, match="did not complete"):
        read_pi_events(json.dumps(event))
