import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import logfire
import pytest
from mps.bandwidth_watchdog import advance, flush, internet_peer, parse_sockets, save

CONFIG = {
    "alert_bytes_15m": 100,
    "host_alert_bytes_15m": 200,
    "pause_bytes_1h": 300,
    "monthly_observed_budget_bytes": 1000,
    "cooldown_seconds": 3600,
    "pause_units": ["download.service"],
}
GROUP = (
    f"/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service/app.slice/download.service"
)


def sockets(count):
    return {"1:abc": {"bytes": count, "group": GROUP, "process": "curl", "pid": 1}}


@pytest.mark.parametrize(
    "peer,expected",
    [
        ("1.1.1.1:443", True),
        ("100.50.185.240:443", True),
        ("100.96.216.23:443", False),
        ("192.168.0.1:80", False),
        ("[::ffff:192.168.0.1]:80", False),
        ("[2606:4700::1111]:443", True),
        ("[fe80::1%eth0]:80", False),
    ],
)
def test_peers(peer, expected):
    assert internet_peer(peer) == expected


def test_socket_attribution():
    raw = 'ESTAB 0 0 192.168.0.155:1234 1.1.1.1:443 users:(("curl",pid=42,fd=6)) ino:99 sk:abc cgroup:/downloads <->\n\t cubic bytes_sent:7 bytes_received:123\nESTAB 0 0 127.0.0.1:1 127.0.0.1:2 ino:100 sk:def\n\t bytes_sent:999\n'
    assert parse_sockets(raw) == {
        "99:abc": {"bytes": 130, "group": "/downloads", "process": "curl", "pid": 42}
    }


def test_restart_baseline_threshold_and_rolloff(tmp_path):
    state = {}
    now = 1800000000
    assert advance(state, sockets(10000), {"wifi": 90000}, "boot1", now, CONFIG) == []
    assert state["month_tcp_bytes"] == 0
    path = tmp_path / "state.json"
    save(path, state)
    state = json.loads(path.read_text())
    actions = advance(state, sockets(10400), {"wifi": 90500}, "boot1", now + 5, CONFIG)
    assert actions == [{"unit": "download.service", "bytes": 400}]
    assert state["month_tcp_bytes"] == 400
    assert {e["event"] for e in state["outbox"]} == {"bandwidth.high-usage", "bandwidth.host-usage"}
    count = len(state["outbox"])
    advance(state, sockets(10400), {"wifi": 90500}, "boot1", now + 10, CONFIG)
    assert len(state["outbox"]) == count
    advance(state, sockets(5), {"wifi": 5}, "boot2", now + 20, CONFIG)
    assert state["month_tcp_bytes"] == 400
    assert advance(state, sockets(5), {"wifi": 5}, "boot2", now + 4000, CONFIG) == []
    assert state["summary"]["tcp_bytes_15m"] == 0


def test_new_socket_counts_but_initial_inventory_does_not():
    state = {}
    advance(state, {}, {}, "boot", 1800000000, CONFIG)
    advance(state, sockets(120), {}, "boot", 1800000005, CONFIG)
    assert state["month_tcp_bytes"] == 120


def test_monthly_alert_explains_cumulative_trigger_after_recent_usage_rolls_off():
    config = {
        **CONFIG,
        "alert_bytes_15m": 5_000_000_000,
        "host_alert_bytes_15m": 5_000_000_000,
        "monthly_observed_budget_bytes": 100_000_000_000,
        "pause_units": [],
    }
    state = {}
    now = 1792198367
    advance(state, sockets(0), {}, "boot", now, config)
    advance(state, sockets(183_000_000_000), {}, "boot", now + 5, config)
    state["outbox"].clear()
    advance(state, sockets(183_000_000_000), {}, "boot", now + 3610, config)

    [alert] = state["outbox"]
    assert alert["event"] == "bandwidth.monthly-budget"
    assert alert["payload"]["month_tcp_bytes"] == 183_000_000_000
    assert alert["payload"]["tcp_bytes_15m"] == 0
    message = alert["payload"]["summary"]
    assert "183.00 GB observed / 100.00 GB monthly warning threshold" in message
    assert "2026-10 UTC; monitoring since 2026-10-17 00:52 UTC; not ISP billing" in message
    assert "Recent usage: observed Internet TCP 0.00 GB/15m" in message


def test_month_rollover_and_counter_reset():
    state = {}
    advance(state, sockets(100), {}, "boot", 1790812790, CONFIG)
    advance(state, sockets(200), {}, "boot", 1790812795, CONFIG)
    assert state["month_tcp_bytes"] == 100
    advance(state, sockets(2), {}, "boot", 1790812801, CONFIG)
    assert state["month_tcp_bytes"] == 0


def test_http_failure_retains_same_event_for_retry():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(503 if len(received) == 1 else 204)
            self.end_headers()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logfire.configure(send_to_logfire=False, console=False)
    state = {
        "outbox": [
            {
                "id": "stable-id",
                "event": "bandwidth.high-usage",
                "payload": {"summary": "controlled test"},
            }
        ]
    }
    try:
        with httpx.Client() as client:
            base = f"http://127.0.0.1:{server.server_port}"
            with pytest.raises(httpx.HTTPStatusError):
                flush(state, client, base)
            assert state["outbox"][0]["id"] == "stable-id"
            flush(state, client, base)
            assert state["outbox"] == []
            assert received[0] == received[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_duplicate_monitor_lock(tmp_path):
    import fcntl

    path = tmp_path / "state.lock"
    with path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import fcntl,sys; f=open(sys.argv[1],'w'); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)",
                str(path),
            ],
            capture_output=True,
            check=False,
        )
        assert result.returncode != 0
        assert b"BlockingIOError" in result.stderr


def test_sampling_gap_does_not_assign_historical_bytes_to_current_window():
    state = {}
    advance(state, sockets(100), {}, "boot", 1800000000, CONFIG)
    actions = advance(state, sockets(1000000), {}, "boot", 1800000100, CONFIG)
    assert actions == []
    assert state["month_tcp_bytes"] == 0
    assert state["outbox"] == []


def test_finished_transfer_does_not_freeze_an_inactive_unit():
    state = {}
    advance(state, sockets(0), {}, "boot", 1800000000, CONFIG)
    assert advance(state, sockets(400), {}, "boot", 1800000005, CONFIG)
    assert advance(state, {}, {}, "boot", 1800000010, CONFIG) == []
