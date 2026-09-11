from datetime import UTC, datetime, timedelta

import pytest
from mps.presence import Presence, proposed_action
from pydantic import ValidationError


@pytest.mark.parametrize(
    "state,seconds_old,expected",
    [
        ("home", 0, "ARRIVAL_LOOK"),
        ("away", 0, "LIGHTS_OFF"),
        ("unknown", 0, "NO_CHANGE"),
        ("away", 301, "LIGHTS_OFF"),
        ("home", 86400, "ARRIVAL_LOOK"),
        ("away", 86400, "LIGHTS_OFF"),
        ("home", -1, "NO_CHANGE"),
    ],
)
def test_presence_policy(state, seconds_old, expected):
    now = datetime.now(UTC)
    presence = Presence(state=state, observedAt=now - timedelta(seconds=seconds_old))
    assert proposed_action(presence, now) == expected


def test_presence_requires_timezone():
    with pytest.raises(ValidationError):
        Presence(state="home", observedAt="2026-09-05T12:00:00")


def test_presence_update_rejects_coordinates():
    from mps.presence import PresenceUpdate

    with pytest.raises(ValidationError):
        PresenceUpdate.model_validate(
            {"state": "home", "observedAt": "2026-09-05T12:00:00Z", "latitude": 41.8}
        )


@pytest.mark.parametrize("seconds_newer,expected_writes", [(-60, 0), (0, 0), (60, 1)])
def test_update_orders_reports_and_verifies_readback(seconds_newer, expected_writes):
    import json

    import httpx
    from mps.presence import COLLECTION, OWNER, SPACE, PresenceUpdate, apply_presence_update

    at = datetime(2026, 9, 5, 12, tzinfo=UTC)
    value = {"$type": COLLECTION, "state": "away", "observedAt": at.isoformat()}
    writes = []

    def handle(request):
        nonlocal value
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["space"] == SPACE
            assert body["repo"] == OWNER
            assert body["collection"] == COLLECTION
            assert body["rkey"] == "self"
            value = body["record"]
            writes.append(value)
        return httpx.Response(
            200,
            json={"uri": f"{SPACE}/{OWNER}/{COLLECTION}/self", "cid": "test-cid", "value": value},
        )

    with httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(handle)) as c:
        report = PresenceUpdate(state="home", observedAt=at + timedelta(seconds=seconds_newer))
        result = apply_presence_update(c, report, at + timedelta(minutes=2))
    assert len(writes) == expected_writes
    assert result.value.state == ("home" if expected_writes else "away")
