from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastmcp import Client
from fastmcp_events import EventsClientExtension, list_events, poll_events_mcp
from mps.alerts.recovery import EVENT_NAME, RESOURCE_ID
from mps.alerts.server import TailscaleIdentity, create_app, create_recovery_server
from starlette.testclient import TestClient

from flows import recovery_events
from flows.evergreen_checks import Finding
from flows.fly_observations import CELLAR_APP, Event, Machine, Observation, exhausted_restart


def test_restart_exhaustion_requires_terminal_automatic_failure():
    now = datetime.now(UTC)
    machine = Machine(
        id="123abc",
        state="stopped",
        config={"restart": {"policy": "on-failure", "max_retries": 10}},
    )
    data = {
        "id": "exit1",
        "type": "exit",
        "source": "flyd",
        "status": "stopped",
        "timestamp": int(now.timestamp() * 1000),
        "request": {
            "restart_count": 10,
            "exit_event": {"exit_code": 7},
        },
    }
    event = Event.model_validate(data)
    assert exhausted_restart(machine, [event], now) == event
    assert exhausted_restart(machine.model_copy(update={"state": "started"}), [event], now) is None
    for changes in ({"requested_stop": True}, {"restarting": True}, {"exit_code": 0}):
        event_data = {**data["request"]["exit_event"], **changes}
        rejected = Event.model_validate(
            {**data, "request": {**data["request"], "exit_event": event_data}}
        )
        assert exhausted_restart(machine, [rejected], now) is None
    rejected = Event.model_validate({**data, "request": {**data["request"], "restart_count": 9}})
    assert exhausted_restart(machine, [rejected], now) is None
    assert exhausted_restart(machine, [event], now + timedelta(days=2)) is None
    newer = Event.model_validate(
        {**data, "id": "start", "type": "start", "timestamp": event.timestamp + 1}
    )
    assert exhausted_restart(machine, [event, newer], now) is None


def test_publish_requires_a_failed_probe_after_the_restart_and_keeps_event_identity(monkeypatch):
    failed_at = datetime.now(UTC) - timedelta(minutes=1)
    event = Event.model_validate(
        {
            "id": "exit1",
            "type": "exit",
            "timestamp": int(failed_at.timestamp() * 1000),
            "request": {"restart_count": 10, "exit_event": {"oom_killed": True}},
        }
    )
    machine = Observation(
        project="cellar",
        app=CELLAR_APP,
        machine="123abc",
        state="stopped",
        checked_at=datetime.now(UTC),
        exhausted_restart=event,
    )
    seen = []
    monkeypatch.setattr(recovery_events, "emit_event", lambda **event: seen.append(event))
    monkeypatch.setattr(recovery_events, "run_link", lambda: "")
    recovery_events.publish_exhausted_recovery(
        [Finding("pollz", "pollz backend", True, "OK")], [machine]
    )
    recovery_events.publish_exhausted_recovery(
        [Finding("pollz", "pollz backend", False, "HTTP 503", failed_at - timedelta(minutes=1))],
        [machine],
    )
    assert seen == []
    failure = Finding("pollz", "pollz backend", False, "HTTP 503")
    recovery_events.publish_exhausted_recovery([failure], [machine, machine])
    assert seen == []
    recovery_events.publish_exhausted_recovery([failure], [machine])
    recovery_events.publish_exhausted_recovery([failure], [machine])
    assert len(seen) == 2 and seen[0]["id"] == seen[1]["id"]
    assert seen[0]["event"] == EVENT_NAME and seen[0]["payload"]["attempts"] == 10


class Events:
    def __init__(self):
        self.rows = []

    def events(self, filter_):
        assert filter_["event"] == {"name": [EVENT_NAME]}
        assert filter_["resource"] == {"id": [RESOURCE_ID]}
        return self.rows, False


def row(at):
    return {
        "id": str(uuid4()),
        "event": EVENT_NAME,
        "resource": {"prefect.resource.id": RESOURCE_ID},
        "occurred": at.isoformat(),
        "payload": {
            "machine": "123abc",
            "attempts": 10,
            "oom_killed": True,
            "failed_at": at.isoformat(),
        },
    }


def events_client(source):
    return Client(create_recovery_server(source), extensions=[EventsClientExtension()], mode="auto")


@pytest.mark.asyncio
async def test_cursor_replays_after_restart_including_older_rows_that_became_visible_late():
    source = Events()
    async with events_client(source) as client:
        (event,) = await list_events(client)
        assert event.name == EVENT_NAME and event.delivery == ["poll"]
        start = await poll_events_mcp(client, EVENT_NAME)
        assert not start.events
    now = datetime.now(UTC) - timedelta(seconds=1)
    newer = row(now)
    older = row(now - timedelta(seconds=1))
    # Both occurrences follow subscription time and are visible in the past relative to polling.
    start.cursor = f'{{"since":"{(now - timedelta(seconds=2)).isoformat()}","seen":[]}}'
    source.rows = [newer]
    async with events_client(source) as client:
        first = await poll_events_mcp(client, EVENT_NAME, cursor=start.cursor)
        assert [event.event_id for event in first.events] == [newer["id"]]
    source.rows = [newer, older, newer]
    async with events_client(source) as client:
        late = await poll_events_mcp(client, EVENT_NAME, cursor=first.cursor)
        assert [event.event_id for event in late.events] == [older["id"]]
        done = await poll_events_mcp(client, EVENT_NAME, cursor=late.cursor)
        assert not done.events


def test_private_path_needs_both_tailnet_identity_and_phi_token(monkeypatch):
    allowed = True

    async def peer_login(*_):
        return "operator" if allowed else None

    monkeypatch.setattr(TailscaleIdentity, "peer_login", peer_login)
    token = "t" * 32
    app = create_app(Events(), "operator", "100.96.216.23", recovery_token=token)
    with TestClient(app, base_url="http://100.96.216.23") as client:
        assert client.get("/phi/mcp").status_code == 401
        assert client.get("/phi/mcp", headers={"authorization": "Bearer wrong"}).status_code == 401
        assert client.get("/mcp").status_code != 401
        assert client.get(
            "/phi/mcp", headers={"authorization": f"Bearer {token}"}
        ).status_code not in (401, 403, 404)
        allowed = False
        assert (
            client.get("/phi/mcp", headers={"authorization": f"Bearer {token}"}).status_code == 403
        )
