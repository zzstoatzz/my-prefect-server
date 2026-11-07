import json
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from pydantic import ValidationError

from flows import fly_observations as fly
from flows.evergreen_checks import Project
from flows.fleet_health import CheckResult, summarize

NOW = datetime(2026, 11, 7, 5, tzinfo=UTC)
APP = "zds-pds"
MACHINE = "9185de12b97dd8"


def event(**changes):
    exit_event = {"oom_killed": True, "requested_stop": False, "exit_code": 137, "restarting": True}
    exit_event.update(changes)
    return {
        "id": "01M6SRG6WS1MHXMKDJ9X28HQK5",
        "type": "exit",
        "timestamp": 1794013404057,
        "request": {"exit_event": exit_event},
    }


def memory(available="805306368", total="1073741824"):
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {
                    "metric": {
                        "__name__": "fly_instance_memory_mem_" + name,
                        "app": APP,
                        "instance": MACHINE,
                    },
                    "value": [NOW.timestamp(), value],
                }
                for name, value in [("available", available), ("total", total)]
            ],
        },
    }


@pytest.fixture
def api(monkeypatch):
    state = {
        "machines": [{"id": MACHINE, "state": "started"}],
        "events": [event()],
        "memory": memory(),
        "inventory": [{"name": "zds", "services": [], "resources": []}],
        "requests": [],
        "events_status": 200,
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            state["requests"].append((path, self.headers.get("authorization")))
            status = 200
            if path == f"/apps/{APP}/machines":
                body = state["machines"]
            elif path == f"/apps/{APP}/machines/{MACHINE}/events":
                body = state["events"]
                status = state["events_status"]
            elif path == "/metrics":
                body = state["memory"]
            elif path == "/inventory":
                body = state["inventory"]
            elif path == "/health":
                body = {"status": "ok"}
            else:
                body, status = {}, 404
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}/"
    monkeypatch.setattr(fly, "METRICS_API", base + "metrics")
    try:
        with httpx.Client(
            base_url=base, headers={"Authorization": "FlyV1 fixture"}, timeout=2
        ) as client:
            yield client, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_recovered_http_health_does_not_erase_an_oom(api):
    client, state = api
    assert client.get("health").status_code == 200
    (observation,) = fly.collect_app(client, "zds", APP, NOW)
    assert observation.state == "started"
    assert observation.available_mib == 768
    assert observation.available_percent == 75
    assert len(observation.recent_failures) == 1
    assert "OOM kill" in observation.recent_failures[0]
    assert "01M6SRG6WS1MHXMKDJ9X28HQK5" in observation.recent_failures[0]
    rows = fly.observation_rows([observation])
    assert any("OOM kill" in row for row in rows)
    assert any("Observation only" in row for row in rows)
    assert all(auth == "FlyV1 fixture" for _, auth in state["requests"])


def test_stopped_machine_keeps_failure_evidence_without_stale_memory(api):
    client, state = api
    state["machines"][0]["state"] = "stopped"
    (observation,) = fly.collect_app(client, "zds", APP, NOW)
    assert observation.available_mib is None
    assert observation.recent_failures
    assert "/metrics" not in [path for path, _ in state["requests"]]


def test_planned_stop_is_not_a_process_failure(api):
    client, state = api
    state["events"] = [event(oom_killed=False, requested_stop=True, exit_code=130)]
    (observation,) = fly.collect_app(client, "zds", APP, NOW)
    assert observation.recent_failures == []


@pytest.mark.parametrize("exit_code", [1, 134, 137])
def test_nonzero_exit_is_retained_without_inventing_an_oom(exit_code):
    parsed = fly.Event.model_validate(event(oom_killed=False, exit_code=exit_code))
    failures = fly.failure_events([parsed], NOW)
    assert len(failures) == 1
    assert f"process exit {exit_code}" in failures[0]
    assert "OOM" not in failures[0]


def test_old_failures_expire_and_future_events_are_not_trusted():
    parsed = fly.Event.model_validate(event())
    assert fly.failure_events([parsed], NOW + timedelta(days=2)) == []
    with pytest.raises(ValueError, match="future"):
        fly.failure_events([parsed], datetime(2026, 11, 6, tzinfo=UTC))


def test_missing_exit_evidence_is_unknown_not_clean(api):
    client, state = api
    state["events"][0]["request"] = {}
    with pytest.raises(ValueError, match="missing its exit details"):
        fly.collect_app(client, "zds", APP, NOW)


def test_events_failure_cannot_produce_a_clean_history(api):
    client, state = api
    state["events_status"] = 503
    with pytest.raises(httpx.HTTPStatusError):
        fly.collect_app(client, "zds", APP, NOW)
    assert any(
        "UNKNOWN" in row for row in fly.observation_rows(RuntimeError("provider unavailable"))
    )


def test_missing_memory_is_explicitly_unknown(api):
    client, state = api
    state["memory"]["data"]["result"] = []
    (observation,) = fly.collect_app(client, "zds", APP, NOW)
    assert observation.available_mib is None
    assert "current memory unknown" in observation.coverage
    assert any("unknown" in row for row in fly.observation_rows([observation]))


def test_absent_machine_does_not_claim_event_coverage(api):
    client, state = api
    state["machines"] = []
    (observation,) = fly.collect_app(client, "zds", APP, NOW)
    assert observation.state == "no machines"
    assert observation.available_mib is None
    assert "not queried" in observation.coverage
    assert len(state["requests"]) == 1


def test_zero_headroom_is_measured_not_missing():
    assert fly.memory_sample(fly.MetricResponse.model_validate(memory("0")), APP, MACHINE, NOW) == (
        0,
        0,
    )


@pytest.mark.parametrize(
    "available,total", [("NaN", "100"), ("-1", "100"), ("101", "100"), ("0", "0")]
)
def test_invalid_memory_is_not_headroom(available, total):
    with pytest.raises(ValueError):
        fly.memory_sample(
            fly.MetricResponse.model_validate(memory(available, total)), APP, MACHINE, NOW
        )


def test_stale_memory_and_wrong_machine_are_rejected():
    report = memory()
    with pytest.raises(ValueError, match="stale"):
        fly.memory_sample(
            fly.MetricResponse.model_validate(report), APP, MACHINE, NOW + timedelta(minutes=4)
        )
    with pytest.raises(ValueError, match="requested machine"):
        fly.memory_sample(fly.MetricResponse.model_validate(report), APP, "different", NOW)


def test_partial_provider_report_is_not_complete_coverage():
    with pytest.raises(ValidationError):
        fly.MetricResponse.model_validate({**memory(), "isPartial": True})


def test_no_opt_in_needs_no_provider_credential(api):
    client, _ = api
    assert fly.observe_fly.fn(inventory_url=str(client.base_url) + "inventory") == []


def test_targets_require_explicit_eligible_owned_resources():
    project = Project.model_validate(
        {
            "name": "zds",
            "services": [],
            "resources": [
                {"provider": "fly", "kind": "app", "name": APP, "monitoring": "observe"},
                {"provider": "fly", "kind": "app", "name": "staging"},
            ],
        }
    )
    assert fly.targets([project]) == [("zds", APP)]
    with pytest.raises(ValueError, match="unique"):
        fly.targets([project, project])
    with pytest.raises(ValidationError, match="Fly app"):
        Project.model_validate(
            {
                "name": "not-fly",
                "services": [],
                "resources": [
                    {
                        "provider": "home",
                        "kind": "machine",
                        "name": "host",
                        "monitoring": "observe",
                    },
                ],
            }
        )


def test_observations_do_not_become_availability_pages():
    _, unhealthy, broken = summarize([CheckResult("zds/PDS health", True, "HTTP 200")])
    assert not unhealthy and not broken
    assert fly.observation_rows([]) == []
    assert "UNKNOWN" in "\n".join(fly.observation_rows(RuntimeError("missing evidence")))
