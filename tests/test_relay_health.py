from datetime import UTC, datetime

from flows.relay_health import SERVICE, STALE_AFTER, assess, transitions

NOW = datetime(2026, 11, 10, 6, 10, tzinfo=UTC).timestamp()
RUN = "2026-11-10T06:05:00Z"


def relay(host, behind=False, connected=True, coverage=99.4):
    return {
        "host": host,
        "behind_lately": behind,
        "behind_runs": 9 if behind else 0,
        "runs": 24,
        "avg_coverage_pct": 81.2 if behind else 99.3,
        "latest": {"ts": RUN, "coverage_pct": coverage, "behind": behind, "connected": connected},
    }


def status(*relays, until=RUN):
    return {
        "window": {"runs": 24, "since": "2026-11-09T18:05:00Z", "until": until},
        "relays": list(relays),
    }


def step(state, *relays, now=NOW, until=RUN):
    return transitions(state, assess(status(*relays, until=until), now), now)


def test_healthy_relays_seed_silently_and_unwatched_hosts_are_ignored():
    state, events = step({}, relay("zlay.waow.tech"), relay("bsky.network", behind=True))
    assert events == []
    assert state == {SERVICE: "ok", "zlay.waow.tech": "ok"}


def test_falling_behind_alerts_once_then_recovery_resolves():
    state, _ = step({}, relay("zlay.waow.tech"))
    state, events = step(state, relay("zlay.waow.tech", behind=True, coverage=62))
    assert [e["event"] for e in events] == ["relay-eval.behind"]
    assert events[0]["resource"]["prefect.resource.id"] == "relay-eval.relay.zlay.waow.tech"
    assert "9/24" in events[0]["payload"]["summary"]
    state, events = step(state, relay("zlay.waow.tech", behind=True, coverage=60))
    assert events == []
    state, events = step(state, relay("zlay.waow.tech"))
    assert [e["event"] for e in events] == ["relay-eval.recovered"]
    assert state["zlay.waow.tech"] == "ok"


def test_disconnected_counts_as_firing_and_already_broken_relays_alert_on_first_sight():
    _, events = step({}, relay("stream.waow.tech", connected=False))
    assert [e["event"] for e in events] == ["relay-eval.behind"]
    assert "disconnected" in events[0]["payload"]["summary"]


def test_stale_relay_eval_alerts_without_touching_relay_state():
    state, _ = step({}, relay("zlay.waow.tech", behind=True))
    stale_until = datetime.fromtimestamp(NOW - STALE_AFTER - 60, UTC).isoformat()
    state, events = step(state, relay("zlay.waow.tech"), until=stale_until)
    assert [e["event"] for e in events] == ["relay-eval.stale"]
    assert events[0]["resource"]["prefect.resource.id"] == SERVICE
    assert state["zlay.waow.tech"] == "firing"
    state, events = step(state, relay("zlay.waow.tech"))
    assert sorted(e["event"] for e in events) == ["relay-eval.recovered", "relay-eval.recovered"]


def test_event_ids_are_stable_for_the_same_verdict():
    first = step({}, relay("zlay.waow.tech", behind=True))[1]
    again = step({}, relay("zlay.waow.tech", behind=True), now=NOW + 300)[1]
    assert first[0]["id"] == again[0]["id"]
