"""Regression tests for bisk-snapshot resilience.

bisk-snapshot failed 3 of 9 runs on 2026-08-12 with
`httpx.RemoteProtocolError: Server disconnected without sending a response`,
and once with a 502, all from constellation.microcosm.blue. Its tasks had no
retries, so a single dropped connection anywhere in a paginated follower walk
failed the whole run.

What we test:
  - every task that talks to the network carries retries with jittered backoff
  - `_get` still raises on error responses, so a failure reaches the task's
    retry rather than returning partial data
  - worst-case retry sleeping fits inside the flow's timeout, so a retrying
    run cannot be killed mid-recovery
"""

import httpx
import pytest

from flows.bisk import (
    _get,
    all_time_coop,
    bisk_snapshot,
    build_pool,
    publish,
    window_bisks,
)

NETWORK_TASKS = (build_pool, window_bisks, all_time_coop, publish)


def test_network_tasks_have_jittered_retries():
    for task in NETWORK_TASKS:
        assert task.retries >= 2, f"{task.name} must retry transient upstream failures"
        assert task.retry_jitter_factor, f"{task.name} should jitter its retry delays"
        assert task.retry_delay_seconds, f"{task.name} should back off between attempts"


def test_retry_budget_fits_in_the_flow_timeout():
    """A run that retries must not be killed by the flow timeout mid-recovery."""
    worst_case = 0.0
    for task in NETWORK_TASKS:
        delays = task.retry_delay_seconds
        delays = delays if isinstance(delays, (list, tuple)) else [delays] * task.retries
        # retry_jitter_factor=1 can double each delay
        worst_case += sum(delays) * (1 + (task.retry_jitter_factor or 0))

    assert worst_case < bisk_snapshot.timeout_seconds, (
        f"retry sleeping ({worst_case:.0f}s) must leave room inside the "
        f"{bisk_snapshot.timeout_seconds}s flow timeout"
    )


@pytest.mark.parametrize(
    "failure",
    [
        httpx.RemoteProtocolError("Server disconnected without sending a response."),
        httpx.ConnectError("connection refused"),
    ],
)
async def test_get_propagates_transport_errors(failure: Exception):
    """Transport failures must reach the task, not be swallowed into a result."""

    class Boom:
        async def get(self, url: str, params: dict) -> httpx.Response:
            raise failure

    with pytest.raises(type(failure)):
        await _get(Boom(), "https://example.invalid/xrpc/whatever", {})


async def test_get_raises_on_error_status():
    """A 502 from constellation must fail the task, not return an empty dict."""
    request = httpx.Request("GET", "https://example.invalid/xrpc/whatever")

    class Gateway:
        async def get(self, url: str, params: dict) -> httpx.Response:
            return httpx.Response(502, request=request, text="bad gateway")

    with pytest.raises(httpx.HTTPStatusError):
        await _get(Gateway(), "https://example.invalid/xrpc/whatever", {})
