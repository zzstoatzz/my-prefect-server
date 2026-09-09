from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from flows.evergreen_health import EvergreenStatus, Project, validate_status

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
def test_failed_service_names_the_affected_endpoint(changes):
    with pytest.raises(RuntimeError, match="example/api: HTTP"):
        validate_status(PROJECTS, report(**changes), NOW)


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
