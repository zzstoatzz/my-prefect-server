import json
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from pydantic import ValidationError

from flows import evergreen_checks
from flows.evergreen_checks import (
    EvergreenStatus,
    Project,
    Service,
    check_evergreen,
    confirm,
    probe,
    validate_status,
)
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
    with pytest.raises(
        ValueError,
        match=r"no result for 1 inventory endpoint\(s\): another/web \(https://other.org\)",
    ):
        validate_status(projects, report(), NOW)


def test_duplicate_results_cannot_hide_missing_service():
    status = report()
    status.results.append(status.results[0])
    with pytest.raises(ValueError, match=r"1 duplicate result\(s\)"):
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
    with pytest.raises(ValueError, match=r"different project/name: example/different"):
        validate_status(PROJECTS, report(name="different"), NOW)


def test_mismatch_lists_extra_results_and_caps_long_lists():
    projects = [
        Project(
            name="big",
            services=[{"name": f"s{i}", "url": f"https://big.org/{i}"} for i in range(8)],
        )
    ]
    with pytest.raises(ValueError) as exc:
        validate_status(projects, report(), NOW)
    message = str(exc.value)
    assert "no result for 8 inventory endpoint(s)" in message
    assert "and 3 more" in message
    assert "1 result(s) not in the inventory: example/api (https://example.org/health)" in message


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
            self.send_response(payload.get("status", 200))
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            payload["posted"] = (
                self.headers["Accept"],
                self.rfile.read(int(self.headers["Content-Length"])).decode(),
            )
            self.send_response(204)
            self.end_headers()

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


def test_real_http_preserves_target_failure_and_detects_inventory_drift(
    monitor_server, monkeypatch
):
    url, payload = monitor_server
    payload["/status"]["results"][0].update(ok=False, status=503)
    monkeypatch.setattr(evergreen_checks, "probe", lambda client, service: "HTTP 503")
    results = check_evergreen.fn(f"{url}/inventory", f"{url}/status")
    assert len(results) == 1
    assert not results[0].healthy
    assert results[0].detail == (
        "HTTP 503 from Evergreen's Worker; HTTP 503 from heavypad (https://example.org/health)"
    )
    payload["/inventory"][0]["services"].append(
        {"name": "new check", "url": "https://example.org/new"}
    )
    with pytest.raises(
        ValueError, match=r"no result for 1 inventory endpoint\(s\): example/new check"
    ):
        check_evergreen.fn(f"{url}/inventory", f"{url}/status")


def test_real_http_rejects_stale_report(monitor_server):
    url, payload = monitor_server
    payload["/status"]["checkedAt"] = NOW.isoformat()
    with pytest.raises(ValueError, match="timestamp"):
        check_evergreen.fn(f"{url}/inventory", f"{url}/status")


def unreachable(service):
    raise AssertionError(f"probed {service.url}")


def test_a_healthy_result_is_not_probed_again():
    [finding] = confirm(PROJECTS, report().results, unreachable)
    assert finding.healthy
    assert finding.detail == "HTTP 200 (https://example.org/health)"


def test_a_failure_only_the_worker_saw_is_not_an_outage():
    dropped = report(
        ok=False,
        status=0,
        attempts=2,
        error="TimeoutError: The operation was aborted due to timeout",
    )
    [finding] = confirm(PROJECTS, dropped.results, lambda service: "HTTP 200")
    assert finding.healthy
    assert finding.detail == (
        "HTTP 200 from heavypad; Evergreen's Worker saw HTTP 0"
        " (TimeoutError: The operation was aborted due to timeout)"
        " (https://example.org/health)"
    )


@pytest.mark.parametrize("here", ["HTTP 503", "HTTP 302", "ConnectTimeout"])
def test_a_failure_seen_from_both_places_is_down_and_keeps_both_samples(here):
    [finding] = confirm(PROJECTS, report(ok=False, status=0).results, lambda service: here)
    assert not finding.healthy
    assert finding.detail == (
        f"HTTP 0 from Evergreen's Worker; {here} from heavypad (https://example.org/health)"
    )
    _, unhealthy, broken = summarize(
        [CheckResult(f"{finding.project}/{finding.name}", finding.healthy, finding.detail)]
    )
    assert unhealthy == [f"example/api: {finding.detail}"]
    assert not broken


def test_the_confirming_probe_uses_the_inventory_check():
    post = {"method": "POST", "headers": {"Accept": "text/event-stream"}, "body": "{}"}
    projects = [
        Project(
            name="example",
            services=[{"name": "api", "url": "https://example.org/health", "check": post}],
        )
    ]
    probed = []
    confirm(
        projects, report(ok=False, status=503).results, lambda s: probed.append(s) or "HTTP 200"
    )
    assert [s.check.model_dump() for s in probed] == [post]


def test_probe_reports_the_status_and_sends_the_check(monitor_server):
    url, payload = monitor_server
    with httpx.Client(timeout=5) as client:
        get = Service.model_construct(
            name="api", url=f"{url}/status", check=evergreen_checks.Check()
        )
        assert probe(client, get) == "HTTP 200"
        payload["status"] = 503
        assert probe(client, get) == "HTTP 503"
        post = Service.model_construct(
            name="mcp",
            url=f"{url}/mcp",
            check=evergreen_checks.Check(
                method="POST", headers={"Accept": "text/event-stream"}, body="{}"
            ),
        )
        assert probe(client, post) == "HTTP 204"
        assert payload["posted"] == ("text/event-stream", "{}")


def test_probe_reports_a_refused_connection_as_its_error():
    refused = Service.model_construct(
        name="api", url="http://127.0.0.1:1/", check=evergreen_checks.Check()
    )
    with httpx.Client(timeout=5) as client:
        assert probe(client, refused).startswith("ConnectError")
