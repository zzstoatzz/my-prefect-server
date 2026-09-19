"""The audit must expose missing evidence and never silently truncate history."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

spec = importlib.util.spec_from_file_location(
    "coding_jobs_audit", Path(__file__).parents[1] / "scripts/coding_jobs_audit.py"
)
assert spec and spec.loader
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def test_pagination_continues_after_server_capped_page():
    offsets = []

    def handle(request):
        offset = json.loads(request.content)["offset"]
        offsets.append(offset)
        return httpx.Response(200, json=[{"id": str(offset)}] if offset < 3 else [])

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert len(audit.pages(client, "https://example.test", "flows")) == 3
    assert offsets == [0, 1, 2, 3]


def test_ignored_pagination_fails_instead_of_claiming_complete():
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[{"id": "same"}]))
        ) as client,
        pytest.raises(RuntimeError, match="pagination"),
    ):
        audit.pages(client, "https://example.test", "flows")


def test_summary_preserves_degraded_and_missing_usage_without_exporting_secrets():
    run = {
        "id": "run",
        "name": "run",
        "created": "2026-09-18",
        "state_name": "Degraded",
        "state_type": "COMPLETED",
        "parameters": {"prompt": "private prompt", "api_key": "secret"},
    }
    summary = audit.summarize(
        run,
        "autofix",
        [{"message": "secret"}],
        [{"id": "artifact", "key": "output", "type": "markdown", "data": "secret"}],
        "https://example.test",
    )
    assert summary["state"] == "Degraded"
    assert summary["tokens"] is None
    assert summary["usage_events"] == 0
    assert "secret" not in json.dumps(summary)
    assert "private prompt" not in json.dumps(summary)


def test_pds_follows_cursor_even_on_short_page():
    calls = []

    def handle(request):
        assert "authorization" not in request.headers
        if request.url.host == "plc.directory":
            return httpx.Response(
                200,
                json={
                    "service": [
                        {
                            "type": "AtprotoPersonalDataServer",
                            "serviceEndpoint": "https://pds.example",
                        }
                    ]
                },
            )
        cursor = request.url.params.get("cursor")
        calls.append(cursor)
        return httpx.Response(
            200,
            json={"records": [{"uri": "one"}], "cursor": "next"}
            if cursor is None
            else {"records": []},
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert audit.records(client, "did:plc:test", "sh.tangled.repo.pull") == [{"uri": "one"}]
    assert calls == [None, "next"]


def test_cost_deduplicates_and_does_not_treat_legacy_zero_as_free():
    row = {"id": "one", "total_cost_usd": 0.1, "total_tokens": 100, "task_name": "pi_judge"}
    unknown = {"id": "two", "total_cost_usd": 0, "total_tokens": 200, "task_name": "pi"}
    summary = audit.cost_summary([row, row, unknown])
    assert summary["usage_records"] == 2
    assert summary["tokens"] == 300
    assert summary["known_estimated_cost_usd"] == 0.1
    assert summary["unpriced_records"] == 1
    assert summary["billed_cost_usd"] is None
