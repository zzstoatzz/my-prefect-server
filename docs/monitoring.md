# Monitoring on the single-node cluster

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

The watchdog's startup token lookup and event outbox still target the original
Prefect server. Transfer that client configuration, required Secret block and
heartbeat/event automations before retiring Hetzner. Once initialized, disk
samples are emitted to Logfire before Prefect event delivery and continue when
that delivery is unavailable. This is capacity monitoring, not automatic cleanup.

## Grafana and Prometheus

Apply the monitoring release with `just monitoring`. It first runs
`just dashboards`, then upgrades the pinned kube-prometheus-stack chart
87.18.1 and waits for readiness. Values live in
`deploy/monitoring-values.yaml`; the Grafana image is pinned to a digest.

Grafana mounts dashboard and datasource ConfigMaps directly, without the two
persistent Kubernetes watcher sidecars. `just dashboards` replaces the personal
dashboard inventory from `deploy/dashboards/`; directory mounts and the file
provider propagate additions, edits, and deletions without a Grafana restart.
The 22 bundled dashboard mounts are explicit, so check them when upgrading the
chart. Datasource changes require a rollout; `just monitoring` hashes the values
into the Grafana pod annotations to trigger it.

Prometheus retains seven days of data. The kubelet scrape drops `apiserver_*`
and `etcd_*` collectors exposed by k3s: the separate control-plane scrapes are
disabled and those extra series do not feed the retained kubelet dashboards.
cAdvisor runs every 30 seconds, and Grafana's scrape keeps its dashboard inputs
and Go/process health metrics. Prefect process metrics, container memory/CPU,
pod restart metrics, and the existing recording/alert rules remain enabled.

After changing filters, compare live scrape sample counts and active series,
check rule evaluation errors, query both Grafana datasources, and confirm all
25 dashboard UIDs remain available. A reduction in active series precedes a
reduction in Prometheus head-series memory: old series remain until head
compaction. Do not lower its memory limit based solely on an immediate scrape
count or restart it just to demonstrate a smaller footprint.

Prometheus 3.13.1 also supports experimental early stale-series compaction.
We set `staleSeriesCompactionThreshold: "0.5"` so that when half the head
series are stale, Prometheus persists their history in blocks and removes
their in-memory entries ahead of normal compaction. This does not delete
historical samples. The chart's CRD upgrade hook keeps the Kubernetes schema
aligned with its operator; the older schema silently omitted this field.
Chart 87.18.1 needs this TSDB setting through `additionalConfig`, with the
chart's default `tsdb` map disabled to avoid duplicate keys.

Verified in Helm revision 11: at 01:37 UTC on September 7, early compaction
wrote 54,510 stale series to a block in 581 ms. Head series fell to 22,739;
the historical control-plane query at 00:40 UTC still returned the same
40,645 series. All 13 scrape targets remained healthy, all 182 rules evaluated
successfully, and Prometheus stayed at zero restarts. After garbage collection,
the container working set measured 266 MiB (324 MiB before this operation).

## Existing analytics datasource issue

The DuckDB snapshot is mounted read-only, but the existing datasource opens
it in write mode, so analytics queries fail. During the September 6 cleanup,
enabling read-only connections without a memory bound triggered a Grafana OOM;
32 MB and 64 MB DuckDB buffer bounds instead returned controlled memory errors
for the dashboard's issue-count query. The datasource configuration was restored
to its prior state. Repairing it requires sizing and testing the database/plugin
workload separately; it is not fixed by this monitoring cleanup.

## September 6, 2026 verification

Helm revision 9 completed with the original DuckDB connection settings restored.
Active series fell from approximately 70,000 to 22,330; all 13 scrape targets
were healthy and all 182 rules evaluated without errors. All 25 dashboard UIDs
remained available, and a Grafana Prometheus query returned both Prefect process
RSS series. cAdvisor's live interval was 30 seconds. The final Grafana pod had
two containers and zero restarts at verification. Removing the two former
watchers eliminated approximately 148 MiB of their observed steady memory use.
Prometheus still used 435 MiB immediately after the changes; head compaction
and subsequent memory measurements are needed to quantify its RAM savings.
