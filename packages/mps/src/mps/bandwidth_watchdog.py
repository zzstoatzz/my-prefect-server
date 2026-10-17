"""Account observed Internet TCP bytes, enforce registered service budgets, emit events."""

import argparse
import fcntl
import ipaddress
import json
import os
import re
import socket
import subprocess
import time
from datetime import UTC, datetime
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


def event(name: str, payload: dict, now: float) -> dict:
    return {
        "id": str(uuid4()),
        "occurred": datetime.fromtimestamp(now, UTC).isoformat(),
        "event": f"bandwidth.{name}",
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
    if state.get("month") != month:
        state.update(month=month, month_tcp_bytes=0, month_host_bytes=0)
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
    cooldowns = state.setdefault("cooldowns", {})
    for kind, count, threshold in (
        ("high-usage", recent_tcp, config["alert_bytes_15m"]),
        ("host-usage", recent_host, config["host_alert_bytes_15m"]),
        ("monthly-budget", state["month_tcp_bytes"], config["monthly_observed_budget_bytes"]),
    ):
        if count >= threshold and now - cooldowns.get(kind, 0) >= config["cooldown_seconds"]:
            cooldowns[kind] = now
            summary_text = (
                f"{socket.gethostname()}: {kind}; observed Internet TCP {recent_tcp / 1e9:.2f} GB/15m; host {recent_host / 1e9:.2f} GB/15m (includes LAN). Top hourly groups: "
                + ", ".join(f"{g.rsplit('/', 1)[-1]} {b / 1e9:.2f} GB" for g, b in top)
            )
            alerts.append(event(kind, {**summary, "summary": summary_text}, now))
    state["summary"] = summary
    actions = []
    for unit in config["pause_units"]:
        group = f"/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service/app.slice/{unit}"
        if totals.get(group, 0) >= config["pause_bytes_1h"] and unit not in state.setdefault(
            "paused", {}
        ):
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if any(not re.fullmatch(r"[a-zA-Z0-9_.@-]+\.service", unit) for unit in config["pause_units"]):
        raise ValueError("pause_units must contain literal systemd service names")
    args.state.parent.mkdir(parents=True, exist_ok=True)
    lock = args.state.with_suffix(".lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    state = json.loads(args.state.read_text()) if args.state.exists() else {}
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    raw_auth = os.environ["PREFECT_API_AUTH_STRING"]
    auth = httpx.BasicAuth(*raw_auth.split(":", 1))
    base = os.environ["PREFECT_API_URL"].rstrip("/")
    with httpx.Client(auth=auth, timeout=5) as client:
        telemetry_ready = False
        while True:
            started = time.monotonic()
            now = time.time()
            sockets, interfaces = collect()
            actions = advance(state, sockets, interfaces, boot, now, config)
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
                flush(state, client, base)
                save(args.state, state)
            except (httpx.HTTPError, KeyError):
                print("Event delivery unavailable; retained in local outbox", flush=True)
            if args.once:
                return
            time.sleep(max(0, config["sample_seconds"] - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
