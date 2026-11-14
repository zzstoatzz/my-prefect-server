from datetime import UTC, datetime, timedelta

import httpx
import pytest

from flows import zds_follow
from flows.evergreen_checks import Project
from flows.zds_follow import SOAK, Follow, canary, notice

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)
REAL_CLIENT = httpx.Client
INVENTORY = [
    {
        "name": "zds",
        "services": [{"name": "PDS health", "url": "https://canary.test/xrpc/_health"}],
        "resources": [{"provider": "fly", "kind": "app", "name": "canary-app"}],
    },
    {"name": "other", "services": [{"name": "x", "url": "https://other.test/health"}]},
]


def follow(operator="0.12.3", canary_version="0.13.0", soaked=True) -> Follow:
    since = NOW - (SOAK + timedelta(hours=2) if soaked else SOAK - timedelta(hours=1))
    return Follow("zds.example", operator, canary_version, since)


def test_a_soaked_newer_canary_release_asks_to_roll():
    message = notice(follow(), NOW, notified=None)
    assert message is not None
    assert "zds 0.13.0 for 26h" in message
    assert "zds.example still runs 0.12.3" in message
    assert "just zds-upgrade 0.13.0" in message


@pytest.mark.parametrize(
    ("operator", "canary_version", "soaked", "notified"),
    [
        ("0.13.0", "0.13.0", True, None),  # current
        ("0.13.1", "0.13.0", True, None),  # ahead of the canary
        ("0.12.3", "0.13.0", False, None),  # canary has not soaked
        ("0.12.3", "0.13.0", True, "0.13.0"),  # already asked about this release
        ("0.4.5051", None, True, None),  # canary unreachable
        ("bluesky", "0.13.0", True, None),  # operator PDS is not a zds release
    ],
)
def test_no_notice(operator, canary_version, soaked, notified):
    assert notice(follow(operator, canary_version, soaked), NOW, notified) is None


def test_versions_compare_numerically_not_as_text():
    assert notice(follow("0.9.0", "0.10.0"), NOW, None) is not None


def test_canary_comes_from_the_inventory():
    projects = [Project.model_validate(p) for p in INVENTORY]
    assert canary(projects) == ("https://canary.test", "canary-app")


def test_a_missing_canary_project_is_an_error_not_a_silent_skip():
    projects = [Project.model_validate(INVENTORY[1])]
    with pytest.raises(ValueError, match="no 'zds' project"):
        canary(projects)


class Store:
    def __init__(self, value=None):
        self.value = value

    def get(self, name, default=None):
        return self.value if self.value is not None else default

    def set(self, name, value, overwrite=False):
        self.value = value


def run(monkeypatch, store: Store, operator_version="0.12.3"):
    versions = {"https://zds.example": operator_version, "https://canary.test": "0.13.0"}
    resolved, events = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/services.json":
            return httpx.Response(200, json=INVENTORY)
        base = f"{request.url.scheme}://{request.url.host}"
        return httpx.Response(200, json={"version": versions[base]})

    monkeypatch.setattr(
        zds_follow.httpx,
        "Client",
        lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw),
    )
    monkeypatch.setattr(
        zds_follow, "resolve_pds", lambda did: resolved.append(did) or "https://zds.example"
    )
    monkeypatch.setattr(zds_follow, "_running_since", lambda app: NOW - SOAK * 2)
    monkeypatch.setattr(
        zds_follow, "datetime", type("dt", (), {"now": staticmethod(lambda tz: NOW)})
    )
    monkeypatch.setattr(zds_follow, "Variable", store)
    monkeypatch.setattr(zds_follow, "emit_event", lambda **kw: events.append(kw))
    result = zds_follow.zds_follow.fn("https://inventory.test/services.json")
    return result, resolved, events


def test_the_operator_pds_is_resolved_from_the_did(monkeypatch):
    result, resolved, _ = run(monkeypatch, Store())
    assert resolved == [zds_follow.OPERATOR_DID]
    assert result.operator == "zds.example"


def test_one_notice_per_release(monkeypatch):
    store = Store()
    _, _, events = run(monkeypatch, store)
    assert [e["event"] for e in events] == ["zds.behind"]
    assert events[0]["payload"]["version"] == "0.13.0"
    assert store.value == "0.13.0"

    _, _, events = run(monkeypatch, store)
    assert events == [], "a release already asked about must not ask again every sweep"


def test_no_notice_once_the_operator_has_rolled(monkeypatch):
    result, _, events = run(monkeypatch, Store(), operator_version="0.13.0")
    assert events == []
    assert result.notice is None
    assert "ok" in result.row()


def test_rolling_closes_the_alert_incident(monkeypatch):
    store = Store("0.13.0")
    _, _, events = run(monkeypatch, store, operator_version="0.13.0")
    assert [e["event"] for e in events] == ["zds.recovered"]
    assert events[0]["resource"] == {"prefect.resource.id": "zds-follow"}
    assert store.value == ""

    _, _, events = run(monkeypatch, store, operator_version="0.13.0")
    assert events == [], "recovery is sent once"
