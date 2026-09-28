"""typeahead-enrich-backfill lost 2 of 10 runs (2026-08-29, -30) to a single
httpx.ReadTimeout raised from a Turso pipeline write in flush_writes. The
appview call next to it retried three times; the Turso call retried never,
so one slow lock acquisition on the shared single writer threw away the rest
of a 3.5 h budget. On 2026-09-27 a run died the same way on a result-level
SQLITE_IOERR inside a 200 response. _tq must retry transport failures and
SQLITE_IOERR/SQLITE_BUSY results, and still refuse to retry any other
statement-level Turso error, which is a bug rather than weather."""

import httpx
import pytest

from flows.typeahead_enrich_backfill import (
    TURSO_ATTEMPTS,
    TURSO_BACKOFF_S,
    TursoTransientError,
    _tq,
)


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
        return httpx.Response(
            200,
            json={
                "results": [
                    {"type": "ok", "response": {"type": "execute", "result": {"rows": []}}},
                    {"type": "ok", "response": {"type": "close"}},
                ]
            },
            request=httpx.Request("POST", url),
        )


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
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"type": "error", "error": {"message": "no such column: nope"}},
                    ]
                },
                request=httpx.Request("POST", url),
            )

    client = ErrClient()
    with pytest.raises(RuntimeError, match="turso"):
        _tq(client, [{"sql": "SELECT nope"}], sleep=lambda _: None)
    assert client.calls == 1


def test_retry_budget_is_small_next_to_the_flow_budget():
    # worst case ~65 s of sleeping per call against a 12,600 s budget
    assert sum(TURSO_BACKOFF_S) < 120


IOERR = {"message": "SQLite error: disk I/O error", "code": "SQLITE_IOERR"}


class ResultErrorClient:
    """answers 200 with `error` as the first result `failures` times, then succeeds"""

    def __init__(self, failures: int, error: dict):
        self.failures, self.error, self.calls = failures, error, 0

    def post(self, url, headers, json, timeout):
        self.calls += 1
        if self.calls <= self.failures:
            results = [{"type": "error", "error": self.error}]
        else:
            results = [{"type": "ok", "response": {"type": "execute", "result": {"rows": []}}}]
        return httpx.Response(200, json={"results": results}, request=httpx.Request("POST", url))


def test_sqlite_ioerr_result_is_retried_with_backoff():
    slept: list[float] = []
    client = ResultErrorClient(1, IOERR)
    assert _tq(client, [{"sql": "UPDATE actors SET x = 1 WHERE did = ?"}], sleep=slept.append) == [
        {"rows": []}
    ]
    assert client.calls == 2
    assert slept == [TURSO_BACKOFF_S[0]]


def test_busy_result_is_retried():
    client = ResultErrorClient(2, {"message": "database is locked", "code": "SQLITE_BUSY"})
    assert _tq(client, [{"sql": "SELECT 1"}], sleep=lambda _: None) == [{"rows": []}]
    assert client.calls == 3


def test_persistent_ioerr_gives_up_after_the_last_attempt():
    client = ResultErrorClient(TURSO_ATTEMPTS + 1, IOERR)
    with pytest.raises(TursoTransientError, match="SQLITE_IOERR"):
        _tq(client, [{"sql": "SELECT 1"}], sleep=lambda _: None)
    assert client.calls == TURSO_ATTEMPTS
