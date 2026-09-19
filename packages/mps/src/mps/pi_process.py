"""Stream Pi's public JSON events and retain usage before the process exits."""

import json
import logging
import os
import queue
import re
import signal
import subprocess
import tempfile
import threading
import time
from uuid import uuid4

from genai_prices.types import Usage
from prefect import get_run_logger
from prefect.artifacts import create_table_artifact
from prefect.exceptions import MissingContextError

from mps.spend import record_usage


def run_logger():
    try:
        return get_run_logger()
    except MissingContextError:
        return logging.getLogger(__name__)


def usage_row(message: dict, *, provider: str, model: str, invocation: str):
    raw = message.get("usage") or {}
    # Pi's input/cache buckets are disjoint; genai-prices expects inclusive input.
    keys = ("input", "output", "cacheRead", "cacheWrite")
    if not all(type(raw.get(k)) is int and raw[k] >= 0 for k in keys):
        run_logger().warning("pi_usage_missing invocation=%s", invocation)
        return None
    if message.get("stopReason") in {"error", "aborted"} and not any(raw[k] for k in keys):
        # Pi initializes an error response with zero counters. That is not a
        # provider usage receipt and must not become a zero-dollar request.
        run_logger().warning("pi_usage_missing invocation=%s", invocation)
        return None
    priced_provider, priced_model = provider, model
    if model == "unknown":
        reported = message.get("model", "")
        if isinstance(reported, str) and re.fullmatch(r"[a-zA-Z0-9_.:/-]{1,120}", reported):
            priced_model = reported
    if provider == "aperture" and "/" in model:
        priced_provider, priced_model = model.split("/", 1)
        if priced_model == "claude-haiku-4.5":
            priced_model = "claude-haiku-4-5"
    return record_usage(
        task_name="pi",
        provider=priced_provider,
        model=priced_model,
        usage=Usage(
            input_tokens=raw["input"] + raw["cacheRead"] + raw["cacheWrite"],
            output_tokens=raw["output"],
            cache_read_tokens=raw["cacheRead"],
            cache_write_tokens=raw["cacheWrite"],
        ),
        metadata={"invocation": invocation, "transport": provider},
        invocation_id=invocation,
    )


def run_json_process(command, *, prompt=None, provider, model, timeout_seconds, **kwargs):
    """No raw JSON is logged. Completed responses persist during running jobs.

    An interrupted response may never supply usage. Summaries expose that
    uncertainty; prices are catalog estimates, never provider billing receipts.
    """
    invocation = str(uuid4())
    logger = run_logger()
    started = time.monotonic()
    rows, messages, missing = [], 0, 0
    final, outcome = None, "failed"
    logger.info(
        "pi_invocation %s", json.dumps({"id": invocation, "provider": provider, "model": model})
    )
    with tempfile.TemporaryFile(mode="w+t") as stdin, tempfile.TemporaryFile(mode="w+t") as stderr:
        stdin.write(prompt or "")
        stdin.seek(0)
        process = subprocess.Popen(
            command,
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
            start_new_session=True,
            **kwargs,
        )
        lines = queue.Queue(maxsize=64)
        stop = threading.Event()

        def read_lines():
            assert process.stdout is not None
            for line in process.stdout:
                while not stop.is_set():
                    try:
                        lines.put(line, timeout=0.1)
                        break
                    except queue.Full:
                        continue
                if stop.is_set():
                    return
            while not stop.is_set():
                try:
                    lines.put(None, timeout=0.1)
                    return
                except queue.Full:
                    continue

        reader = threading.Thread(target=read_lines, daemon=True)
        reader.start()
        try:
            while True:
                remaining = timeout_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    outcome = "timeout"
                    raise TimeoutError("Pi execution timed out; retained usage is partial")
                try:
                    line = lines.get(timeout=min(remaining, 0.1))
                except queue.Empty:
                    continue
                if line is None:
                    break
                event = json.loads(line)
                if event.get("type") == "tool_execution_end":
                    name = event.get("toolName")
                    tool = (
                        name
                        if name in {"read", "grep", "find", "ls", "bash", "edit", "write"}
                        else "other"
                    )
                    logger.info(
                        "pi_tool %s",
                        json.dumps({"tool": tool, "error": bool(event.get("isError"))}),
                    )
                if event.get("type") != "message_end":
                    continue
                message = event.get("message") or {}
                if message.get("role") != "assistant":
                    continue
                messages += 1
                row = usage_row(message, provider=provider, model=model, invocation=invocation)
                if row is None:
                    missing += 1
                else:
                    rows.append(row)
                if message.get("stopReason") in {"error", "aborted"}:
                    raise RuntimeError("Pi inference did not complete; retained usage is partial")
                text = "".join(
                    p.get("text", "") for p in message.get("content", []) if p.get("type") == "text"
                )
                if text:
                    final = text
            process.wait(timeout=max(0.001, timeout_seconds - (time.monotonic() - started)))
            if process.returncode:
                raise RuntimeError(f"Pi exited {process.returncode}; retained usage is partial")
            if final is None:
                raise RuntimeError("Pi returned no final text")
            outcome = "completed"
            return final
        finally:
            stop.set()
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            reader.join(timeout=1)
            if process.stdout is not None:
                process.stdout.close()
            priced = [r["total_cost_usd"] for r in rows if r["total_cost_usd"] is not None]
            summary = {
                "invocation": invocation,
                "provider": provider,
                "model": model,
                "outcome": outcome,
                "seconds": round(time.monotonic() - started, 3),
                "messages": messages,
                "usage_records": len(rows),
                "missing_usage": missing,
                "usage_complete": outcome == "completed" and messages > 0 and missing == 0,
                "known_estimated_cost_usd": sum(priced) if priced else None,
                "unpriced_records": len(rows) - len(priced),
                "billed_cost_usd": None,
                "tokens": sum(r["total_tokens"] for r in rows) if rows else None,
            }
            logger.info("pi_execution %s", json.dumps(summary))
            try:
                get_run_logger()  # Avoid starting a local Prefect server outside a run.
                create_table_artifact(
                    key="pi-execution-usage",
                    table=[summary],
                    description="Pi usage coverage and estimated inference cost; excludes infrastructure",
                )
            except MissingContextError:
                pass
            except Exception:
                logger.warning("Pi usage artifact persistence failed; summary emitted to run logs")
