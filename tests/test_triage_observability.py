import pytest

from flows.triage_observability import TriageReport, observe_triage


def test_run_and_thread_records_are_correlated(capfire):
    report = TriageReport("run-123", "https://prefect.example/run-123", True)
    with observe_triage(report):
        report.candidates = 2
        report.skipped = 1
        report.triaged = 1
        report.prs = 1
        report.cost_usd = 0.5
        report.status = "completed"
        report.thread(5287, "triaged", pr_url="https://github.com/PrefectHQ/fastmcp/pull/5291")
    spans = capfire.exporter.exported_spans_as_dict()
    assert len(spans) == 3
    assert all(s["attributes"]["run_id"] == "run-123" for s in spans)
    summary = spans[-1]["attributes"]
    assert summary["status"] == "completed"
    assert summary["duration_seconds"] >= 0
    assert summary["prs"] == 1
    assert summary["cost_usd"] == 0.5


def test_failure_emits_summary_without_swallowing_or_exporting_exception_text(capfire):
    with (
        pytest.raises(RuntimeError, match="private output"),
        observe_triage(TriageReport("run-456", "", False)),
    ):
        raise RuntimeError("private output from an agent")
    spans = capfire.exporter.exported_spans_as_dict()
    summary = spans[-1]["attributes"]
    assert summary["status"] == "failed"
    assert summary["error_type"] == "RuntimeError"
    assert "private output" not in str(spans)


@pytest.mark.parametrize("status", ["deferred", "noop", "degraded"])
def test_nonfailure_outcomes_remain_distinct(capfire, status):
    with observe_triage(TriageReport("run-789", "", False)) as report:
        report.status = status
    summary = capfire.exporter.exported_spans_as_dict()[-1]["attributes"]
    assert summary["status"] == status
    assert summary["error_type"] == ""
