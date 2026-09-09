"""Regression tests for fleet-health failure semantics.

On 2026-08-13 fleet-health failed every run for hours because stream's
compaction watermark was legitimately behind — but the sweep itself ran
fine. A health checker that successfully checks the fleet has done its job;
a failed flow run must mean the *sweep* could not run.

What we test:
  - unhealthy findings are reported (rows + findings list) but do not land
    in the group that fails the flow
  - a check task that itself errored (non-CheckResult) does fail the flow
"""

from flows.fleet_health import CheckResult, summarize


def test_unhealthy_finding_is_reported_but_does_not_break_the_sweep():
    rows, unhealthy, broken = summarize(
        [
            CheckResult("stream (deep)", False, "compaction watermark lag 56.6h (> 48h)"),
            CheckResult("hub", True, "HTTP 200"),
        ]
    )
    assert unhealthy == ["stream (deep): compaction watermark lag 56.6h (> 48h)"]
    assert broken == []
    assert any("**DOWN**" in r for r in rows)


def test_errored_check_task_marks_the_sweep_broken():
    boom = RuntimeError("TimeoutError: check never came back")
    rows, unhealthy, broken = summarize([boom, CheckResult("hub", True, "HTTP 200")])
    assert unhealthy == []
    assert broken == [str(boom)]
    assert any("(task error)" in r for r in rows)


def test_all_healthy_yields_nothing_to_raise_or_page():
    _, unhealthy, broken = summarize([CheckResult("hub", True, "HTTP 200")])
    assert unhealthy == []
    assert broken == []


def test_network_retries_finish_before_endpoint_failure_is_classified():
    import httpx
    from mps.inventory import EndpointCheck

    from flows.fleet_health import check_endpoint, endpoint_finding

    endpoint = EndpointCheck(name="api", url="https://example.org", href="https://example.org")
    request = httpx.Request("GET", endpoint.url)
    error = httpx.HTTPStatusError(
        "unavailable", request=request, response=httpx.Response(503, request=request)
    )
    finding = endpoint_finding("example", endpoint, error)
    assert check_endpoint.retries == 3
    assert not finding.healthy
    assert finding.status == 503
    assert finding.project == "example"
    assert finding.kind == "endpoint"


def test_shared_inventory_has_unique_endpoints_and_new_projects():
    from mps.inventory import load_projects

    projects = load_projects()
    assert {"rally", "doodl", "stream", "zds", "birds.place"} <= {p.name for p in projects}
    urls = [s.url for p in projects for s in p.services]
    assert len(set(urls)) == len(urls)
