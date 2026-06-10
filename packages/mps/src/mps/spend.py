"""LLM usage and cost tracking for flows."""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from genai_prices import calc_price
from genai_prices.types import Usage


RAW_LLM_SPEND_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_llm_spend (
    id VARCHAR PRIMARY KEY,
    recorded_at TIMESTAMP,
    flow_name VARCHAR,
    flow_run_id VARCHAR,
    task_name VARCHAR,
    provider VARCHAR,
    model VARCHAR,
    request_count INTEGER,
    input_tokens INTEGER,
    cache_write_tokens INTEGER,
    cache_read_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER,
    input_cost_usd DOUBLE,
    output_cost_usd DOUBLE,
    total_cost_usd DOUBLE,
    metadata_json VARCHAR
)
"""


def spend_log_path() -> str:
    if path := os.environ.get("LLM_SPEND_LOG_PATH"):
        return path
    if analytics_db := os.environ.get("ANALYTICS_DB_PATH"):
        return str(Path(analytics_db).with_name("llm-spend.jsonl"))
    return os.environ.get("PREFECT_LOCAL_STORAGE_PATH", "/tmp") + "/llm-spend.jsonl"


def _runtime_value(module_name: str, attr: str) -> str:
    try:
        module = __import__(module_name, fromlist=[attr])
        value = getattr(module, attr)
        return str(value or "")
    except Exception:
        return ""


def current_flow_name() -> str:
    return _runtime_value("prefect.runtime.flow_run", "flow_name")


def current_flow_run_id() -> str:
    return _runtime_value("prefect.runtime.flow_run", "id")


def _int(value: Any) -> int:
    return int(value or 0)


def _money(value: Decimal | int | float | None) -> float:
    return float(value or 0)


def _usage_from_pydantic_result(result: Any) -> Usage:
    raw_usage = result.usage() if callable(getattr(result, "usage", None)) else getattr(result, "usage", None)
    if raw_usage is None:
        return Usage()
    return Usage(
        input_tokens=getattr(raw_usage, "input_tokens", 0),
        cache_write_tokens=getattr(raw_usage, "cache_write_tokens", 0),
        cache_read_tokens=getattr(raw_usage, "cache_read_tokens", 0),
        output_tokens=getattr(raw_usage, "output_tokens", 0),
        input_audio_tokens=getattr(raw_usage, "input_audio_tokens", 0),
        cache_audio_read_tokens=getattr(raw_usage, "cache_audio_read_tokens", 0),
    )


def _request_count(result: Any) -> int:
    raw_usage = result.usage() if callable(getattr(result, "usage", None)) else getattr(result, "usage", None)
    return _int(getattr(raw_usage, "requests", 1)) or 1


def _row_id(
    *,
    recorded_at: dt.datetime,
    flow_run_id: str,
    task_name: str,
    provider: str,
    model: str,
    usage: Usage,
    metadata: dict[str, Any] | None,
) -> str:
    payload = {
        "recorded_at": recorded_at.isoformat(),
        "flow_run_id": flow_run_id,
        "task_name": task_name,
        "provider": provider,
        "model": model,
        "usage": asdict(usage) if is_dataclass(usage) else str(usage),
        "metadata": metadata or {},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:32]


def _append_jsonl(path: str, event: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, default=str, separators=(",", ":"), sort_keys=True) + "\n"
    with target.open("a", encoding="utf-8") as fp:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
        try:
            fp.write(line)
            fp.flush()
            os.fsync(fp.fileno())
        finally:
            fcntl.flock(fp.fileno(), fcntl.LOCK_UN)


def record_usage(
    *,
    log_path: str | None = None,
    task_name: str,
    provider: str,
    model: str,
    usage: Usage,
    request_count: int = 1,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Persist one LLM usage/cost row.

    This is intentionally best-effort. Cost telemetry should not turn a
    successful user-facing flow into a failed one.
    """
    try:
        recorded_at = dt.datetime.now(dt.UTC)
        flow_name = current_flow_name()
        flow_run_id = current_flow_run_id()
        price = calc_price(
            usage,
            model,
            provider_id=provider,
            genai_request_timestamp=recorded_at,
        )
        event = {
            "id": _row_id(
                recorded_at=recorded_at,
                flow_run_id=flow_run_id,
                task_name=task_name,
                provider=provider,
                model=model,
                usage=usage,
                metadata=metadata,
            ),
            "recorded_at": recorded_at.isoformat(),
            "flow_name": flow_name,
            "flow_run_id": flow_run_id,
            "task_name": task_name,
            "provider": provider,
            "model": model,
            "request_count": request_count,
            "input_tokens": _int(usage.input_tokens),
            "cache_write_tokens": _int(usage.cache_write_tokens),
            "cache_read_tokens": _int(usage.cache_read_tokens),
            "output_tokens": _int(usage.output_tokens),
            "total_tokens": _int(usage.input_tokens) + _int(usage.output_tokens),
            "input_cost_usd": _money(price.input_price),
            "output_cost_usd": _money(price.output_price),
            "total_cost_usd": _money(price.total_price),
            "metadata": metadata or {},
        }
        _append_jsonl(log_path or spend_log_path(), event)
    except Exception:
        return


def record_pydantic_ai_result(
    *,
    task_name: str,
    model: str,
    provider: str = "anthropic",
    result: Any,
    metadata: dict[str, Any] | None = None,
    log_path: str | None = None,
) -> None:
    record_usage(
        log_path=log_path,
        task_name=task_name,
        provider=provider,
        model=model,
        usage=_usage_from_pydantic_result(result),
        request_count=_request_count(result),
        metadata=metadata,
    )


def record_openai_embedding_response(
    *,
    task_name: str,
    model: str,
    response: Any,
    item_count: int | None = None,
    metadata: dict[str, Any] | None = None,
    log_path: str | None = None,
) -> None:
    response_usage = getattr(response, "usage", None)
    input_tokens = _int(getattr(response_usage, "prompt_tokens", 0) or getattr(response_usage, "total_tokens", 0))
    merged_metadata = dict(metadata or {})
    if item_count is not None:
        merged_metadata["item_count"] = item_count
    record_usage(
        log_path=log_path,
        task_name=task_name,
        provider="openai",
        model=model,
        usage=Usage(input_tokens=input_tokens, output_tokens=0),
        request_count=1,
        metadata=merged_metadata,
    )
