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

## TODO: file issue on prefecthq/prefect-helm

**the `prefect-server` helm chart does not configure `PREFECT_SERVER_DOCKET_URL`.**

when `redis.enabled: true` and `backgroundServices.runAsSeparateDeployment: true`, the chart automatically sets all the other Redis env vars:
- `PREFECT_MESSAGING_BROKER`
- `PREFECT_MESSAGING_CACHE`
- `PREFECT_SERVER_EVENTS_CAUSAL_ORDERING`
- `PREFECT_SERVER_CONCURRENCY_LEASE_STORAGE`
- `PREFECT_REDIS_MESSAGING_HOST` / `PORT` / `DB` / `PASSWORD`

but it does NOT set `PREFECT_SERVER_DOCKET_URL`, which defaults to `memory://` (single-server only). this means HA deployments using the helm chart have docket silently running in-memory mode, which breaks coordination of background services like the scheduler, late run detection, and automation triggers.

the fix should be: when `redis.enabled: true`, automatically set `PREFECT_SERVER_DOCKET_URL` to `redis://<redis-host>:6379/1` (using a separate db number from messaging, matching the docker-compose example in the self-hosted docs). this belongs in the `backgroundServices.envVars` helper in `_helpers.tpl`.

the current workaround is creating a separate ConfigMap with the env var and referencing it via `extraEnvVarsCM` on both `server` and `backgroundServices`, which is unnecessarily complex for what should be a built-in setting.

**action: create an issue at https://github.com/PrefectHQ/prefect-helm/issues with the above.**

## what's next

- custom "executive" grafana dashboard — single pane with infra + prefect overview
- CI for flow registration on tangled (.tangled CI, not github actions)
- contribute guide back to prefect docs
