"""fleet health — one deep check for stream, shallow checks for everything else.

Born 2026-08-12, the morning after a day of diagnosing stream by hand. The
things that needed discovering with ad-hoc probes are exactly what this flow
watches:

  - a live tail that serves but does not advance (seq must move between reads)
  - seals that stall ingest (seal duration from /metrics)
  - compaction quietly not running (watermark commit age vs its interval)
  - a wedged consumer-facing surface while /status still says "live"

Design constraints, both learned the hard way:

  - This runs on the home box (heavypad). Everything here is curl-weight HTTP
    GETs — a few KB per run. No websockets, no subscribe windows, no firehose
    sampling from a residential connection.
  - One healthy sample is not proof, and one bad sample is not an incident.
    The stream deep check reads /status twice across a 20s window before
    calling the tail stuck, and any shallow check gets one retry before it
    counts as down.

Failure semantics: a failed flow run means the sweep itself could not run
(a check task errored out even after retry). Unhealthy targets are a
*finding*, not a flow failure — they're logged as warnings, recorded in the
markdown artifact, and emitted as a `fleet-health.unhealthy` event for
automations to page on.

Inventory is packaged with mps; reports use Prefect artifacts already stored by the server.
"""

import time
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

import httpx
import logfire
from mps.inventory import EndpointCheck, load_projects
from mps.observability import configure_logfire
from prefect import flow, get_run_logger, task
from prefect.artifacts import create_markdown_artifact, create_table_artifact
from prefect.cache_policies import NONE
from prefect.events import emit_event
from prefect.states import Completed

TIMEOUT_S = 10
TAIL_WINDOW_S = 20

# the watermark lag (tip minus watermark, in seconds of history) oscillates
# between one and ~two 12h intervals plus a pass duration in healthy steady
# state; sustained growth past 48h is the 2026-08-10 drought signature.
COMPACTION_MAX_LAG_S = 48 * 3600
SEAL_MAX_S = 10.0

# stream holds ingest closed at startup until the tombstone rebuild finishes
# (measured ~4 min on 2026-08-16). A non-advancing tail inside this window is
# a deploy booting, not an outage — report it as "starting", don't page. A
# process that is still stuck past the grace pages on the next sweep, and a
# crash-loop keeps failing the shallow site check regardless.
STARTUP_GRACE_S = 6 * 60


@dataclass
class CheckResult:
    name: str
    healthy: bool
    detail: str
    project: str = "stream"
    url: str | None = None
    status: int = 0
    ms: float = 0
    kind: str = "deep"


def _get(url: str) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": "fleet-health/1"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return resp.status, resp.read()


def _status_fields(body: str) -> dict[str, str]:
    fields = {}
    for line in body.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            fields[" ".join(parts[0].split())] = parts[1].strip()
    return fields


def _status_int(body: str, label: str) -> int | None:
    for line in body.splitlines():
        if line.strip().startswith(label):
            digits = "".join(c for c in line if c.isdigit())
            if digits:
                return int(digits)
    return None


def _uptime_seconds(raw: str | None) -> int | None:
    """Parse /status uptime strings like '38s', '42m', '1h 3m', '11d 6h'."""
    if not raw:
        return None
    total, num = 0, ""
    units = {"d": 86400, "h": 3600, "m": 60, "s": 1}
    for ch in raw:
        if ch.isdigit():
            num += ch
        elif ch in units and num:
            total += int(num) * units[ch]
            num = ""
        elif ch == " ":
            continue
        else:
            return None
    return total if not num else None


def _metric(metrics: str, name: str) -> float | None:
    for line in metrics.splitlines():
        if line.startswith(name) and (line[len(name)] in " {"):
            try:
                return float(line.rsplit(None, 1)[-1])
            except ValueError:
                return None
    return None


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1, cache_policy=NONE)
def check_stream_deep() -> CheckResult:
    """The checks unique to stream: tail advance, seal cost, compaction age."""
    logger = get_run_logger()
    problems: list[str] = []

    st, body_a = _get("https://stream.waow.tech/status")
    if st != 200:
        return CheckResult("stream (deep)", False, f"/status returned {st}")
    seq_a = _status_int(body_a.decode(), "upstream seq")

    time.sleep(TAIL_WINDOW_S)
    _, body_b = _get("https://stream.waow.tech/status")
    text_b = body_b.decode()
    seq_b = _status_int(text_b, "upstream seq")

    starting = False
    uptime_s = _uptime_seconds(_status_fields(text_b).get("uptime"))
    if seq_a is None or seq_b is None:
        problems.append("could not parse upstream seq from /status")
    elif seq_b <= seq_a:
        if uptime_s is not None and uptime_s < STARTUP_GRACE_S:
            # deploy booting: ingest holds until the tombstone rebuild ends
            starting = True
        else:
            # "state live" only means the process serves; the tail is alive
            # only if the seq moved across the window
            problems.append(f"tail not advancing: seq {seq_a} -> {seq_b} over {TAIL_WINDOW_S}s")

    _, metrics_raw = _get("https://stream.waow.tech/metrics")
    metrics = metrics_raw.decode()

    seal_sum = _metric(metrics, "jetstream_segment_seal_duration_seconds_sum")
    seal_count = _metric(metrics, "jetstream_segment_seal_duration_seconds_count")
    if seal_sum is not None and seal_count:
        mean_seal = seal_sum / seal_count
        if mean_seal > SEAL_MAX_S:
            problems.append(f"mean seal duration {mean_seal:.1f}s (> {SEAL_MAX_S}s) this process")

    wm_lag = _metric(metrics, "jetstream_compaction_watermark_lag_seconds")
    if wm_lag is not None and wm_lag > COMPACTION_MAX_LAG_S:
        problems.append(
            f"compaction watermark lag {wm_lag / 3600:.1f}h (> {COMPACTION_MAX_LAG_S / 3600:.0f}h)"
        )

    fields = _status_fields(text_b)
    detail = (
        f"seq +{(seq_b or 0) - (seq_a or 0)}/{TAIL_WINDOW_S}s, "
        f"phase {fields.get('phase', '?')}, uptime {fields.get('uptime', '?')}"
    )
    if starting:
        detail = f"starting (tail held for startup rebuild): {detail}"
    if problems:
        for p in problems:
            logger.warning("stream: %s", p)
        return CheckResult("stream (deep)", False, "; ".join(problems))
    return CheckResult("stream (deep)", True, detail)


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1, cache_policy=NONE)
def check_endpoint(project: str, endpoint: EndpointCheck) -> CheckResult:
    started = time.monotonic()
    options = endpoint.check
    with (
        httpx.Client(timeout=TIMEOUT_S, follow_redirects=True) as client,
        client.stream(
            options.method,
            endpoint.url,
            headers=options.headers,
            content=options.body,
        ) as response,
    ):
        response.raise_for_status()
        return CheckResult(
            endpoint.name,
            True,
            f"HTTP {response.status_code}",
            project,
            endpoint.url,
            response.status_code,
            (time.monotonic() - started) * 1000,
            "endpoint",
        )


def endpoint_finding(project: str, endpoint: EndpointCheck, result) -> CheckResult:
    """Classify a dead endpoint after the task has exhausted its network retries."""
    if isinstance(result, CheckResult):
        return result
    status = result.response.status_code if isinstance(result, httpx.HTTPStatusError) else 0
    return CheckResult(
        endpoint.name,
        False,
        f"{type(result).__name__} after retries",
        project,
        endpoint.url,
        status,
        0,
        "endpoint",
    )


def summarize(results: list) -> tuple[list[str], list[str], list[str]]:
    """Split sweep results into table rows, unhealthy findings, and checks
    that themselves failed to run. Only the last group fails the flow."""
    rows = ["| target | state | detail |", "| --- | --- | --- |"]
    unhealthy: list[str] = []
    broken_checks: list[str] = []
    for r in results:
        if isinstance(r, CheckResult):
            mark = "ok" if r.healthy else "**DOWN**"
            rows.append(f"| {r.name} | {mark} | {r.detail} |")
            if not r.healthy:
                unhealthy.append(f"{r.name}: {r.detail}")
        else:  # task itself errored after retry — the sweep is incomplete
            rows.append(f"| (task error) | **DOWN** | {r} |")
            broken_checks.append(str(r))
    return rows, unhealthy, broken_checks


@flow(log_prints=True)
def fleet_health():
    logger = get_run_logger()
    configure_logfire("prefect-flow-fleet-health")

    deep_future = check_stream_deep.submit()
    checks = [(p.name, endpoint) for p in load_projects() for endpoint in p.services]
    futures = [check_endpoint.submit(project, endpoint) for project, endpoint in checks]
    deep_result = deep_future.result(raise_on_failure=False)
    results = [deep_result]
    results += [
        endpoint_finding(project, endpoint, future.result(raise_on_failure=False))
        for (project, endpoint), future in zip(checks, futures, strict=True)
    ]
    checked_at = datetime.now(UTC).isoformat()
    # A table artifact is the existing persistence path. Hub reads this report;
    # browsing the dashboard never probes the fleet again.
    create_table_artifact(
        key="fleet-health-status",
        table=[
            {**asdict(result), "checkedAt": checked_at}
            for result in results
            if isinstance(result, CheckResult)
        ],
        description="Endpoint availability and Stream deep checks for Hub",
    )

    rows, unhealthy, broken_checks = summarize(results)

    create_markdown_artifact(
        key="fleet-health",
        markdown="\n".join(rows),
        description="latest fleet health sweep",
    )

    for line in rows[2:]:
        logger.info(line)
    if unhealthy:
        logger.warning("%d unhealthy: %s", len(unhealthy), " | ".join(unhealthy))
        emit_event(
            event="fleet-health.unhealthy",
            resource={"prefect.resource.id": "fleet-health"},
            payload={"unhealthy": unhealthy},
        )
    if broken_checks:
        raise RuntimeError(
            f"{len(broken_checks)} check(s) could not run: " + " | ".join(broken_checks)
        )

    if unhealthy:
        logfire.warn("fleet health findings: {findings}", findings=" | ".join(unhealthy))
        logfire.force_flush()
        return Completed(name="Degraded", message=" | ".join(unhealthy))
