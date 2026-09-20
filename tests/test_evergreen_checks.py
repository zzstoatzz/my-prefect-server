import json
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from pydantic import ValidationError

from flows.evergreen_checks import EvergreenStatus, Project, check_evergreen, validate_status
from flows.fleet_health import CheckResult, summarize

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


def test_real_http_rejects_stale_report(monitor_server):
    url, payload = monitor_server
    payload["/status"]["checkedAt"] = NOW.isoformat()
    with pytest.raises(ValueError, match="timestamp"):
        check_evergreen.fn(f"{url}/inventory", f"{url}/status")
