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

## step 10: ephemeral flow environments with `uv run --with`

the original approach baked dependencies into the worker image or tried to install them during pull steps. this doesn't work because pull steps run *after* the container process starts — so the process needs deps before pull steps can provide them.

the solution is overriding the `command` job variable to use `uv run --with`:

```yaml
definitions:
  work_pools:
    k8s: &k8s
      name: kubernetes-pool
      job_variables:
        command: >-
          uv run
          --with 'my-prefect-server @ git+https://github.com/zzstoatzz/my-prefect-server.git'
          prefect flow-run execute
```

this creates an ephemeral venv per flow run with the project and all its deps installed, before `prefect flow-run execute` invokes pull steps. uv is pre-installed in the `prefecthq/prefect:3-python3.14-kubernetes` image.

references: [discussion #21185](https://github.com/PrefectHQ/prefect/discussions/21185), [discussion #18223](https://github.com/PrefectHQ/prefect/discussions/18223).

### per-deployment python version overrides

dbt-core doesn't support python 3.14 yet (mashumaro + pydantic v1 shim issues). rather than downgrading everything, the enrich deployment overrides `command` to use `--python 3.13`:

```yaml
- name: enrich
  entrypoint: flows/enrich.py:enrich
  work_pool:
    name: kubernetes-pool
    job_variables:
      command: >-
        uv run --python 3.13
        --with 'my-prefect-server @ git+https://github.com/zzstoatzz/my-prefect-server.git'
        prefect flow-run execute
```

uv downloads cpython 3.13 on-demand in the pod. this required lowering `requires-python` to `>=3.13` in both root and `packages/mps` pyproject.toml files.

### pitfall: `job_variables` placement

`job_variables` must go under `work_pool`, not at the deployment root level. prefect silently ignores it in the wrong location.

### pitfall: tangled.sh git protocol errors

`git+https://tangled.sh/...` intermittently fails with `fatal: protocol error: bad pack header` during uv resolution. using the github mirror (`git+https://github.com/...`) in the `--with` URL avoids this. the pull step still tries tangled.sh first (with github fallback), which works fine since git clone is more tolerant.

## step 11: tangled.org as a second hub data source

added tangled.org issues/PRs alongside github as a data source for the hub (hub.waow.tech).

### data pipeline

1. **`flows/tangled_items.py`** — fetches issues, PRs, and comments from the PDS (`pds.zzstoatzz.io`) via `com.atproto.repo.listRecords`. no auth needed — PDS records are public. resolves repo names from `sh.tangled.repo` records.
2. **`packages/mps/src/mps/tangled.py`** — models (`TangledItem`), PDS fetch helpers, URL builders for tangled.org
3. **`packages/mps/src/mps/db.py`** — `write_tangled_items()` persists to `raw_tangled_items` table in DuckDB
4. **dbt models**:
   - `stg_tangled_items` — dedup view, excludes comments
   - `int_tangled_items_scored` — scoring (recency only, no engagement data from PDS)
   - `hub_action_items` — cross-source union mart replacing `github_action_items`
5. **hub frontend** — updated to read from `hub_action_items`, handles both sources with appropriate URL helpers

### what we learned about tangled/AT Protocol data

- issue/PR **state (open/closed) is NOT on the PDS** — the knot/appview tracks it server-side. `sh.tangled.repo.issue.state` records exist but are empty.
- the `repo` field on issues/PRs is an `at://` URI, not a name — need to resolve via `sh.tangled.repo` records
- labels are tracked via `sh.tangled.label.op` records (add/delete operations on AT URI subjects)
- activity volume is very low currently — full resync each run is fine

## step 12: event-driven pipeline with deployment triggers

replaced the staggered cron schedules on `enrich` and `curate` with deployment triggers (prefect automations). the data flows (`gh-notifications` at :00, `tangled-items` at :02) stay on cron. downstream flows fire reactively:

- `enrich` triggers on `tangled-items` completion — by that point both data sources have written to DuckDB
- `curate` triggers on `enrich` completion — the mart is fresh

this eliminates wasted pods: no enrich/curate run unless upstream actually finished. previously, cron offsets were a guess (`:05`, `:10`) — if enrich took longer than 5 minutes, curate would run on stale data.

the `curate` flow also gained a `ByItemsContent` cache policy that hashes the items text + system prompt. if the scored items haven't changed, the LLM call is skipped entirely (4h cache expiration). combined with event-driven triggering, curate only spins up a pod when there's new data, and only calls haiku when the data actually changed.

defined in `prefect.yaml` as deployment triggers:
```yaml
triggers:
  - type: event
    expect:
      - "prefect.flow-run.Completed"
    match_related:
      prefect.resource.name: "tangled-items"
      prefect.resource.role: "deployment"
```

these are syntactic sugar — `prefect deploy` converts each trigger into a full `Automation` with a `RunDeployment` action targeting the owning deployment.

**gotcha**: `prefect deploy` creates the automation but does NOT remove old cron schedules. must manually pause them via `prefect deployment schedule pause`.

## what's next

- file upstream issue on prefecthq/prefect-helm for docket URL
- contribute guide back to prefect docs
- upstream: `job_variables` placement should be validated or warned about (silent ignore is surprising)
- upstream: consider a prefect recipe/docs page for the `uv run --with` pattern on kubernetes work pools
- upstream: `prefect deploy` should remove schedules when switching to triggers-only
