"""Watch public stream rates and push sustained anomalies to Phi."""

import math
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from mps.blocks import secret
from prefect import flow, task
from prefect.variables import Variable

from flows.phi_trigger import PHI_BASE

STATE = "stream-activity-watch"
STREAM = "https://stream.waow.tech"
RELAY = "https://relay-eval.waow.tech/api"
METRICS = {
    "seq": "stream_upstream_seq",
    "start": "process_start_time_seconds",
    "reconnects": "jetstream_livestream_reconnects_total",
    "seen": "jetstream_livestream_last_seen_upstream_event_timestamp_seconds",
}


def sample_metrics(body: str, now: float) -> dict:
    values = {}
    for line in body.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0] in METRICS.values():
            values[fields[0]] = float(fields[1])
    sample = {key: values[name] for key, name in METRICS.items()}
    if any(not math.isfinite(value) or value < 0 for value in sample.values()):
        raise ValueError("invalid stream metrics")
    return {**sample, "at": now}


def expected_rate(profile: dict, start: float, end: float) -> float:
    hours = {row["h"]: row for row in profile["hours"]}
    total = 0.0
    cursor = start
    while cursor < end:
        stop = min(end, (cursor // 3600 + 1) * 3600)
        row = hours[datetime.fromtimestamp(cursor, UTC).hour]
        rate = float(row["med"])
        if row["n"] < 20 or not math.isfinite(rate) or rate <= 0:
            raise ValueError("insufficient hourly baseline")
        total += rate * (stop - cursor)
        cursor = stop
    return total / (end - start)


def relay_health(verdict: dict, now: float) -> tuple[bool, str]:
    rows = {row["host"]: row for row in verdict["relays"]}
    details = []
    healthy = True
    for host in ("stream.waow.tech", "bsky.network"):
        latest = rows[host]["latest"]
        age = now - datetime.fromisoformat(latest["ts"].replace("Z", "+00:00")).timestamp()
        coverage = float(latest["coverage_pct"])
        ok = (
            -60 <= age <= 4500
            and latest["connected"] is True
            and latest["behind"] is False
            and math.isfinite(coverage)
            and coverage >= 95
        )
        healthy &= ok
        details.append(f"{host}: coverage {coverage:.1f}%, check {age / 60:.0f}m old, usable={ok}")
    return healthy, "; ".join(details)


def observe(state: dict, sample: dict, profile: dict, verdict: dict) -> tuple[dict, dict]:
    previous = state.get("sample")
    result: dict[str, Any] = {**state, "sample": sample}
    result.pop("pending", None)
    clean = previous is not None
    elapsed = sample["at"] - previous["at"] if previous else 0
    if previous:
        clean = (
            240 <= elapsed <= 900
            and sample["start"] == previous["start"]
            and sample["reconnects"] == previous["reconnects"]
            and sample["seq"] >= previous["seq"] > 0
            and all(-10 <= p["at"] - p["seen"] <= 60 for p in (sample, previous))
        )
    healthy, health = relay_health(verdict, sample["at"])
    if previous is None or not clean or not healthy:
        result.pop("high_since", None)
        result.pop("low_since", None)
        return result, {"status": "deferred", "relay_health": health}
    typical = expected_rate(profile, previous["at"], sample["at"])
    rate = (sample["seq"] - previous["seq"]) / elapsed
    ratio = rate / typical
    report = {"status": "observed", "rate": rate, "typical": typical, "ratio": ratio}
    if ratio >= 2:
        result.pop("low_since", None)
        confirming = "high_since" in result
        since = result.setdefault("high_since", previous["at"])
        if confirming and not result.get("active") and sample["at"] - since >= 540:
            event_id = f"stream-activity:{int(since)}"
            result["pending"] = {
                "event_id": event_id,
                "material": (
                    f"Observed {datetime.fromtimestamp(sample['at'], UTC).isoformat()}. "
                    f"Upstream sequence advancement averaged {rate:.0f}/s over {elapsed:.0f}s, "
                    f"{ratio:.2f}x the hourly median ({typical:.0f}/s). "
                    f"Consecutive elevated windows since {datetime.fromtimestamp(since, UTC).isoformat()}. "
                    f"No restart or reconnect between samples; upstream timestamps are current. "
                    f"Relay checks: {health}. Cause unknown.\n"
                    f"Stats: {STREAM}/stats\nBaseline: {RELAY}/rate-profile\n"
                    f"Relay health: {RELAY}/status"
                ),
            }
            report["status"] = "pending"
    else:
        result.pop("high_since", None)
        if ratio < 1.5:
            since = result.setdefault("low_since", previous["at"])
            if sample["at"] - since >= 540:
                result.pop("active", None)
        else:
            result.pop("low_since", None)
    return result, report


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
async def read_public_stats() -> tuple[dict, dict, dict]:
    async with httpx.AsyncClient(timeout=20) as client:
        metrics = await client.get(f"{STREAM}/metrics")
        metrics.raise_for_status()
        sample = sample_metrics(metrics.text, time.time())
        baseline = await client.get(f"{RELAY}/rate-profile")
        baseline.raise_for_status()
        health = await client.get(f"{RELAY}/status")
        health.raise_for_status()
    return sample, baseline.json(), health.json()


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
async def deliver(event: dict) -> None:
    token = await secret("phi-control-token")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{PHI_BASE}/api/control/trigger/network-activity",
            headers={"authorization": f"Bearer {token}"},
            json=event,
        )
        response.raise_for_status()


@flow(name="watch-stream", log_prints=True, timeout_seconds=180)
async def watch_stream() -> dict[str, Any]:
    state = await Variable.aget(STATE, default={})
    if not isinstance(state, dict):
        raise ValueError("invalid stream watcher state")
    if state.get("pending"):
        await deliver(state["pending"])
        state["active"] = state.pop("pending")["event_id"]
        await Variable.aset(STATE, state, overwrite=True)
        return {"status": "delivered"}
    sample, baseline, health = await read_public_stats()
    state, report = observe(state, sample, baseline, health)
    await Variable.aset(STATE, state, overwrite=True)
    if state.get("pending"):
        await deliver(state["pending"])
        state["active"] = state.pop("pending")["event_id"]
        await Variable.aset(STATE, state, overwrite=True)
        report["status"] = "delivered"
    print(report)
    return report
