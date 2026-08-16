"""Regression tests for ingest surviving a single dead upstream.

ingest-81cdf915 died on a ReadTimeout from one page of the PDS likes walk;
ingest-b6a3551c died the same way on a github fetch. Every external fetch was
a bare @task — one attempt, joined with .result() — so any blip discarded the
whole run including the sources that had already succeeded.
"""

import logging

import httpx
import pytest

from flows.ingest import (
    LIKES_PAGES,
    NETWORK_RETRIES,
    _tolerate,
    fetch_all_tangled_items,
    fetch_authored_items,
    fetch_emails,
    fetch_issue_or_pr,
    fetch_nate_likes,
    fetch_notifications,
    fetch_phi_memory,
    resolve_liked_posts,
)
from mps.likes import fetch_likes

NETWORK_TASKS = [
    fetch_notifications,
    fetch_issue_or_pr,
    fetch_authored_items,
    fetch_all_tangled_items,
    fetch_emails,
    fetch_phi_memory,
    fetch_nate_likes,
    resolve_liked_posts,
]


@pytest.mark.parametrize("task", NETWORK_TASKS, ids=lambda t: t.name)
def test_network_tasks_retry(task):
    assert task.retries == NETWORK_RETRIES["retries"]
    assert task.retry_delay_seconds == NETWORK_RETRIES["retry_delay_seconds"]
    assert task.retry_jitter_factor == NETWORK_RETRIES["retry_jitter_factor"]


class _FailedFuture:
    def __init__(self, exc: BaseException):
        self._exc = exc

    def result(self, timeout=None, raise_on_failure=True):
        if raise_on_failure:
            raise self._exc
        return self._exc


class _OkFuture:
    def __init__(self, value):
        self._value = value

    def result(self, timeout=None, raise_on_failure=True):
        return self._value


LOGGER = logging.getLogger("test-ingest")


def test_tolerate_passes_a_healthy_source_through():
    degraded: list[str] = []
    assert _tolerate(_OkFuture([1, 2]), "likes", [], degraded, LOGGER) == [1, 2]
    assert degraded == []


def test_tolerate_substitutes_the_default_for_a_dead_source(caplog):
    degraded: list[str] = []
    future = _FailedFuture(httpx.ReadTimeout("The read operation timed out"))

    with caplog.at_level(logging.WARNING, logger="test-ingest"):
        assert _tolerate(future, "likes", [], degraded, LOGGER) == []

    assert degraded == ["likes"]
    assert "likes unavailable this run" in caplog.text


def test_tolerate_records_each_dead_source():
    degraded: list[str] = []
    _tolerate(_FailedFuture(httpx.ReadTimeout("x")), "likes", [], degraded, LOGGER)
    _tolerate(_OkFuture([]), "tangled", [], degraded, LOGGER)
    _tolerate(
        _FailedFuture(httpx.ConnectError("y")), "phi", ([], []), degraded, LOGGER
    )
    assert degraded == ["likes", "phi"]


# --- likes pagination is bounded ---


def _paged_transport(pages: int) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        cursor = request.url.params.get("cursor")
        n = int(cursor) if cursor else 0
        body = {
            "records": [
                {
                    "uri": f"at://x/{n}",
                    "value": {"subject": {"uri": "at://s"}, "createdAt": "2026-01-01"},
                }
            ]
        }
        if n + 1 < pages:
            body["cursor"] = str(n + 1)
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def test_fetch_likes_stops_at_max_pages():
    with httpx.Client(transport=_paged_transport(50)) as client:
        items = fetch_likes(client, max_pages=3)
    assert [i.at_uri for i in items] == ["at://x/0", "at://x/1", "at://x/2"]


def test_fetch_likes_walks_everything_when_unbounded():
    with httpx.Client(transport=_paged_transport(5)) as client:
        items = fetch_likes(client, max_pages=None)
    assert len(items) == 5


def test_fetch_likes_stops_on_a_missing_cursor_before_max_pages():
    with httpx.Client(transport=_paged_transport(2)) as client:
        items = fetch_likes(client, max_pages=10)
    assert len(items) == 2


def test_ingest_bounds_the_hourly_likes_walk():
    assert fetch_nate_likes.fn.__defaults__ == (LIKES_PAGES,)
    assert LIKES_PAGES is not None


# --- the Degraded state contract ingest relies on ---


def test_a_named_completed_state_is_completed_but_not_Completed():
    """The flow returns Completed(name="Degraded") when a source was skipped.

    The failure automation expects prefect.flow-run.{Failed,TimedOut,Crashed},
    and the event name is built from state.name — so this must stay a
    COMPLETED-type state carrying a name of its own.
    """
    from prefect import flow
    from prefect.states import Completed
    from prefect.testing.utilities import prefect_test_harness

    @flow
    def degraded_flow():
        return Completed(name="Degraded", message="unavailable: likes")

    with prefect_test_harness():
        state = degraded_flow(return_state=True)

    assert state.is_completed()
    assert state.name == "Degraded"
    assert state.type.value == "COMPLETED"
