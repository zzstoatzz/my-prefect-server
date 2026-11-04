from copy import deepcopy
from datetime import UTC, datetime

import pytest

from flows.watch_stream import expected_rate, observe, sample_metrics

AT = datetime(2026, 11, 4, 6, tzinfo=UTC).timestamp()
PROFILE = {"hours": [{"h": h, "n": 180, "med": 238} for h in range(24)]}


def sample(at, seq, **changes):
    return {"at": at, "seq": seq, "seen": at - 1, "start": 1, "reconnects": 0, **changes}


def verdict(at=AT):
    return {
        "relays": [
            {
                "host": host,
                "latest": {
                    "ts": datetime.fromtimestamp(at, UTC).isoformat(),
                    "connected": True,
                    "behind": False,
                    "coverage_pct": 99.5,
                },
            }
            for host in ("stream.waow.tech", "bsky.network")
        ]
    }


def advance(state, minute, rate=892, **changes):
    seq = state.get("sample", {}).get("seq", 100000) + rate * 300
    return observe(state, sample(AT + minute * 60, seq, **changes), PROFILE, verdict())


def test_surge_opens_once_and_rearms_after_two_normal_windows():
    state, _ = advance({}, 0)
    state, report = advance(state, 5)
    assert report["ratio"] == pytest.approx(892 / 238)
    assert "pending" not in state
    state, _ = advance(state, 10)
    event = state.pop("pending")
    assert "Cause unknown" in event["material"]
    state["active"] = event["event_id"]
    for minute in (15, 20, 25):
        state, _ = advance(state, minute)
        assert "pending" not in state
    state, _ = advance(state, 30, rate=238)
    assert "active" in state
    state, _ = advance(state, 35, rate=238)
    assert "active" not in state
    state, _ = advance(state, 40)
    state, _ = advance(state, 45)
    assert state["pending"]["event_id"] != event["event_id"]


@pytest.mark.parametrize(
    "changes", [{"reconnects": 1}, {"start": 2}, {"seen": AT - 1000}, {"seq": 0}]
)
def test_reconnect_restart_stale_events_and_sequence_reset_break_confirmation(changes):
    state, _ = advance({}, 0)
    state, _ = advance(state, 5)
    state, report = observe(state, {**sample(AT + 600, 1000000), **changes}, PROFILE, verdict())
    assert report["status"] == "deferred"
    assert "pending" not in state
    assert "high_since" not in state


def test_delayed_poll_and_bad_relay_coverage_do_not_create_activity_event():
    state, _ = advance({}, 0)
    delayed, report = advance(state, 30)
    assert report["status"] == "deferred"
    bad = verdict()
    bad["relays"][0]["latest"]["behind"] = True
    state, report = observe(state, sample(AT + 300, 1000000), PROFILE, bad)
    assert report["status"] == "deferred"
    assert "pending" not in delayed and "pending" not in state


def test_unrelated_relay_outage_does_not_veto_network_activity():
    state, _ = advance({}, 0)
    state, _ = advance(state, 5)
    health = verdict()
    health["relays"].append({"host": "unrelated", "latest": {"connected": False}})
    state, _ = observe(state, sample(AT + 600, state["sample"]["seq"] + 892 * 300), PROFILE, health)
    assert "pending" in state


def test_hour_boundary_uses_both_hourly_medians():
    profile = deepcopy(PROFILE)
    profile["hours"][5]["med"] = 300
    assert expected_rate(profile, AT - 150, AT + 150) == 269


def test_missing_or_nonfinite_metrics_fail_instead_of_inventing_zero():
    with pytest.raises(KeyError):
        sample_metrics("stream_upstream_seq 100", AT)
    text = "\n".join(
        [
            "stream_upstream_seq NaN",
            "process_start_time_seconds 1",
            "jetstream_livestream_reconnects_total 0",
            f"jetstream_livestream_last_seen_upstream_event_timestamp_seconds {AT}",
        ]
    )
    with pytest.raises(ValueError):
        sample_metrics(text, AT)


def test_one_delayed_high_window_still_needs_confirmation():
    state, _ = advance({}, 0)
    state, _ = observe(
        state, sample(AT + 600, state["sample"]["seq"] + 892 * 600), PROFILE, verdict()
    )
    assert "pending" not in state
    state, _ = advance(state, 15)
    assert "pending" in state
