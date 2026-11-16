import pytest

from scripts.register_heavypad_main import ci_passed


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ({"check.yml": "success", "integration.yml": "success"}, True),
        ({"check.yml": "success", "integration.yml": "running"}, False),
        ({"check.yml": "failed", "integration.yml": "success"}, False),
        ({"integration.yml": "success"}, False),
    ],
)
def test_registration_requires_both_workflows(statuses, expected):
    workflows = [{"name": name, "status": status} for name, status in statuses.items()]
    assert ci_passed(workflows) is expected
