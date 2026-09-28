import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from mps import pull_comment_bridge as bridge
from mps.pull_comments import (
    PULL_PREFIX,
    REVISE_EVENT,
    created_before,
    relevant_comment,
    subscribe_url,
)
from mps.tangled import (
    DID as OPERATOR_DID,
    FEED_COMMENT_NSID,
    PHI_DID,
    comment_subject,
    comment_text,
)
from prefect.testing.utilities import prefect_test_harness

from flows import watch_tangled_pulls

PULL = f"{PULL_PREFIX}3abc"


def feed_event(
    rkey="3k1", subject=PULL, op="create", collection=None, time_us=1_800_000_000_000_000
):
    return {
        "did": OPERATOR_DID,
        "time_us": time_us,
        "commit": {
            "operation": op,
            "collection": collection or FEED_COMMENT_NSID,
            "rkey": rkey,
            "record": {
                "subject": {"uri": subject, "cid": "bafy"},
                "body": {"text": "please tighten this"},
                "createdAt": "2026-09-01T00:00:00Z",
            },
        },
    }


def test_relevant_comment_matches_gardener_pulls_only():
    assert relevant_comment(feed_event())["pull"] == PULL
    assert relevant_comment(feed_event(subject="at://did:plc:other/sh.tangled.repo.pull/x")) is None
    assert relevant_comment(feed_event(op="delete")) is None
    assert relevant_comment(feed_event(collection="app.bsky.feed.post")) is None


def test_comment_lexicon_normalization():
    feed = {"subject": {"uri": PULL, "cid": "x"}, "body": {"text": "hi"}}
    legacy = {"pull": PULL, "body": "hi"}
    assert comment_subject(feed) == comment_subject(legacy) == PULL
    assert comment_text(feed) == comment_text(legacy) == "hi"


def test_only_phi_change_requests_start_revisions():
    event = feed_event()
    event["did"] = PHI_DID
    for verdict in ("approve", "escalate"):
        event["commit"]["record"]["body"]["text"] = f"VERDICT: {verdict}"
        assert relevant_comment(event) is None
    event["commit"]["record"]["body"]["text"] = "VERDICT: request-changes"
    assert relevant_comment(event)["pull"] == PULL
    event["did"] = "did:plc:stranger"
    assert relevant_comment(event) is None


def test_subscribe_url_filters_reviewers_server_side():
    url = subscribe_url(123)
    assert f"wantedDids={OPERATOR_DID}" in url and f"wantedDids={PHI_DID}" in url
    assert f"wantedCollections={FEED_COMMENT_NSID}" in url
    assert url.endswith("&cursor=123")
    assert "cursor" not in subscribe_url(None)


def test_created_before_treats_unreadable_timestamps_as_old():
    now = datetime.now(UTC)
    assert created_before({"created_at": (now - timedelta(hours=1)).isoformat()}, now)
    assert not created_before({"created_at": now.isoformat()}, now - timedelta(minutes=1))
    assert created_before({"created_at": "not a date"}, now)
    assert created_before({"created_at": "2026-09-01T00:00:00"}, now)


def comment(rkey, created_at):
    event = feed_event(rkey=rkey)
    event["commit"]["record"]["createdAt"] = created_at
    return relevant_comment(event)


async def test_reconcile_recovers_missed_comments_once(monkeypatch):
    old = comment("3old", "2026-09-01T00:00:00Z")
    emitted = []
    monkeypatch.setattr(watch_tangled_pulls, "reconcile", lambda: [old])
    monkeypatch.setattr(watch_tangled_pulls, "emit_event", lambda **kw: emitted.append(kw))
    with prefect_test_harness():
        first = await watch_tangled_pulls.watch_tangled_pulls(return_state=True)
        second = await watch_tangled_pulls.watch_tangled_pulls(return_state=True)
    assert first.name == "Recovered" and old["uri"] in first.message
    assert second.name == "Completed"
    assert [e["event"] for e in emitted] == [REVISE_EVENT]
    assert emitted[0]["payload"]["pull"] == PULL


async def test_reconcile_leaves_fresh_comments_to_the_bridge(monkeypatch):
    fresh = comment("3new", datetime.now(UTC).isoformat())
    emitted = []
    monkeypatch.setattr(watch_tangled_pulls, "reconcile", lambda: [fresh])
    monkeypatch.setattr(watch_tangled_pulls, "emit_event", lambda **kw: emitted.append(kw))
    with prefect_test_harness():
        state = await watch_tangled_pulls.watch_tangled_pulls(return_state=True)
    assert state.name == "Completed"
    assert emitted == []


class FakeHandled:
    def __init__(self):
        self.uris: list[str] = []

    async def load(self):
        return list(self.uris)

    async def mark(self, uris):
        self.uris += uris


class FakeEvents:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def emit(self, event):
        if self.fail:
            raise OSError("prefect api down")
        self.sent.append(event)


@pytest.fixture
def handled(monkeypatch):
    store = FakeHandled()
    monkeypatch.setattr(bridge, "load_handled", store.load)
    monkeypatch.setattr(bridge, "mark_handled", store.mark)
    return store


async def test_bridge_emits_once_and_advances_cursor(monkeypatch, tmp_path, handled):
    events = FakeEvents()
    monkeypatch.setattr(bridge, "get_events_client", events)
    state, status = tmp_path / "cursor", bridge.Status()

    raw = json.dumps(feed_event(time_us=42))
    await bridge.handle_message(raw, status, state)
    await bridge.handle_message(raw, status, state)

    assert [e.event for e in events.sent] == [REVISE_EVENT]
    assert events.sent[0].payload["pull"] == PULL
    assert handled.uris == [relevant_comment(feed_event())["uri"]]
    assert bridge.read_cursor(state) == 42 and status.emitted == 1


async def test_bridge_does_not_advance_past_an_undelivered_comment(monkeypatch, tmp_path, handled):
    monkeypatch.setattr(bridge, "get_events_client", FakeEvents(fail=True))
    state, status = tmp_path / "cursor", bridge.Status()
    bridge.write_cursor(state, 7)

    with pytest.raises(OSError):
        await bridge.handle_message(json.dumps(feed_event(time_us=42)), status, state)

    assert bridge.read_cursor(state) == 7
    assert handled.uris == []


async def test_bridge_advances_cursor_on_irrelevant_events(tmp_path, handled):
    state = tmp_path / "cursor"
    await bridge.handle_message(
        json.dumps(feed_event(op="delete", time_us=99)), bridge.Status(), state
    )
    assert bridge.read_cursor(state) == 99


def test_health_goes_stale_only_after_a_sustained_disconnect():
    status = bridge.Status()
    assert status.healthy(status.disconnected_since + bridge.STALE_AFTER_S - 1)
    assert not status.healthy(status.disconnected_since + bridge.STALE_AFTER_S + 1)
    status.connected()
    assert status.healthy(status.connected_since + 10 * bridge.STALE_AFTER_S)
    status.disconnected("stream closed")
    assert status.last_error == "stream closed"
    assert not status.healthy(status.disconnected_since + bridge.STALE_AFTER_S + 1)


async def test_health_endpoint_reports_503_when_stale():
    status = bridge.Status()
    status.disconnected_since -= bridge.STALE_AFTER_S + 1
    server = await bridge.serve_health(status, 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET /health HTTP/1.1\r\n\r\n")
        await writer.drain()
        response = await reader.read()
        writer.close()
    head, body = response.split(b"\r\n\r\n", 1)
    assert head.startswith(b"HTTP/1.1 503")
    assert json.loads(body)["connected_since"] is None
