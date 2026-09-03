"""typeahead-enrich-backfill lost 2 of 10 runs (2026-08-29, -30) to a single
httpx.ReadTimeout raised from a Turso pipeline write in flush_writes. The
appview call next to it retried three times; the Turso call retried never,
so one slow lock acquisition on the shared single writer threw away the rest
of a 3.5 h budget. _tq must retry transport failures and still refuse to
retry a statement-level Turso error, which is a bug rather than weather."""

import httpx
import pytest

from flows.typeahead_enrich_backfill import TURSO_ATTEMPTS, TURSO_BACKOFF_S, _tq


class FlakyClient:
    """fails `failures` times with the given exception, then answers"""

    def __init__(self, failures: int, exc: Exception):
        self.failures = failures
        self.exc = exc
        self.calls = 0

    def post(self, url, headers, json, timeout):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exc
        return httpx.Response(200, json={"results": [
            {"type": "ok", "response": {"type": "execute", "result": {"rows": []}}},
            {"type": "ok", "response": {"type": "close"}},
        ]}, request=httpx.Request("POST", url))


@pytest.fixture(autouse=True)
def turso_env(monkeypatch):
    monkeypatch.setenv("TURSO_URL", "libsql://example.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "t")


def test_read_timeout_is_retried_with_backoff():
    slept: list[float] = []
    client = FlakyClient(2, httpx.ReadTimeout("The read operation timed out"))
    out = _tq(client, [{"sql": "SELECT 1"}], sleep=slept.append)
    assert out == [{"rows": []}]
    assert client.calls == 3
    assert slept == list(TURSO_BACKOFF_S[:2])


def test_gives_up_after_the_last_attempt():
    client = FlakyClient(TURSO_ATTEMPTS + 1, httpx.ReadTimeout("The read operation timed out"))
    with pytest.raises(httpx.ReadTimeout):
        _tq(client, [{"sql": "SELECT 1"}], sleep=lambda _: None)
    assert client.calls == TURSO_ATTEMPTS


def test_statement_error_is_not_retried():
    class ErrClient:
        calls = 0

        def post(self, url, headers, json, timeout):
            self.calls += 1
            return httpx.Response(200, json={"results": [
                {"type": "error", "error": {"message": "no such column: nope"}},
            ]}, request=httpx.Request("POST", url))

    client = ErrClient()
    with pytest.raises(RuntimeError, match="turso"):
        _tq(client, [{"sql": "SELECT nope"}], sleep=lambda _: None)
    assert client.calls == 1


def test_retry_budget_is_small_next_to_the_flow_budget():
    # worst case ~65 s of sleeping per call against a 12,600 s budget
    assert sum(TURSO_BACKOFF_S) < 120
