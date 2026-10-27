import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import logfire
import pytest
from mps.bandwidth_watchdog import (
    advance,
    flush,
    heat,
    internet_peer,
    parse_sockets,
    read_disks,
    save,
)

CONFIG = {
    "alert_bytes_15m": 100,
    "host_alert_bytes_15m": 200,
    "pause_bytes_1h": 300,
    "monthly_observed_budget_bytes": 1000,
    "cooldown_seconds": 3600,
    "pause_units": ["download.service"],
    "heat_alert_celsius": 95,
    "heat_window_seconds": 300,
}


def test_disk_sampling_uses_available_space_and_deduplicates_filesystems(tmp_path):
    other = tmp_path / "postgres"
    other.mkdir()
    samples = read_disks([str(tmp_path), str(other)])
    assert len(samples) == 1
    sample = samples[0]
    stat = os.statvfs(tmp_path)
    assert sample["disk_paths"] == [str(tmp_path), str(other)]
    assert sample["disk_total_bytes"] == stat.f_blocks * stat.f_frsize
    assert 0 <= sample["disk_free_bytes"] <= sample["disk_total_bytes"]
    assert 0 <= sample["disk_free_ratio"] <= 1
    if stat.f_files:
        assert 0 <= sample["disk_free_inode_ratio"] <= 1


def test_missing_disk_path_is_not_reported_healthy(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_disks([str(tmp_path / "missing")])


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


def test_monthly_milestones_survive_quiet_periods_and_restart(tmp_path):
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
    [alert] = [e for e in state["outbox"] if e["event"] == "bandwidth.monthly-budget"]
    assert alert["payload"]["month_tcp_bytes"] == 183_000_000_000
    message = alert["payload"]["summary"]
    assert "183.00 GB observed / 100.00 GB monthly warning threshold" in message
    assert "2026-10 UTC; monitoring since 2026-10-17 00:52 UTC; not ISP billing" in message
    assert "Next monthly warning at 200.00 GB." in message
    state["outbox"].clear()
    advance(state, sockets(183_000_000_000), {}, "boot", now + 3610, config)
    assert state["outbox"] == []
    path = tmp_path / "state.json"
    save(path, state)
    state = json.loads(path.read_text())
    advance(state, sockets(183_000_000_000), {}, "boot", now + 3615, config)
    assert state["outbox"] == []
    advance(state, sockets(200_000_000_000), {}, "boot", now + 3620, config)
    [alert] = [e for e in state["outbox"] if e["event"] == "bandwidth.monthly-budget"]
    assert "Next monthly warning at 300.00 GB." in alert["payload"]["summary"]
    state["outbox"].clear()
    advance(state, sockets(200_000_000_000), {}, "boot", now + 8000, config)
    assert state["outbox"] == []


def test_monthly_milestone_waits_for_cooldown_and_coalesces_crossed_levels():
    config = {**CONFIG, "alert_bytes_15m": 100000, "host_alert_bytes_15m": 100000}
    state = {}
    now = 1800000000
    advance(state, sockets(0), {}, "boot", now, config)
    advance(state, sockets(1000), {}, "boot", now + 5, config)
    state["outbox"].clear()
    advance(state, sockets(5500), {}, "boot", now + 10, config)
    assert state["outbox"] == []
    advance(state, sockets(5500), {}, "boot", now + 3610, config)
    assert len(state["outbox"]) == 1
    assert state["monthly_warned_bytes"] == 5000
    state["outbox"].clear()
    advance(state, sockets(5500), {}, "boot", now + 8000, config)
    assert state["outbox"] == []


def test_monthly_migration_preserves_previous_warning_and_accounting():
    state = {}
    now = 1800000000
    advance(state, sockets(0), {}, "boot", now, CONFIG)
    advance(state, sockets(1500), {}, "boot", now + 5, CONFIG)
    del state["monthly_warned_bytes"]
    state["outbox"].clear()
    advance(state, sockets(1500), {}, "boot", now + 4000, CONFIG)
    assert state["outbox"] == []
    assert state["month_tcp_bytes"] == 1500
    assert state["monthly_warned_bytes"] == 1000


def test_new_month_rearms_warning_even_within_previous_cooldown():
    state = {}
    now = 1790812790
    advance(state, sockets(0), {}, "boot", now, CONFIG)
    advance(state, sockets(1500), {}, "boot", now + 5, CONFIG)
    state["outbox"].clear()
    advance(state, sockets(3000), {}, "boot", now + 11, CONFIG)
    [alert] = state["outbox"]
    assert alert["event"] == "bandwidth.monthly-budget"
    assert alert["payload"]["month_tcp_bytes"] == 1500


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


def sustain(state, celsius, start, seconds, throttles=0, step=5):
    for offset in range(0, seconds + 1, step):
        heat(state, (celsius, throttles + offset), start + offset, CONFIG)


def test_heat_warns_only_after_a_sustained_window():
    state = {}
    sustain(state, 99, 1800000000, 200)
    assert state.get("outbox", []) == []
    sustain(state, 99, 1800000205, 100, throttles=205)
    [warning] = state["outbox"]
    assert warning["event"] == "heat.cpu-hot"
    assert warning["payload"]["mean_celsius"] == 99
    assert warning["payload"]["throttle_events_per_minute"] == 60
    assert "averaged 99.0°C" in warning["payload"]["summary"]


def test_heat_below_threshold_and_brief_spikes_stay_quiet():
    state = {}
    sustain(state, 93, 1800000000, 600)
    heat(state, (100, 0), 1800000605, CONFIG)
    assert state.get("outbox", []) == []


def test_heat_cooldown_and_gap_reset():
    state = {}
    sustain(state, 97, 1800000000, 300)
    assert len(state["outbox"]) == 1
    sustain(state, 97, 1800000305, 1200, throttles=305)
    assert len(state["outbox"]) == 1
    state = {"cooldowns": {}}
    sustain(state, 97, 1800000000, 200)
    sustain(state, 97, 1800000260, 200, throttles=260)
    assert state.get("outbox", []) == []


def test_heat_without_a_package_sensor_is_a_no_op():
    state = {}
    heat(state, None, 1800000000, CONFIG)
    assert state == {}
