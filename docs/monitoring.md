# Monitoring on the single-node cluster

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
