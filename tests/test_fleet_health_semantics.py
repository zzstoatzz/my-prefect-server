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

from flows.evergreen_checks import STATUS_URL
from flows.fleet_health import (
    EVERGREEN,
    BrokenCheck,
    CheckResult,
    _named,
    broken_sweep_message,
    summarize,
    unhealthy_payload,
)


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
    boom = _named("hub", RuntimeError("TimeoutError: check never came back"))
    rows, unhealthy, broken = summarize([boom, CheckResult("relay-eval", True, "HTTP 200")])
    assert unhealthy == []
    assert broken == [BrokenCheck("hub", "TimeoutError: check never came back")]
    assert "| hub | **CHECK ERRORED** | TimeoutError: check never came back |" in rows


def test_broken_sweep_message_names_the_check_and_the_next_step():
    message = broken_sweep_message(
        [BrokenCheck(EVERGREEN, "Evergreen's results do not match its published inventory: x")]
    )
    assert message.startswith("Sweep incomplete: 1 check(s) errored")
    assert "Evergreen inventory errored: Evergreen's results do not match" in message
    assert f"Next: compare Evergreen's live results ({STATUS_URL})" in message
    assert "RuntimeError" not in message


def test_broken_sweep_message_falls_back_for_unknown_checks():
    message = broken_sweep_message([BrokenCheck("hub", "boom")])
    assert "hub errored: boom" in message
    assert "Next: open the run below" in message
    assert len(broken_sweep_message([BrokenCheck("hub", "x" * 5000)])) <= 1600


def test_exception_without_text_still_names_its_type():
    assert _named("hub", TimeoutError()) == BrokenCheck("hub", "TimeoutError")


def test_all_healthy_yields_nothing_to_raise_or_page():
    _, unhealthy, broken = summarize([CheckResult("hub", True, "HTTP 200")])
    assert unhealthy == []
    assert broken == []


def test_unhealthy_event_carries_the_summary_the_discord_automation_renders():
    payload = unhealthy_payload(["stream (deep): tail stalled", "hub: HTTP 503"])
    assert payload["unhealthy"] == ["stream (deep): tail stalled", "hub: HTTP 503"]
    assert payload["summary"] == "stream (deep): tail stalled\nhub: HTTP 503"
    assert len(unhealthy_payload(["x" * 5000])["summary"]) <= 2000


def test_unhealthy_summary_links_the_run_inside_a_flow_run(monkeypatch):
    from prefect.settings import PREFECT_UI_URL, temporary_settings

    monkeypatch.setenv("PREFECT__RUNTIME__FLOW_RUN__ID", "948b0104-40f8-479e-9803-08199c870857")
    with temporary_settings({PREFECT_UI_URL: "https://prefect-server.waow.tech"}):
        summary = unhealthy_payload(["hub: HTTP 503", "y" * 5000])["summary"]
    assert summary.endswith(
        "\nrun: https://prefect-server.waow.tech/runs/flow-run/948b0104-40f8-479e-9803-08199c870857"
    )
    assert len(summary) <= 1800


def test_unhealthy_summary_uses_the_runtime_ui(monkeypatch):
    from prefect.settings import PREFECT_UI_URL, temporary_settings

    monkeypatch.setenv("PREFECT__RUNTIME__FLOW_RUN__ID", "948b0104-40f8-479e-9803-08199c870857")
    with temporary_settings({PREFECT_UI_URL: "https://heavypad.tailb660b6.ts.net:10443/"}):
        summary = unhealthy_payload(["example: HTTP 503"])["summary"]
    assert summary.endswith(
        "\nrun: https://heavypad.tailb660b6.ts.net:10443/runs/flow-run/948b0104-40f8-479e-9803-08199c870857"
    )
