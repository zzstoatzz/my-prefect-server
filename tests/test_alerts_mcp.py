import json
from pathlib import Path

import httpx
import pytest
import yaml
from fastmcp import Client
from fastmcp.exceptions import ToolError
from mps.alerts.model import NOTIFICATIONS, incidents, key_of
from mps.alerts.server import Prefect, TailscaleIdentity, create_app, create_server
from starlette.testclient import TestClient

FAILURE = "prefect.automation.aaaa"
FAN = "prefect.automation.bbbb"
AUTOMATIONS = {FAILURE: "flow-run failure -> discord", FAN: "heavypad fan health -> discord"}


def run_failed(at, run="r1", deployment="mcp-fleet-health", state="Crashed"):
    return {
        "occurred": at,
        "event": "prefect.automation.triggered",
        "resource": {"prefect.resource.id": FAILURE},
        "payload": {
            "triggering_event": {
                "event": f"prefect.flow-run.{state}",
                "resource": {
                    "prefect.resource.id": f"prefect.flow-run.{run}",
                    "prefect.resource.name": f"{deployment}-{run}",
                    "prefect.state-name": state,
                    "prefect.state-message": "Process exited with status code: 1",
                },
                "related": [
                    {"prefect.resource.role": "flow", "prefect.resource.id": "prefect.flow.f"},
                    {
                        "prefect.resource.role": "deployment",
                        "prefect.resource.id": "prefect.deployment.d",
                        "prefect.resource.name": deployment,
                    },
                ],
                "payload": {},
            }
        },
    }


def fan(at, name="fan.degraded"):
    return {
        "occurred": at,
        "event": "prefect.automation.triggered",
        "resource": {"prefect.resource.id": FAN},
        "payload": {
            "triggering_event": {
                "event": name,
                "resource": {"prefect.resource.id": "host.heavypad"},
                "related": [],
                "payload": {"summary": "cpu fan is slow"},
            }
        },
    }


def update(at, key, action, agent="ycc", note="", link=""):
    return {
        "occurred": at,
        "event": action,
        "resource": {"prefect.resource.id": "alert." + key},
        "payload": {"agent": agent, "note": note, "link": link},
    }


KEY = "flow-run-failure/mcp-fleet-health"


def test_repeated_failures_of_one_deployment_are_one_incident():
    found = incidents(
        AUTOMATIONS,
        [
            run_failed("2026-11-07T09:12:00Z", "r3"),
            run_failed("2026-11-07T07:12:00Z", "r1"),
            run_failed("2026-11-07T08:12:00Z", "r2"),
            run_failed("2026-11-07T08:30:00Z", "r9", deployment="ingest", state="Failed"),
        ],
        [],
    )
    assert set(found) == {KEY, "flow-run-failure/ingest"}
    incident = found[KEY]
    assert incident["fired"] == 3
    assert incident["status"] == "open"
    assert incident["first_fired"] == "2026-11-07T07:12:00Z"
    assert incident["firings"][-1]["flow_run_id"] == "r3"
    assert incident["latest"] == "Crashed: Process exited with status code: 1"
    assert incident["title"] == "flow-run failure: mcp-fleet-health"


def test_claim_handoff_resolve_and_reopen():
    firing = run_failed("2026-11-07T07:12:00Z")
    claimed = update("2026-11-07T07:20:00Z", KEY, "alert.claimed")
    handed = update("2026-11-07T07:30:00Z", KEY, "alert.handed-off", note="needs the fly token")
    resolved = update("2026-11-07T08:00:00Z", KEY, "alert.resolved", agent="claude-code")
    again = run_failed("2026-11-07T09:12:00Z", "r2")

    def status(*updates, firings=(firing,)):
        item = incidents(AUTOMATIONS, firings, updates)[KEY]
        return item["status"], item["owner"], item["needs"]

    assert status(claimed) == ("claimed", "ycc", "")
    assert status(claimed, handed) == ("open", "", "needs the fly token")
    assert status(claimed, handed, resolved) == ("resolved", "claude-code", "")
    assert status(claimed, handed, resolved, firings=(firing, again)) == ("open", "", "")
    assert status(claimed, firings=(firing, again)) == ("claimed", "ycc", "")


def test_recovery_event_resolves_and_unknown_updates_are_dropped():
    found = incidents(
        AUTOMATIONS,
        [fan("2026-11-07T07:00:00Z"), fan("2026-11-07T08:00:00Z", "fan.recovered")],
        [update("2026-11-07T07:30:00Z", "never-fired/x", "alert.claimed")],
    )
    assert set(found) == {"heavypad-fan-health/host.heavypad"}
    assert found["heavypad-fan-health/host.heavypad"]["status"] == "resolved"


def test_other_automations_and_agent_updates_are_not_alerts():
    stranger = run_failed("2026-11-07T07:12:00Z")
    stranger["resource"]["prefect.resource.id"] = "prefect.automation.digest"
    echo = fan("2026-11-07T07:12:00Z", "alert.claimed")
    assert incidents(AUTOMATIONS, [stranger, echo], []) == {}


def test_missing_event_alert_has_a_key():
    assert key_of("bandwidth watchdog missing -> discord", None) == "bandwidth-watchdog-missing"


def test_every_notification_name_is_a_declared_automation():
    spec = yaml.safe_load((Path(__file__).parent.parent / "deploy/automations.yaml").read_text())
    declared = {item["name"] for item in spec["automations"]}
    assert declared >= NOTIFICATIONS


class Server:
    def __init__(self):
        self.triggered = [run_failed("2026-11-07T07:12:00Z")]
        self.lifecycle = []
        self.emitted = []
        self.filters = []
        self.completed = []
        self.lagging = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        if path == "/api/automations/filter":
            return httpx.Response(
                200,
                json=[
                    {"id": "aaaa", "name": name, "actions": [{"type": "send-notification"}]}
                    for name in ("flow-run failure -> discord",)
                ]
                + [
                    {
                        "id": "digest",
                        "name": "brief ready -> discord",
                        "actions": [{"type": "send-notification"}],
                    },
                    {
                        "id": "runs",
                        "name": "x__automation_1",
                        "actions": [{"type": "run-deployment"}],
                    },
                ],
            )
        if path == "/api/events/filter":
            self.filters.append(body["filter"])
            names = body["filter"]["event"]["name"]
            if names == ["prefect.flow-run.Completed"]:
                return httpx.Response(200, json={"events": self.completed, "next_page": None})
            events = self.triggered if names == ["prefect.automation.triggered"] else self.lifecycle
            return httpx.Response(
                200,
                json={
                    "events": events[:1],
                    "next_page": "http://elsewhere/api/events/filter/next?page-token=t"
                    if len(events) > 1
                    else None,
                },
            )
        if path == "/api/events/filter/next":
            assert request.url.host == "prefect.test"
            events = self.triggered if self.triggered[1:] else self.lifecycle
            return httpx.Response(200, json={"events": events[1:], "next_page": None})
        if path == "/api/events":
            self.emitted.extend(body)
            if not self.lagging:
                self.lifecycle.extend(body)
            return httpx.Response(204)
        if path == "/api/logs/filter":
            return httpx.Response(
                200,
                json=[
                    {"flow_run_id": "r1", "timestamp": "t2", "level": 40, "message": "boom"},
                    {"flow_run_id": "other", "timestamp": "t1", "level": 20, "message": "leak"},
                    {"flow_run_id": "r1", "timestamp": "t0", "level": 20, "message": "start"},
                ],
            )
        raise AssertionError(path)


@pytest.fixture
def server():
    return Server()


@pytest.fixture
def mcp(server):
    client = httpx.Client(transport=httpx.MockTransport(server))
    return create_server(Prefect("https://prefect.test/api", client), "https://ui.test")


async def call(mcp, tool, **arguments):
    async with Client(mcp) as client:
        return (await client.call_tool(tool, arguments)).data


async def test_list_and_get_read_only_alerting_automations(mcp, server):
    server.triggered.append(run_failed("2026-11-07T08:12:00Z", "r2"))
    listed = await call(mcp, "list_alerts")
    assert [item["key"] for item in listed["alerts"]] == [KEY]
    assert listed["alerts"][0]["fired"] == 2
    assert server.filters[0]["resource"] == {"id": ["prefect.automation.aaaa"]}
    assert server.filters[2]["any_resource"] == {"id": ["prefect.deployment.d"]}
    assert server.filters[2]["occurred"] == {"since": "2026-11-07T08:12:00Z"}

    server.triggered.pop()
    alert = await call(mcp, "get_alert", key=KEY)
    assert alert["run_url"] == "https://ui.test/runs/flow-run/r1"
    assert [line["message"] for line in alert["logs"]] == ["start", "boom"]


async def test_update_emits_one_event_and_a_second_claim_is_refused(mcp, server):
    first = await call(mcp, "update_alert", key=KEY, agent="ycc", action="claim")
    assert first["recorded"] and first["alert"]["status"] == "claimed"
    second = await call(mcp, "update_alert", key=KEY, agent="phi", action="claim")
    assert not second["recorded"] and "ycc" in second["reason"]
    done = await call(
        mcp,
        "update_alert",
        key=KEY,
        agent="ycc",
        action="resolve",
        note="deferred the import",
        link="https://tangled.org/zzstoatzz.io/my-prefect-server/pulls/1",
    )
    assert done["alert"]["status"] == "resolved" and done["alert"]["owner"] == "ycc"
    assert [event["event"] for event in server.emitted] == ["alert.claimed", "alert.resolved"]
    resolved = server.emitted[-1]
    assert resolved["resource"] == {
        "prefect.resource.id": "alert." + KEY,
        "prefect.resource.name": "flow-run failure: mcp-fleet-health",
    }
    assert resolved["payload"]["summary"] == (
        "ycc resolved it: deferred the import\n"
        "https://tangled.org/zzstoatzz.io/my-prefect-server/pulls/1"
    )
    assert (await call(mcp, "list_alerts", status="resolved"))["alerts"][0]["owner"] == "ycc"


@pytest.mark.parametrize(
    "arguments",
    [
        {"key": "nope/x", "agent": "ycc", "action": "claim"},
        {"key": KEY, "agent": "ycc", "action": "resolve"},
        {"key": KEY, "agent": "ycc", "action": "handoff", "note": " "},
        {"key": KEY, "agent": "@everyone\nhi", "action": "claim"},
        {"key": KEY, "agent": "ycc", "action": "note", "note": "x", "link": "http://plain"},
    ],
)
async def test_invalid_updates_emit_nothing(mcp, server, arguments):
    with pytest.raises(ToolError):
        await call(mcp, "update_alert", **arguments)
    assert server.emitted == []


def test_only_the_owners_tailnet_peers_are_admitted(server, monkeypatch):
    logins = {"100.96.0.1": "owner@github", "100.96.0.2": "guest@github"}

    async def whois(self, address):
        return logins.get(address)

    monkeypatch.setattr(TailscaleIdentity, "peer_login", whois)
    prefect = Prefect(
        "https://prefect.test/api", httpx.Client(transport=httpx.MockTransport(server))
    )
    app = create_app(prefect, "owner@github", "100.96.0.9")
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    headers = {"host": "100.96.0.9", "accept": "application/json, text/event-stream"}
    for peer, status in (("100.96.0.1", 200), ("100.96.0.2", 403), ("127.0.0.1", 403)):
        with TestClient(app, client=(peer, 50000)) as client:
            response = client.post("/mcp", json=request, headers=headers)
            assert response.status_code == status, peer
    with TestClient(app, client=("100.96.0.1", 50000)) as client:
        # A request without the 2026-07-28 envelope is answered as an event stream.
        body = client.post("/mcp", json=request, headers=headers).text
        message = next(line for line in body.splitlines() if line.startswith("data:"))
        tools = json.loads(message.removeprefix("data:"))["result"]["tools"]
        assert {tool["name"] for tool in tools} == {"list_alerts", "get_alert", "update_alert"}


async def test_loopback_and_public_addresses_are_never_looked_up():
    identity = TailscaleIdentity(lambda *_: None, "owner@github")
    assert await identity.peer_login("127.0.0.1") is None
    assert await identity.peer_login("203.0.113.5") is None
    assert await identity.peer_login("not-an-address") is None


async def test_tools_declare_what_they_change(mcp):
    async with Client(mcp) as client:
        hints = {tool.name: tool.annotations for tool in await client.list_tools()}
    assert hints["list_alerts"].read_only_hint and hints["get_alert"].read_only_hint
    assert hints["update_alert"].read_only_hint is False


def completed(at, deployment="prefect.deployment.d"):
    return {
        "event": "prefect.flow-run.Completed",
        "occurred": at,
        "related": [{"prefect.resource.role": "deployment", "prefect.resource.id": deployment}],
    }


async def test_a_later_completed_run_resolves_a_flow_run_failure(mcp, server):
    server.completed = [completed("2026-11-07T10:12:00Z")]
    assert (await call(mcp, "list_alerts"))["alerts"] == []
    alert = (await call(mcp, "list_alerts", status="resolved"))["alerts"][0]
    assert alert["recovered_at"] == "2026-11-07T10:12:00Z" and "owner" not in alert


@pytest.mark.parametrize(
    "event",
    [
        completed("2026-11-07T10:12:00Z", "prefect.deployment.other"),
        completed("2026-11-07T06:00:00Z"),
        {**completed("2026-11-07T10:12:00Z"), "event": "prefect.flow-run.Running"},
    ],
)
async def test_an_unrelated_or_earlier_run_does_not_resolve_it(mcp, server, event):
    server.completed = [event]
    assert [item["key"] for item in (await call(mcp, "list_alerts"))["alerts"]] == [KEY]


async def test_updates_count_before_the_event_log_shows_them(mcp, server):
    server.lagging = True
    assert (await call(mcp, "update_alert", key=KEY, agent="ycc", action="claim"))["recorded"]
    second = await call(mcp, "update_alert", key=KEY, agent="phi", action="claim")
    assert not second["recorded"] and "ycc" in second["reason"]
    assert (await call(mcp, "list_alerts"))["alerts"][0]["owner"] == "ycc"
    server.lifecycle.extend(server.emitted)
    assert len((await call(mcp, "get_alert", key=KEY))["updates"]) == 1


async def test_a_resolved_alert_cannot_be_claimed_or_handed_off(mcp, server):
    await call(mcp, "update_alert", key=KEY, agent="phi", action="claim")
    done = await call(mcp, "update_alert", key=KEY, agent="ycc", action="resolve", note="fixed")
    assert done["alert"]["owner"] == "ycc"
    for action in ("claim", "handoff"):
        refused = await call(mcp, "update_alert", key=KEY, agent="phi", action=action, note="x")
        assert not refused["recorded"] and "resolved" in refused["reason"]
    assert len(server.emitted) == 2


def fired(at, event_id, run="r1"):
    return {**run_failed(at, run), "id": event_id}


def events_client(mcp):
    from fastmcp.events import EventsClientExtension

    return Client(mcp, mode="auto", extensions=[EventsClientExtension()])


async def test_alert_event_starts_from_now_then_delivers_later_firings(mcp, server):
    server.triggered = [fired("2026-11-07T07:12:00Z", "e1")]
    async with events_client(mcp) as client:
        (listed,) = await client.list_events()
        start = await client.poll_events_mcp("alert.fired")
        quiet = await client.poll_events_mcp("alert.fired", cursor="2026-11-07T07:12:00+00:00")
        server.triggered.append(fired("2026-11-07T08:12:00Z", "e2", "r2"))
        server.triggered.append(fired("2026-11-07T09:12:00Z", "e3", "r3"))
        first = await client.poll_events_mcp("alert.fired", cursor=quiet.cursor, max_events=1)
        rest = await client.poll_events_mcp("alert.fired", cursor=first.cursor)

    assert (listed.name, listed.delivery) == ("alert.fired", ["poll", "push"])
    assert start.events == [] and start.cursor is not None
    assert quiet.events == [] and quiet.cursor == "2026-11-07T07:12:00+00:00"
    assert [event.event_id for event in first.events] == ["e2"]
    assert first.events[0].data == {
        "key": KEY,
        "title": "flow-run failure: mcp-fleet-health",
        "event": "prefect.flow-run.Crashed",
        "summary": "Crashed: Process exited with status code: 1",
        "at": "2026-11-07T08:12:00Z",
    }
    assert first.has_more is True and first.cursor == "2026-11-07T08:12:00Z"
    assert [event.event_id for event in rest.events] == ["e3"]
    assert rest.has_more is False
    assert server.filters[-1]["occurred"] == {"since": "2026-11-07T08:12:00Z"}


async def test_alert_event_rejects_a_cursor_that_is_not_a_time(mcp):
    from mcp.shared.exceptions import MCPError

    async with events_client(mcp) as client:
        with pytest.raises(MCPError) as refused:
            await client.poll_events_mcp("alert.fired", cursor="yesterday")
    assert refused.value.error.code == -32602
