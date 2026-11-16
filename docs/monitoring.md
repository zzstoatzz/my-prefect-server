# monitoring

## Evergreen operational observations

The existing `fleet-health` deployment reads opt-in `monitoring: "observe"`
fields on Fly app resources in Evergreen's published inventory. This first
stage records current VM memory headroom and retained machine exit evidence
in the normal `fleet-health` artifact and logs. It does not emit additional
alerts, change availability verdicts, or treat collection errors as healthy.
Unavailable observations are explicitly `UNKNOWN`; their task retains its
failure and retries independently of the existing availability checks.

The collector uses the existing `fly-api-token` Secret block for GET requests
to the Machines and Prometheus APIs. No token is copied into Evergreen's
public Worker or inventory. Apps without an explicit opt-in are not queried.
Requested stops are excluded from process-failure findings. OOMs are identified
only by Fly's `oom_killed` flag; a plain exit 137 is not asserted to be an OOM.

Failures remain visible for 24 hours while Fly retains the event, even when
the service is already returning HTTP 200. Saved artifacts preserve observations
that were collected, but Fly's bounded event history is not a complete restart
ledger: rapid loops, replacement machines, and deployment transitions can hide
events between sweeps. Memory is VM available memory, not process RSS or a
leak diagnosis. Missing, stale, contradictory, and partial memory data must not
be interpreted as capacity headroom.

Add alerting only after reviewing real observation windows, expected stops,
provider gaps, notification deduplication, and recovery behavior. An observation
does not authorize restarts or resource changes. Evergreen owns the candidate
assessment and cohort in `docs/operational-checks.md` and `site/services.json`.

## HeavyPad storage alerts

Grafana is a dashboard surface; alert rules for HeavyPad storage live in Logfire,
project `zig-prefect-server`. No Grafana alert rules or notification contacts were
created. The host's existing bandwidth watchdog now sends filesystem samples
once a minute, independent of Prefect flow scheduling. Physical filesystems are
deduplicated, so `/` and the orchestration Postgres storage path currently produce
one sample. Available space excludes filesystem-reserved blocks.

On October 27 at 00:11 UTC the shared filesystem had 1,017,106,341,888 available
bytes, 51.8% free space and 92.3% free inodes. The Postgres database measured
21 MB. Its `20Gi` local-path PVC is a declaration, not an enforced separate
filesystem quota; the host filesystem is the capacity boundary to monitor.

| Rule | Condition | Logfire ID |
| --- | --- | --- |
| heavypad disk space warning | Below 15% or 100 GiB available, or below 10% free inodes; excludes critical conditions | `8a9401ae-3071-4534-9d1e-ee56c6c09cb8` |
| heavypad disk space critical | Below 5% or 30 GiB available, or below 2% free inodes | `e5d9cb95-8939-47ed-a703-e8081d3152ca` |
| heavypad disk telemetry missing | No disk sample in five minutes | `178036c5-37b1-4ee1-b9db-44303721201c` |

Rules evaluate every five minutes and notify on matching-state changes through
the existing `phi alert watch (push)` channel. Source receipts and queries are
`deploy/bandwidth-watchdog/logfire-disk-*.json`; update the recorded IDs rather
than create duplicate rules. Live query checks returned healthy, and all three
scheduled evaluations completed without query errors. SQL boundary tests exercised
healthy, warning, critical-byte and critical-inode cases without injecting fake
production telemetry or sending a test notification. The filesystem collector's
25 tests passed, including real-filesystem deduplication and missing-path failure.

The watchdog's startup token lookup and event outbox target the home Prefect
server through the canonical hostname. Its Logfire write block and host-event
automations were transferred and verified before Hetzner retirement. Once initialized, disk
samples are emitted to Logfire before Prefect event delivery and continue when
that delivery is unavailable. This is capacity monitoring, not automatic cleanup.

## Grafana and Prometheus

Production uses the home user services in [prefect-edge](../deploy/prefect-edge/),
with immutable images in `services.toml`. `scripts/run_prefect_edge.py` runs
Grafana and Prometheus against their persistent home state. Prometheus keeps
90 days; its scrape configuration is rendered by `just edge-secrets` from
`scripts/prefect_edge_secrets.py`. Use `--apply` only for an intended change.

Grafana's data, provisioning and analytics snapshot live under
`~/.local/share/prefect-edge/monitoring`. The
`prefect-grafana-snapshot.timer` updates the analytics copy; see
[snapshot_grafana_analytics.py](../scripts/snapshot_grafana_analytics.py).
This is a local query snapshot, not an offsite backup. The committed host backup
does not include the monitoring state directory; see the
[recovery coverage and gaps](heavypad-outage-runbook.md).
The repository's custom dashboards remain in `deploy/dashboards/`. Inspect the
installed provisioning paths before changing them; the retired Helm ConfigMap
commands no longer manage production.

After a monitoring change, check target health, rule/query errors and actual
Grafana queries. Lower scrape volume alone does not establish lower retained
memory usage. Monitor the host's independent bandwidth/storage watchdog as well
as Prefect's scheduled checks; a dead control plane cannot report its own loss.
