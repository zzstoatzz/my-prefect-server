from __future__ import annotations

import json
from pathlib import Path

import duckdb
from genai_prices.types import Usage

from flows.transform import import_spend_log
from mps.spend import RAW_LLM_SPEND_SCHEMA, record_usage


def test_record_usage_appends_jsonl(tmp_path: Path) -> None:
    log_path = tmp_path / "llm-spend.jsonl"

    record_usage(
        log_path=str(log_path),
        task_name="smoke",
        provider="anthropic",
        model="claude-haiku-4-5",
        usage=Usage(input_tokens=1000, output_tokens=100),
        metadata={"kind": "test"},
    )

    [line] = log_path.read_text(encoding="utf-8").splitlines()
    event = json.loads(line)
    assert event["task_name"] == "smoke"
    assert event["model"] == "claude-haiku-4-5"
    assert event["input_tokens"] == 1000
    assert event["output_tokens"] == 100
    assert event["total_cost_usd"] > 0
    assert event["metadata"] == {"kind": "test"}


def test_record_usage_prices_aliased_model(tmp_path: Path) -> None:
    # claude-sonnet-5 isn't in genai-prices yet; it must still record (with the
    # real model string) and be priced via its alias, not silently dropped.
    log_path = tmp_path / "llm-spend.jsonl"

    record_usage(
        log_path=str(log_path),
        task_name="smoke",
        provider="anthropic",
        model="claude-sonnet-5",
        usage=Usage(input_tokens=1_000_000, output_tokens=1_000_000),
    )

    [line] = log_path.read_text(encoding="utf-8").splitlines()
    event = json.loads(line)
    assert event["model"] == "claude-sonnet-5"  # real model preserved
    assert event["total_cost_usd"] > 0  # priced via alias, row not dropped


def test_record_usage_unknown_model_records_zero_cost(tmp_path: Path) -> None:
    # A truly unknown model must still preserve token usage rather than drop the
    # row — the dashboard should never silently lose volume.
    log_path = tmp_path / "llm-spend.jsonl"

    record_usage(
        log_path=str(log_path),
        task_name="smoke",
        provider="anthropic",
        model="claude-nonexistent-99",
        usage=Usage(input_tokens=1000, output_tokens=100),
    )

    [line] = log_path.read_text(encoding="utf-8").splitlines()
    event = json.loads(line)
    assert event["model"] == "claude-nonexistent-99"
    assert event["input_tokens"] == 1000
    assert event["total_cost_usd"] == 0


def test_import_spend_log_materializes_raw_llm_spend(tmp_path: Path) -> None:
    log_path = tmp_path / "llm-spend.jsonl"
    db_path = tmp_path / "analytics.duckdb"
    event = {
        "id": "abc123",
        "recorded_at": "2026-06-10T00:00:00+00:00",
        "flow_name": "brief",
        "flow_run_id": "flow-run-id",
        "task_name": "generate_briefing",
        "provider": "anthropic",
        "model": "claude-haiku-4-5",
        "request_count": 1,
        "input_tokens": 10,
        "cache_write_tokens": 0,
        "cache_read_tokens": 0,
        "output_tokens": 5,
        "total_tokens": 15,
        "input_cost_usd": 0.1,
        "output_cost_usd": 0.2,
        "total_cost_usd": 0.3,
        "metadata": {"item_count": 4},
    }
    log_path.write_text(json.dumps(event) + "\n", encoding="utf-8")

    count = import_spend_log.fn(log_path, db_path)

    assert count == 1
    con = duckdb.connect(str(db_path))
    try:
        con.execute(RAW_LLM_SPEND_SCHEMA)
        row = con.execute(
            "SELECT id, flow_name, task_name, total_cost_usd, metadata_json FROM raw_llm_spend"
        ).fetchone()
    finally:
        con.close()
    assert row == ("abc123", "brief", "generate_briefing", 0.3, '{"item_count":4}')
