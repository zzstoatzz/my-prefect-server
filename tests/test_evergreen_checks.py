import json
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

import flows.fleet_health as fleet
from flows.evergreen_checks import EvergreenStatus, Project, check_evergreen, validate_status
from flows.fleet_health import CheckResult, evergreen_result, summarize

NOW = datetime(2026, 9, 9, tzinfo=UTC)
PROJECTS = [
    Project(name="example", services=[{"name": "api", "url": "https://example.org/health"}])
]


def report(**changes):
    result = {
        "project": "example",
        "name": "api",
        "url": "https://example.org/health",
        "status": 200,
        "ok": True,
        "ms": 10,
    }
    result.update(changes)
    return EvergreenStatus(checkedAt=NOW, results=[result])


TRANSPORT_ERROR = {"category": "timeout", "message": "Probe timed out"}
FAILED_ATTEMPT = {"status": 0, "ok": False, "ms": 10000, "error": TRANSPORT_ERROR}
HEALTHY_ATTEMPT = {"status": 200, "ok": True, "ms": 20}
HTTP_FAILURE = {"status": 503, "ok": False, "ms": 30}


def recovered_report(**changes):
    diagnostics = {
        "ms": 11020,
        "attempt_count": 2,
        "attempts": [FAILED_ATTEMPT, HEALTHY_ATTEMPT],
    }
    diagnostics.update(changes)
    return report(**diagnostics)


@pytest.mark.parametrize("status,ok", [(200, True), (204, True), (503, False), (0, False)])
def test_legacy_reports_retain_health_semantics(status, ok):
    result = validate_status(PROJECTS, report(status=status, ok=ok), NOW)[0]
    rows, unhealthy, broken = summarize([evergreen_result(result)])
    assert bool(unhealthy) is not ok
    assert not broken
    assert ("probe transport failure" if status == 0 else f"HTTP {status}") in rows[-1]
    assert "HTTP 0" not in rows[-1]


def test_recovery_retains_first_failure_and_timings_without_unhealthy_finding():
    result = validate_status(PROJECTS, recovered_report(), NOW)[0]
    rows, unhealthy, broken = summarize([evergreen_result(result)])
    assert not unhealthy and not broken
    assert "recovered" in rows[-1]
    assert "total 11020 ms" in rows[-1]
    assert "2 attempt(s)" in rows[-1]
    assert "attempt 1: probe transport failure (timeout: Probe timed out), 10000 ms" in rows[-1]
    assert "attempt 2: HTTP 200, 20 ms" in rows[-1]


@pytest.mark.parametrize("first", [FAILED_ATTEMPT, HTTP_FAILURE])
def test_final_http_failure_is_not_a_transport_failure(first):
    result = validate_status(
        PROJECTS,
        recovered_report(status=503, ok=False, attempts=[first, HTTP_FAILURE]),
        NOW,
    )[0]
    rows, unhealthy, broken = summarize([evergreen_result(result)])
    assert unhealthy and not broken
    assert "HTTP 503" in rows[-1]
    assert "recovered" not in rows[-1]


def test_http_failure_can_recover_without_a_page():
    result = validate_status(
        PROJECTS,
        recovered_report(attempts=[HTTP_FAILURE, HEALTHY_ATTEMPT]),
        NOW,
    )[0]
    rows, unhealthy, broken = summarize([evergreen_result(result)])
    assert not unhealthy and not broken
    assert "recovered" in rows[-1] and "attempt 1: HTTP 503" in rows[-1]


def test_single_healthy_attempt_round_trip():
    status = report(attempt_count=1, attempts=[HEALTHY_ATTEMPT], ms=20)
    restored = EvergreenStatus.model_validate_json(status.model_dump_json())
    assert validate_status(PROJECTS, restored, NOW)[0].ok
    assert not summarize([evergreen_result(restored.results[0])])[1]


@pytest.mark.parametrize("error", [None, {"category": "network", "message": "different"}])
def test_final_transport_error_must_match_top_level(error):
    with pytest.raises(ValidationError, match="final attempt"):
        recovered_report(
            status=0,
            ok=False,
            ms=21000,
            error=error,
            attempts=[FAILED_ATTEMPT, FAILED_ATTEMPT],
        )


def test_exhausted_transport_failure_remains_visible_and_unhealthy():
    result = validate_status(
        PROJECTS,
        recovered_report(
            status=0,
            ok=False,
            ms=21000,
            error=TRANSPORT_ERROR,
            attempts=[FAILED_ATTEMPT, FAILED_ATTEMPT],
        ),
        NOW,
    )[0]
    rows, unhealthy, broken = summarize([evergreen_result(result)])
    assert unhealthy and not broken
    assert "attempt 2: probe transport failure" in rows[-1]
    assert "HTTP 0" not in rows[-1]


@pytest.mark.parametrize(
    "changes",
    [
        {"attempt_count": 1},
        {"attempt_count": True},
        {"attempt_count": "2"},
        {"attempt_count": 3},
        {"attempt_count": None},
        {"attempts": None},
        {"attempts": []},
        {"attempts": [FAILED_ATTEMPT]},
        {"attempts": [FAILED_ATTEMPT, HTTP_FAILURE]},
        {"attempts": [HEALTHY_ATTEMPT, HEALTHY_ATTEMPT]},
        {"attempts": [{**FAILED_ATTEMPT, "ok": True}, HEALTHY_ATTEMPT]},
        {"attempts": [{**FAILED_ATTEMPT, "error": None}, HEALTHY_ATTEMPT]},
        {"attempts": [{"status": 0, "ok": False, "ms": 10}, HEALTHY_ATTEMPT]},
        {"attempts": [FAILED_ATTEMPT, {**HEALTHY_ATTEMPT, "error": TRANSPORT_ERROR}]},
        {"attempts": [FAILED_ATTEMPT, {**HEALTHY_ATTEMPT, "ms": float("inf")}]},
        {"attempts": [FAILED_ATTEMPT, {**HEALTHY_ATTEMPT, "ms": -1}]},
        {"ms": 10},
        {"error": TRANSPORT_ERROR},
    ],
)
def test_contradictory_or_malformed_diagnostics_rejected(changes):
    with pytest.raises(ValidationError):
        recovered_report(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"attempt_count": 1},
        {"attempts": [HEALTHY_ATTEMPT]},
        {"error": TRANSPORT_ERROR},
        {"status": 0, "ok": False, "error": {"category": "dns", "message": "failure"}},
        {"status": 0, "ok": False, "error": {"category": "network", "message": "x" * 161}},
        {"status": 0, "ok": False, "error": {"category": "unknown", "message": ""}},
    ],
)
def test_partial_or_invalid_diagnostics_cannot_bypass_validation(changes):
    with pytest.raises(ValidationError):
        report(**changes)


@pytest.mark.parametrize("category", ["timeout", "network", "unknown"])
def test_single_attempt_transport_diagnostics(category):
    error = {"category": category, "message": "safe failure"}
    result = report(
        status=0,
        ok=False,
        error=error,
        attempt_count=1,
        attempts=[{**FAILED_ATTEMPT, "error": error}],
        ms=10000,
    ).results[0]
    assert category in evergreen_result(result).detail


def test_error_message_cannot_break_report_table():
    result = report(
        status=0,
        ok=False,
        error={
            "category": "unknown",
            "message": "failure | <script>\nnext",
        },
    ).results[0]
    detail = evergreen_result(result).detail
    assert "|" not in detail and "<script>" not in detail and "\n" not in detail


@pytest.mark.parametrize("status", [report(), recovered_report(), report(status=0, ok=False)])
def test_optional_diagnostics_round_trip(status):
    restored = EvergreenStatus.model_validate_json(status.model_dump_json())
    assert restored == status
    assert validate_status(PROJECTS, restored, NOW) == status.results


def test_null_optional_diagnostics_remain_legacy_compatible():
    status = report(attempt_count=None, attempts=None, error=None)
    assert validate_status(PROJECTS, status, NOW)[0].ok


def test_complete_healthy_report():
    assert len(validate_status(PROJECTS, report(), NOW)) == 1


@pytest.mark.parametrize("changes", [{"ok": False}, {"status": 503}, {"status": 0}])
def test_contradictory_verdict_is_a_broken_monitor(changes):
    with pytest.raises(ValueError, match="contradicts"):
        validate_status(PROJECTS, report(**changes), NOW)


def test_unhealthy_service_remains_a_finding_not_a_broken_sweep():
    results = validate_status(PROJECTS, report(ok=False, status=503), NOW)
    _, unhealthy, broken = summarize(
        [CheckResult(f"{r.project}/{r.name}", r.ok, f"HTTP {r.status}") for r in results]
    )
    assert unhealthy == ["example/api: HTTP 503"]
    assert not broken


def test_missing_service_is_not_healthy():
    projects = [
        *PROJECTS,
        Project(name="another", services=[{"name": "web", "url": "https://other.org"}]),
    ]
    with pytest.raises(ValueError, match="complete published inventory"):
        validate_status(projects, report(), NOW)


def test_duplicate_results_cannot_hide_missing_service():
    status = report()
    status.results.append(status.results[0])
    with pytest.raises(ValueError, match="complete published inventory"):
        validate_status(PROJECTS, status, NOW)


@pytest.mark.parametrize("seconds", [181, -31])
def test_stale_or_future_report(seconds):
    with pytest.raises(ValueError, match="timestamp"):
        validate_status(PROJECTS, report(), NOW + timedelta(seconds=seconds))


def test_naive_timestamp_rejected():
    status = report()
    status.checkedAt = NOW.replace(tzinfo=None)
    with pytest.raises(ValueError, match="timestamp"):
        validate_status(PROJECTS, status, NOW)


def test_empty_or_duplicate_inventory_rejected():
    for projects in ([], PROJECTS * 2):
        with pytest.raises(ValueError, match="inventory"):
            validate_status(projects, report(), NOW)


@pytest.mark.parametrize("changes", [{"ok": "true"}, {"status": "200"}, {"ms": float("nan")}])
def test_malformed_results_rejected(changes):
    with pytest.raises(ValidationError):
        report(**changes)


def test_renamed_service_is_inventory_drift():
    with pytest.raises(ValueError, match="complete published inventory"):
        validate_status(PROJECTS, report(name="different"), NOW)


def test_unmonitored_projects_are_allowed_without_claiming_health():
    projects = [*PROJECTS, Project(name="unmonitored", services=[])]
    assert len(validate_status(projects, report(), NOW)) == 1


@pytest.fixture
def monitor_server():
    payload = {
        "/inventory": [p.model_dump() for p in PROJECTS],
        "/status": report().model_dump(mode="json"),
    }
    payload["/status"]["checkedAt"] = datetime.now(UTC).isoformat()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(payload[self.path]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", payload
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_real_http_preserves_target_failure_and_detects_inventory_drift(monitor_server):
    url, payload = monitor_server
    payload["/status"]["results"][0].update(ok=False, status=503)
    results = check_evergreen.fn(f"{url}/inventory", f"{url}/status")
    assert len(results) == 1
    assert results[0].status == 503
    assert not results[0].ok
    payload["/inventory"][0]["services"].append(
        {"name": "new check", "url": "https://example.org/new"}
    )
    with pytest.raises(ValueError, match="complete published inventory"):
        check_evergreen.fn(f"{url}/inventory", f"{url}/status")


@pytest.mark.parametrize("final", [HEALTHY_ATTEMPT, HTTP_FAILURE, FAILED_ATTEMPT])
def test_flow_emits_pages_only_for_final_failure(monkeypatch, final):
    status = recovered_report(
        status=final["status"],
        ok=final["ok"],
        ms=21000,
        attempts=[FAILED_ATTEMPT, final],
        **({"error": final["error"]} if "error" in final else {}),
    )

    def future(value):
        return SimpleNamespace(result=lambda **kwargs: value)

    monkeypatch.setattr(
        fleet,
        "check_stream_deep",
        SimpleNamespace(
            submit=lambda: future(CheckResult("stream", True, "ok")),
        ),
    )
    monkeypatch.setattr(fleet, "SUPPLEMENTAL_CHECKS", [])
    monkeypatch.setattr(
        fleet,
        "check_evergreen",
        SimpleNamespace(
            submit=lambda: future(validate_status(PROJECTS, status, NOW)),
        ),
    )
    monkeypatch.setattr(fleet, "get_run_logger", Mock())
    artifact, event = Mock(), Mock()
    monkeypatch.setattr(fleet, "create_markdown_artifact", artifact)
    monkeypatch.setattr(fleet, "emit_event", event)
    fleet.fleet_health.fn()
    markdown = artifact.call_args.kwargs["markdown"]
    assert "attempt 1: probe transport failure" in markdown
    assert "HTTP 0" not in markdown
    if final["ok"]:
        event.assert_not_called()
        assert "recovered" in markdown
    else:
        event.assert_called_once()
        assert event.call_args.kwargs["event"] == "fleet-health.unhealthy"
        assert event.call_args.kwargs["payload"]["unhealthy"]


def test_real_http_reads_recovered_diagnostics(monitor_server):
    url, payload = monitor_server
    payload["/status"]["results"] = recovered_report().model_dump(
        mode="json",
        exclude_none=True,
    )["results"]
    result = check_evergreen.fn(f"{url}/inventory", f"{url}/status")[0]
    assert result.attempt_count == 2
    assert result.attempts[0].error.category == "timeout"
    assert not summarize([evergreen_result(result)])[1]


def test_real_http_rejects_stale_report(monitor_server):
    url, payload = monitor_server
    payload["/status"]["checkedAt"] = NOW.isoformat()
    with pytest.raises(ValueError, match="timestamp"):
        check_evergreen.fn(f"{url}/inventory", f"{url}/status")
