import datetime as dt

from mps.costs.coverage import Project, reconcile
from mps.costs.types import LineItem, Period, Snapshot


def test_declarations_override_old_names_without_losing_or_double_counting_costs():
    projects = [
        Project(name="find-bufo", costKeys=["fly:bufo"], resources=[]),
        Project(name="other", costKeys=["fly:duplicate"], resources=[]),
        Project(name="conflict", costKeys=["fly:duplicate"], resources=[]),
    ]
    items = [
        LineItem(provider="fly", project="old", service=service, amount=100, estimated=True)
        for service in ["bufo:compute", "bufo-other:compute", "duplicate:compute"]
    ]
    result, issues = reconcile(items, projects)
    assert [i.project for i in result] == ["find-bufo", "unattributed", "unattributed"]
    assert sum(i.amount for i in result) == 300
    assert "fly:bufo-other:compute: unassigned resource" in issues
    assert "fly:duplicate:compute: conflicting ownership" in issues


def test_missing_provider_is_not_free_and_shared_cost_is_not_divided():
    projects = [
        Project(
            name="app",
            costKeys=[],
            resources=[
                {"provider": "voyage"},
                {"provider": "wisp"},
                {"provider": "retired", "state": "retired"},
            ],
        )
    ]
    result, issues = reconcile(
        [LineItem(provider="turso", project="shared", service="plan", amount=2900, estimated=True)],
        projects,
    )
    assert result[0].project == "shared"
    assert "voyage: no amount collected; not verified free" in issues
    assert not any(s.startswith(("wisp:", "retired:")) for s in issues)
    snapshot = Snapshot(
        generated_at=dt.datetime.now(dt.UTC),
        period=Period.trailing_month(),
        line_items=result,
        coverage_issues=issues,
    )
    assert snapshot.to_record()["coverageIssues"] == issues
    assert snapshot.to_record()["total"] == 2900
