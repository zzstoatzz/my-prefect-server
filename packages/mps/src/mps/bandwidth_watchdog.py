"""Account observed Internet TCP bytes, enforce registered service budgets, emit events."""

import argparse
import fcntl
import ipaddress
import json
import os
import re
import socket
import subprocess
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import httpx

EXCLUDED = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "224.0.0.0/4",
        "::/128",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
        "ff00::/8",
    )
)


def internet_peer(endpoint: str) -> bool:
    addr = ipaddress.ip_address(endpoint.rsplit(":", 1)[0].strip("[]").split("%", 1)[0])
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return not any(addr.version == net.version and addr in net for net in EXCLUDED)


def parse_sockets(output: str, include_local: bool = False) -> dict:
    result = {}
    header = None
    for line in output.splitlines():
        if not line.startswith((" ", "\t")):
            header = line
            continue
        if header is None:
            continue
        fields = header.split()
        if len(fields) < 5 or (not include_local and not internet_peer(fields[4])):
            continue
        ino = re.search(r"\bino:(\d+)", header)
        cookie = re.search(r"\bsk:([0-9a-f]+)", header)
        if not ino or not cookie:
            continue
        metrics = {k: int(v) for k, v in re.findall(r"\b(bytes_received|bytes_sent):(\d+)", line)}
        owner = re.search(r'users:\(\("([^"\n]+)",pid=(\d+)', header)
        cgroup = re.search(r"\bcgroup:(\S+)", header)
        group = cgroup[1] if cgroup else "unattributed"
        result[f"{ino[1]}:{cookie[1]}"] = {
            "bytes": sum(metrics.values()),
            "group": group,
            "process": owner[1] if owner else "unknown",
            "pid": int(owner[2]) if owner else None,
        }
    return result


def collect(include_local: bool = False) -> tuple[dict, dict]:
    output = subprocess.run(
        ["ss", "-Htinpe"], check=True, capture_output=True, text=True, timeout=4
    ).stdout
    interfaces = {}
    for path in Path("/sys/class/net").iterdir():
        if (path / "device").exists():
            interfaces[path.name] = sum(
                int((path / "statistics" / f"{direction}_bytes").read_text())
                for direction in ("rx", "tx")
            )
    return parse_sockets(output, include_local), interfaces


def read_heat() -> tuple[float, int] | None:
    for zone in Path("/sys/class/thermal").glob("thermal_zone*"):
        if (zone / "type").read_text().strip() == "x86_pkg_temp":
            celsius = int((zone / "temp").read_text()) / 1000
            break
    else:
        return None
    counter = Path("/sys/devices/system/cpu/cpu0/thermal_throttle/package_throttle_count")
    return celsius, int(counter.read_text()) if counter.exists() else 0


def read_disks(paths: list[str]) -> list[dict]:
    devices: dict[int, dict] = {}
    for path in paths:
        device = os.stat(path).st_dev
        if device in devices:
            devices[device]["disk_paths"].append(path)
            continue
        stat = os.statvfs(path)
        total = stat.f_blocks * stat.f_frsize
        available = stat.f_bavail * stat.f_frsize
        devices[device] = {
            "host": socket.gethostname(),
            "disk_paths": [path],
            "disk_free_bytes": available,
            "disk_total_bytes": total,
            "disk_free_ratio": available / total if total else None,
            "disk_free_inodes": stat.f_favail,
            "disk_total_inodes": stat.f_files,
            "disk_free_inode_ratio": stat.f_favail / stat.f_files if stat.f_files else None,
        }
    return list(devices.values())


def heat(state: dict, reading: tuple[float, int] | None, now: float, config: dict) -> None:
    """Warn when the CPU package stays hot for a whole window, not on a single spike."""
    if reading is None:
        return
    celsius, throttles = reading
    window = config["heat_window_seconds"]
    samples = state.get("heat", [])
    if samples and (now - samples[-1]["at"] > 30 or throttles < samples[-1]["throttles"]):
        samples = []
    samples = [s for s in samples if now - s["at"] <= window]
    samples.append({"at": now, "c": celsius, "throttles": throttles})
    state["heat"] = samples
    span = now - samples[0]["at"]
    if span < window * 0.9:
        return
    mean = sum(s["c"] for s in samples) / len(samples)
    cooldowns = state.setdefault("cooldowns", {})
    if (
        mean < config["heat_alert_celsius"]
        or now - cooldowns.get("cpu-hot", 0) < config["cooldown_seconds"]
    ):
        return
    cooldowns["cpu-hot"] = now
    peak = max(s["c"] for s in samples)
    per_minute = (throttles - samples[0]["throttles"]) / span * 60
    summary = (
        f"{socket.gethostname()}: CPU package averaged {mean:.1f}°C over the last "
        f"{window // 60} minutes (peak {peak:.0f}°C, limit 100°C, warning at "
        f"{config['heat_alert_celsius']}°C); {per_minute:.0f} package throttle events/min."
    )
    payload = {
        "mean_celsius": round(mean, 1),
        "peak_celsius": peak,
        "window_seconds": window,
        "threshold_celsius": config["heat_alert_celsius"],
        "throttle_events_per_minute": round(per_minute),
        "summary": summary,
    }
    state.setdefault("outbox", []).append(event("cpu-hot", payload, now, namespace="heat"))


def event(name: str, payload: dict, now: float, namespace: str = "bandwidth") -> dict:
    return {
        "id": str(uuid4()),
        "occurred": datetime.fromtimestamp(now, UTC).isoformat(),
        "event": f"{namespace}.{name}",
        "resource": {
            "prefect.resource.id": f"bandwidth.host.{socket.gethostname()}",
            "prefect.resource.name": socket.gethostname(),
        },
        "related": [],
        "payload": payload,
    }


def advance(
    state: dict, sockets: dict, interfaces: dict, boot: str, now: float, config: dict
) -> list[dict]:
    gap = "sampled_at" in state and now - state["sampled_at"] > 30
    baseline = state.get("boot") != boot or "sockets" not in state or gap
    state["sampled_at"] = now
    previous = state.get("sockets", {})
    groups = {}
    for key, value in sockets.items():
        delta = 0 if baseline else max(0, value["bytes"] - previous.get(key, {}).get("bytes", 0))
        group = value["group"]
        groups[group] = groups.get(group, 0) + delta
    host_bytes = (
        0
        if baseline
        else sum(
            max(0, value - state.get("interfaces", {}).get(name, value))
            for name, value in interfaces.items()
        )
    )
    state.update(boot=boot, sockets=sockets, interfaces=interfaces)
    state.setdefault("started", now)
    month = datetime.fromtimestamp(now, UTC).strftime("%Y-%m")
    cooldowns = state.setdefault("cooldowns", {})
    monthly_threshold = config["monthly_observed_budget_bytes"]
    if state.get("month") != month:
        state.update(month=month, month_tcp_bytes=0, month_host_bytes=0, monthly_warned_bytes=0)
        cooldowns.pop("monthly-budget", None)
    if "monthly_warned_bytes" not in state:
        last_warning_month = datetime.fromtimestamp(
            cooldowns.get("monthly-budget", 0), UTC
        ).strftime("%Y-%m")
        state["monthly_warned_bytes"] = monthly_threshold if last_warning_month == month else 0
    state["month_tcp_bytes"] += sum(groups.values())
    state["month_host_bytes"] += host_bytes
    samples = [s for s in state.get("samples", []) if now - s["at"] <= 3600]
    samples.append({"at": now, "groups": groups, "host": host_bytes})
    state["samples"] = samples
    recent_tcp = sum(sum(s["groups"].values()) for s in samples if now - s["at"] <= 900)
    recent_host = sum(s["host"] for s in samples if now - s["at"] <= 900)
    totals = {}
    for sample in samples:
        for group, count in sample["groups"].items():
            totals[group] = totals.get(group, 0) + count
    top = sorted(totals.items(), key=lambda pair: pair[1], reverse=True)[:5]
    summary = {
        "tcp_bytes_15m": recent_tcp,
        "host_bytes_15m": recent_host,
        "month_tcp_bytes": state["month_tcp_bytes"],
        "month_host_bytes": state["month_host_bytes"],
        "observed_since": state["started"],
        "top_groups_1h": dict(top),
        "coverage": "Observed Internet TCP lower bound; host counters include LAN and all protocols. UTC month; not an ISP meter.",
    }
    alerts = []
    for kind, count, threshold in (
        ("high-usage", recent_tcp, config["alert_bytes_15m"]),
        ("host-usage", recent_host, config["host_alert_bytes_15m"]),
        ("monthly-budget", state["month_tcp_bytes"], monthly_threshold),
    ):
        if kind == "monthly-budget" and count < state["monthly_warned_bytes"] + threshold:
            continue
        if count >= threshold and now - cooldowns.get(kind, 0) >= config["cooldown_seconds"]:
            cooldowns[kind] = now
            reason = ""
            if kind == "monthly-budget":
                state["monthly_warned_bytes"] = (count // threshold) * threshold
                next_warning = state["monthly_warned_bytes"] + threshold
                observed_since = datetime.fromtimestamp(state["started"], UTC).strftime(
                    "%Y-%m-%d %H:%M UTC"
                )
                reason = (
                    f"{count / 1e9:.2f} GB observed / {threshold / 1e9:.2f} GB monthly warning threshold "
                    f"({month} UTC; monitoring since {observed_since}; not ISP billing). "
                    f"Next monthly warning at {next_warning / 1e9:.2f} GB. "
                    "Recent usage: "
                )
            summary_text = (
                f"{socket.gethostname()}: {kind}; {reason}observed Internet TCP {recent_tcp / 1e9:.2f} GB/15m; host {recent_host / 1e9:.2f} GB/15m (includes LAN). Top hourly groups: "
                + ", ".join(f"{g.rsplit('/', 1)[-1]} {b / 1e9:.2f} GB" for g, b in top)
            )
            alerts.append(event(kind, {**summary, "summary": summary_text}, now))
    state["summary"] = summary
    actions = []
    state.setdefault("paused", {})
    for unit in config["pause_units"]:
        group = f"/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service/app.slice/{unit}"
        if group in groups and totals.get(group, 0) >= config["pause_bytes_1h"]:
            actions.append({"unit": unit, "bytes": totals[group]})
    state.setdefault("outbox", []).extend(alerts)
    return actions


def save(path: Path, state: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(state, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def enforce(state: dict, actions: list[dict], now: float) -> None:
    for action in actions:
        unit = action["unit"]
        current = subprocess.run(
            ["systemctl", "--user", "show", unit, "--property=FreezerState", "--value"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        if current.stdout.strip() == "frozen" and unit in state["paused"]:
            continue
        result = subprocess.run(
            ["systemctl", "--user", "freeze", unit],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        frozen = subprocess.run(
            ["systemctl", "--user", "show", unit, "--property=FreezerState", "--value"],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
        if result.returncode != 0 or frozen.stdout.strip() != "frozen":
            raise RuntimeError(f"Could not freeze registered service {unit}")
        state["paused"][unit] = {**action, "at": now}
        state["outbox"].append(
            event(
                "paused",
                {
                    **action,
                    "summary": f"Paused {unit}: observed {action['bytes'] / 1e9:.2f} GB Internet TCP in one hour. Progress retained; manual review required before thaw.",
                },
                now,
            )
        )


def flush(state: dict, client: httpx.Client, base: str) -> None:
    import logfire

    while state["outbox"]:
        item = state["outbox"][0]
        response = client.post(f"{base}/events", json=[item])
        response.raise_for_status()
        if item["event"] != "bandwidth.heartbeat":
            logfire.warn(
                "bandwidth watchdog: {summary}",
                summary=item["payload"]["summary"],
                watchdog_event=item["event"],
                event_id=item["id"],
                evidence=item["payload"],
            )
            logfire.force_flush()
        state["outbox"].pop(0)


def prometheus_metrics(state: dict, budget: int = 1_000_000_000_000) -> str:
    month = datetime.fromtimestamp(state["sampled_at"], UTC).strftime("%Y-%m")
    # A stopped sampler must not carry last month's totals into a new month.
    current = state["month"] == datetime.now(UTC).strftime("%Y-%m")
    values = {
        "sample_timestamp_seconds": state["sampled_at"],
        "observed_since_timestamp_seconds": state["started"],
        "budget_bytes": budget,
        "month_tcp_bytes": state["month_tcp_bytes"] if current else 0,
        "month_host_bytes": state["month_host_bytes"] if current else 0,
        "tcp_bytes_15m": state.get("summary", {}).get("tcp_bytes_15m", 0),
        "host_bytes_15m": state.get("summary", {}).get("host_bytes_15m", 0),
    }
    return "".join(
        f"# TYPE bandwidth_watchdog_{name} gauge\n"
        f'bandwidth_watchdog_{name}{{month="{month}"}} {value}\n'
        for name, value in values.items()
    )


def metrics_server(path: Path, port: int, budget: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/metrics":
                self.send_error(404)
                return
            try:
                body = prometheus_metrics(json.loads(path.read_text()), budget).encode()
            except (OSError, ValueError, KeyError):
                self.send_error(503, "Watchdog state unavailable")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    config = {
        "heat_alert_celsius": 95,
        "heat_window_seconds": 300,
        "disk_paths": ["/", str(Path.home() / ".local/share/prefect-home/storage")],
    } | json.loads(args.config.read_text())
    if any(not re.fullmatch(r"[a-zA-Z0-9_.@-]+\.service", unit) for unit in config["pause_units"]):
        raise ValueError("pause_units must contain literal systemd service names")
    args.state.parent.mkdir(parents=True, exist_ok=True)
    lock = args.state.with_suffix(".lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    state = json.loads(args.state.read_text()) if args.state.exists() else {}
    metrics_server(
        args.state,
        config.get("metrics_port", 19815),
        config.get("isp_budget_bytes", 1_000_000_000_000),
    )
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    raw_auth = os.environ["PREFECT_API_AUTH_STRING"]
    auth = httpx.BasicAuth(*raw_auth.split(":", 1))
    base = os.environ["PREFECT_API_URL"].rstrip("/")
    with httpx.Client(auth=auth, timeout=5) as client:
        telemetry_ready = False
        last_disk_sample = 0
        while True:
            started = time.monotonic()
            now = time.time()
            sockets, interfaces = collect()
            actions = advance(state, sockets, interfaces, boot, now, config)
            heat(state, read_heat(), now, config)
            save(args.state, state)
            enforce(state, actions, now)
            if now - state.get("heartbeat", 0) >= 60:
                state["heartbeat"] = now
                state["outbox"] = [
                    item for item in state["outbox"] if item["event"] != "bandwidth.heartbeat"
                ]
                state["outbox"].append(event("heartbeat", state["summary"], now))
            save(args.state, state)
            try:
                if not telemetry_ready:
                    import logfire

                    response = client.get(
                        f"{base}/block_types/slug/secret/block_documents/name/logfire-server-write-token",
                        params={"include_secrets": "true"},
                    )
                    response.raise_for_status()
                    logfire.configure(
                        token=response.json()["data"]["value"],
                        service_name="bandwidth-watchdog",
                        console=False,
                    )
                    telemetry_ready = True
                if now - last_disk_sample >= 60:
                    import logfire

                    try:
                        for sample in read_disks(config["disk_paths"]):
                            logfire.info(
                                "heavypad disk sample",
                                watchdog_event="disk.sample",
                                **sample,
                            )
                    except OSError as error:
                        logfire.error(
                            "heavypad disk sample unavailable",
                            watchdog_event="disk.sample_error",
                            error_type=type(error).__name__,
                        )
                    logfire.force_flush()
                    last_disk_sample = now
                flush(state, client, base)
                save(args.state, state)
            except (httpx.HTTPError, KeyError):
                print("Event delivery unavailable; retained in local outbox", flush=True)
            if args.once:
                return
            time.sleep(max(0, config["sample_seconds"] - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
