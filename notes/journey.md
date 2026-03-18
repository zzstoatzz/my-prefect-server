# self-hosted prefect on hetzner: build notes

these notes capture the full journey of deploying a self-hosted Prefect OSS server from scratch. the goal is to feed these back into the Prefect docs as an end-to-end guide.

## starting point

nothing. empty directory, fresh hetzner project with an API token.

## step 1: hetzner VM with k3s

provisioned a single VM using terraform's hcloud provider, following the `relay/indigo` pattern from our other projects.

- **server type**: `cpx21` (3 vCPU, 4 GB RAM, ~€4/mo). note: `cx22` doesn't exist at the `ash` (ashburn) datacenter — shared AMD (`cpx`) vs shared Intel (`cx`) naming.
- **cloud-init**: installs k3s with `--tls-san $PUBLIC_IP`, waits for readiness, touches `/run/k3s-ready` as a signal file
- **firewall**: ports 22 (SSH), 80/443 (HTTP/HTTPS), 6443 (k8s API)
- **kubeconfig**: fetched via `scp`, rewritten to use the public IP instead of `127.0.0.1`

### pitfall: we initially tried bare Docker containers (not k3s)

the original plan called for Docker containers with Caddy as a reverse proxy. this worked but was a dead end for monitoring — the Prefect Grafana dashboards use Kubernetes-specific PromQL queries (recording rules, `namespace=`, `pod=` labels). we tore everything down and rebuilt on k3s.

**lesson**: if you want Prefect's official monitoring dashboards, run on Kubernetes.

## step 2: deploying the prefect stack via helm

the `just deploy` recipe handles all of this in one shot:

1. **cert-manager** — `jetstack/cert-manager` helm chart with CRDs enabled, plus a `ClusterIssuer` for Let's Encrypt (HTTP-01 via traefik)
2. **prefect auth secret** — `kubectl create secret generic prefect-auth` with the `AUTH_STRING` (format: `user:pass`)
3. **prefect server** — `prefect/prefect-server` helm chart:
   - 2 API server replicas (`server.replicaCount: 2`)
   - background services as a separate deployment (`backgroundServices.runAsSeparateDeployment: true`)
   - postgresql (bitnami subchart, 10Gi persistent volume)
   - redis (bitnami subchart, standalone mode)
   - basic auth enabled, referencing the `prefect-auth` secret
   - traefik ingress with cert-manager TLS annotation
   - `uiConfig.prefectUiApiUrl` set to the public HTTPS URL
4. **monitoring** — `prometheus-community/kube-prometheus-stack`:
   - lightweight config: alertmanager disabled, most kube component monitors disabled
   - prometheus: 30s scrape interval, 14d retention, 10Gi storage
   - grafana: sidecar enabled for auto-discovering dashboard ConfigMaps (label: `grafana_dashboard: "1"`)
   - node-exporter + kube-state-metrics enabled
5. **prefect dashboards** — loaded from `deploy/dashboards/*.json` as ConfigMaps with the grafana sidecar label
6. **prefect exporter** — `prefect/prometheus-prefect-exporter` helm chart, pointed at the in-cluster prefect API URL

### pitfall: helm secret ownership

if you manually `kubectl create secret` something that a helm chart wants to manage (like the postgresql password secret), helm will fail because the secret lacks helm ownership labels. solution: let helm manage its own secrets and pass values via `--set postgresql.auth.password=...`.

### pitfall: DNS caching blocks cert-manager

cert-manager's HTTP-01 solver needs to resolve your domain. if the node's systemd-resolved is caching a previous NXDOMAIN (from before you set the DNS record), cert-manager will fail. fix:

```bash
resolvectl dns eth0 8.8.8.8 1.1.1.1
resolvectl flush-caches
kubectl rollout restart deploy/coredns -n kube-system
```

## step 3: terraform for prefect resources

a second terraform layer (`prefect/`) uses the `prefecthq/prefect` provider to manage server-level resources:

```hcl
provider "prefect" {
  endpoint       = "https://${var.domain}/api"
  basic_auth_key = var.auth_string  # same user:pass format
}

resource "prefect_work_pool" "k8s" {
  name = "kubernetes-pool"
  type = "kubernetes"
}
```

individual flow deployments are NOT managed by terraform — they're registered from code (see step 5).

### pitfall: work pool namespace default

the kubernetes work pool's base job template defaults `namespace` to `default`. if your worker's RBAC is scoped to the `prefect` namespace (as it should be), flow run jobs will fail with 403s. fix:

```bash
prefect work-pool get-default-base-job-template --type kubernetes > template.json
# edit template.json: set variables.properties.namespace.default to "prefect"
prefect work-pool update kubernetes-pool --base-job-template template.json
```

## step 4: kubernetes worker

deployed as a manual k8s Deployment (not the helm chart, which is oriented toward Cloud):

- **image**: `prefecthq/prefect:3-python3.11-kubernetes` — the `-kubernetes` tag includes the `kubernetes` python package. the base `3-latest` image does NOT.
- **RBAC**: namespace-scoped Role + RoleBinding in `prefect` namespace, matching the official helm chart's permissions:
  - `events, pods, pods/log, pods/status` — get, watch, list
  - `jobs` — get, list, watch, create, update, patch, delete
- **critical env var**: `PREFECT_INTEGRATIONS_KUBERNETES_OBSERVER_NAMESPACES=prefect` — the kubernetes worker uses kopf, which watches at cluster scope by default. this env var restricts it to a single namespace, so a namespace-scoped Role works (no ClusterRole needed).

### pitfall: kopf cluster-scope watching

without `PREFECT_INTEGRATIONS_KUBERNETES_OBSERVER_NAMESPACES`, the worker tries to list/watch all jobs and pods cluster-wide. with a namespace-scoped Role, this produces 403 errors. the fix is NOT to escalate to a ClusterRole — it's to tell the worker which namespace to watch.

## step 5: deploying flows

the key pattern is `flow.from_source()` + `.deploy()`:

```python
from prefect import flow

if __name__ == "__main__":
    flow.from_source(
        source="https://tangled.sh/zzstoatzz.io/my-prefect-server.git",
        entrypoint="flows/diagnostics.py:diagnostics",
    ).deploy(
        name="diagnostics-every-5m",
        work_pool_name="kubernetes-pool",
        cron="*/5 * * * *",
    )
```

- `from_source()` tells Prefect to use `git_clone` as the pull step — the worker clones the repo at runtime
- without `from_source()`, `.deploy()` tries to build a Docker image with your code baked in
- each flow run gets its own k8s Job/Pod — clean isolation, no dependency pollution
- flow code stays in git, not in the worker image or configmaps
- registration runs from a local machine (eventually CI): `uv run --with prefect flows/diagnostics.py`

## architecture summary

```
┌─────────────────────────────────────────────────────┐
│ hetzner cpx21 (k3s)                                 │
│                                                     │
│  prefect namespace:                                 │
│    ├── prefect-server (2 replicas) ─── traefik ─── TLS │
│    ├── prefect-background-services (1 replica)      │
│    ├── postgresql (1 replica, 10Gi PVC)             │
│    ├── redis (1 replica, standalone)                │
│    ├── prefect-worker (1 replica)                   │
│    ├── prometheus-prefect-exporter                  │
│    └── flow run jobs (ephemeral, per-run)           │
│                                                     │
│  monitoring namespace:                              │
│    ├── prometheus                                   │
│    ├── grafana ─── traefik ─── TLS                  │
│    ├── node-exporter                                │
│    └── kube-state-metrics                           │
│                                                     │
│  cert-manager namespace:                            │
│    └── cert-manager (ACME/Let's Encrypt)            │
└─────────────────────────────────────────────────────┘
```

## step 6: redis/docket configuration

docket is Prefect's coordination layer for background services (scheduler, late run detection, automation triggers). it's configured via `PREFECT_SERVER_DOCKET_URL` and defaults to `memory://`, which only works for single-process deployments.

the helm chart auto-configures all the other Redis env vars (`PREFECT_MESSAGING_BROKER`, `PREFECT_MESSAGING_CACHE`, etc.) but does NOT set `PREFECT_SERVER_DOCKET_URL`. this means HA deployments silently run docket in-memory mode.

### workaround: ConfigMap for docket URL

created a ConfigMap with `PREFECT_SERVER_DOCKET_URL` and referenced it via `extraEnvVarsCM` on both `server` and `backgroundServices`:

```yaml
# in prefect-values.yaml
server:
  extraEnvVarsCM: prefect-docket-config
backgroundServices:
  extraEnvVarsCM: prefect-docket-config
```

### pitfall: Redis auth in docket URL

initial attempt used `redis://redis-host:6379/1` — failed with `redis.exceptions.AuthenticationError`. the bitnami Redis subchart enables auth by default. the URL must include the password:

```
redis://:${REDIS_PASSWORD}@prefect-server-redis-master.prefect.svc.cluster.local:6379/1
```

verified docket is working by checking Redis db 1:
```bash
kubectl exec -it -n prefect prefect-server-redis-master-0 -- redis-cli -a "$PASS" -n 1 KEYS '*'
# shows docket coordination keys
```

### TODO: file issue on prefecthq/prefect-helm

**the `prefect-server` helm chart should configure `PREFECT_SERVER_DOCKET_URL` automatically.**

when `redis.enabled: true`, the chart should set `PREFECT_SERVER_DOCKET_URL` to `redis://<redis-host>:6379/1` (separate db from messaging, matching the docker-compose example in the self-hosted docs). this belongs in the `backgroundServices.envVars` helper in `_helpers.tpl`.

**action: create an issue at https://github.com/PrefectHQ/prefect-helm/issues with the above.**

## step 7: executive grafana dashboard

built a single-pane dashboard (`deploy/dashboards/executive-overview.json`) combining infra + prefect overview:

**prefect section:**
- stat panels: flows, deployments, work pools, workers, avg run time, active runs
- pie chart: flow runs by state (color-coded — green/completed, red/failed, blue/running, etc.)
- table: deployments with name, flow, status, work pool

**infrastructure section:**
- gauge panels: node CPU %, memory %, disk %
- stat panel: pods not ready (Failed|Unknown only — initially counted Succeeded pods from completed Jobs, which inflated the number to 51)
- time series: per-pod CPU and memory usage in the prefect namespace

dashboards are loaded as ConfigMaps with the `grafana_dashboard: "1"` label, auto-discovered by the grafana sidecar.

### pitfall: pods-not-ready counting completed Jobs

initial query counted all pods not in `Running` or `Succeeded` phase. but completed flow run Jobs leave pods in `Succeeded` state, and a broad "not ready" filter caught them. fix: only count `Failed|Unknown` phases:
```promql
count(kube_pod_status_phase{namespace=~"prefect|monitoring", phase=~"Failed|Unknown"}) or vector(0)
```

## step 8: memory optimization

the cpx21 node (4 GB RAM) was at ~80% memory usage. biggest consumers:
- grafana: ~340 Mi
- prometheus: ~301 Mi
- prefect API servers (2 replicas): ~170 Mi each

optimizations applied:
1. scaled API server to 1 replica (sufficient for this use case)
2. increased prometheus scrape interval from 30s to 60s
3. reduced prometheus retention from 14d to 7d
4. set explicit resource limits on prometheus (512Mi), grafana (256Mi), prometheus-operator (128Mi)

result: ~73% memory usage, stable.

## step 9: justfile as the operations interface

early on, we were running long ad-hoc `kubectl` commands. consolidated everything into justfile recipes to keep operations repeatable:

- `just logs [component]` — parameterized log tailing (defaults to prefect-server)
- `just prefect *args` — proxy for running prefect CLI against the remote server
- `just dashboards` — reload grafana dashboards from `deploy/dashboards/`
- `just status` — `kubectl top nodes`, `kubectl top pods`, pod status for prefect + monitoring namespaces

**lesson**: if you're running it more than once, put it in the justfile.

## architecture summary

```
┌─────────────────────────────────────────────────────┐
│ hetzner cpx21 (k3s)                                 │
│                                                     │
│  prefect namespace:                                 │
│    ├── prefect-server (1 replica) ─── traefik ─── TLS │
│    ├── prefect-background-services (1 replica)      │
│    ├── postgresql (1 replica, 10Gi PVC)             │
│    ├── redis (1 replica, standalone)                │
│    ├── prefect-worker (1 replica)                   │
│    ├── prometheus-prefect-exporter                  │
│    └── flow run jobs (ephemeral, per-run)           │
│                                                     │
│  monitoring namespace:                              │
│    ├── prometheus (60s scrape, 7d retention)        │
│    ├── grafana ─── traefik ─── TLS                  │
│    ├── node-exporter                                │
│    └── kube-state-metrics                           │
│                                                     │
│  cert-manager namespace:                            │
│    └── cert-manager (ACME/Let's Encrypt)            │
└─────────────────────────────────────────────────────┘
```

## what's next

- CI for flow registration on tangled (.tangled CI, not github actions)
- more interesting deployments with automations
- file upstream issue on prefecthq/prefect-helm for docket URL
- contribute guide back to prefect docs
