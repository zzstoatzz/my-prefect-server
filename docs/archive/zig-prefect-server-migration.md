> archived. this described a plan or a past state and shipped or was superseded; it is kept for the why and does not describe the present.

# Zig Prefect Server Migration Notes

These notes are for adopting the zig `prefect-server` implementation from
`tangled.org/zzstoatzz.io/prefect-server` in this repo. They are intentionally
descriptive: the current deploy path already uses the zig chart, but the details
below are the things to know if another environment is still on the Python
`prefect/prefect-server` Helm chart or if this deployment needs to be rebuilt.

> **Execution topology (current):** the k3s node runs the zig **server + public edge**
> only. All flow deployments now execute on the **`home-pool` process worker** on the
> home box (heavypad), which polls the server outbound over Tailscale
> (`deploy/home-worker/`). The `kubernetes-pool`, the k8s worker, and the "a Kubernetes
> Job is created in the `prefect` namespace" validation step below are **retained but
> unused** — read them only if rebuilding the k8s fallback path.

## Current Shape In This Repo

`just deploy` installs the server from a sibling checkout:

```bash
PREFECT_SERVER_CHART_PATH=${PREFECT_SERVER_CHART_PATH:-../prefect-server/charts/prefect-server}
helm upgrade --install prefect-server "$PREFECT_SERVER_CHART_PATH" \
  --namespace prefect \
  --values -
```

The values file is `deploy/prefect-values.yaml`.

The current topology is:

- `prefect-server` Helm release from the zig chart.
- One webserver replica and one services replica.
- Standalone Postgres from `deploy/prefect-postgres.yaml`.
- Standalone Redis from `deploy/prefect-redis.yaml`.
- Basic auth through `auth.existingSecret: prefect-auth`.
- Traefik ingress at `DOMAIN`.
- Python Kubernetes worker remains unchanged and talks to
  `http://prefect-server.prefect.svc.cluster.local:4200/api`.

The worker image is still `prefecthq/prefect:3-python3.14-kubernetes`. The
server swap does not imply rewriting flows or replacing the worker runtime.

## What Changes From The Python Helm Chart

The zig chart is smaller and more direct than `prefect/prefect-server`, but it
does not include the same subchart stack.

| Area | Python Helm chart | Zig chart/current repo |
|---|---|---|
| Server image | `prefecthq/prefect:*` | pinned `atcr.io/zzstoatzz.io/prefect-server:ReleaseFast-<sha>` |
| API process | Python Prefect API | Zig API binary |
| Background services | Python `backgroundServices` deployment | Zig `services` deployment |
| Postgres | Bitnami subchart option | Bring your own Postgres manifest/secret |
| Redis | Bitnami subchart option | Bring your own Redis manifest/service |
| Docket | Python env URL, easy to miss in HA | Built into the zig services path, configured by broker/backend values |
| UI bundle | Bundled in Python server | Prefect React v2 bundle mounted in the Zig image |
| Worker | Python worker | Still Python worker |
| Prometheus/Grafana | External monitoring stack | kube-prometheus-stack dashboards scrape kube/pod metrics and the Zig server metrics endpoint |

In this repo, Postgres and Redis are deliberately plain standalone manifests
instead of chart dependencies. That avoids Helm subchart ownership issues and
makes the server chart a drop-in API/services install, not a whole platform
install.

## Values And Manifests To Compare

The important local files are:

- `deploy/prefect-values.yaml` — zig chart values.
- `deploy/prefect-postgres.yaml` — standalone DB, DB password secret, and DB URL
  secret.
- `deploy/prefect-redis.yaml` — standalone Redis.
- `deploy/home-worker/` — the current worker (a systemd **process** worker on the home
  box). The old in-cluster `deploy/worker.yaml` Kubernetes worker is gone.
- `deploy/prefect-limits.yaml` — namespace safety net for flow pod resource
  defaults.
- `deploy/dashboards/*.json` and `deploy/monitoring-values.yaml` — Grafana and
  Prometheus configuration.
- `justfile` — install order and environment variables.

The chart path defaults to the sibling checkout. If the repo is not laid out
next to `prefect-server`, set:

```bash
export PREFECT_SERVER_CHART_PATH=/path/to/prefect-server/charts/prefect-server
```

The deployed image is currently:

```yaml
image:
  repository: atcr.io/zzstoatzz.io/prefect-server
  tag: "ReleaseFast-<sha>"  # current tag lives in deploy/prefect-values.yaml (bumped every publish-server-remote)
```

`just publish-server-remote` builds the server on the Hetzner node and updates
the live deployments to `ReleaseFast-<git-sha>`. Keep this values file pinned to
the same tag after a successful rollout so a later `just deploy` cannot roll the
server backward.

## Database And Redis Details

The zig server supports SQLite locally, but this repo uses Postgres in the
cluster:

```yaml
config:
  database:
    backend: postgres
    existingSecret: prefect-db-url
    existingSecretKey: url
```

`deploy/prefect-postgres.yaml` creates `prefect-db-url` with:

```text
postgresql://prefect:POSTGRES_PASSWORD@prefect-postgres:5432/prefect
```

Redis is configured as the broker backend:

```yaml
config:
  broker:
    backend: redis
    redis:
      host: prefect-redis
      port: 6379
      db: 0
```

The current standalone Redis manifest does not enable auth because it is only
reachable through in-cluster DNS in the `prefect` namespace. If Redis auth is
added later, check the zig chart/server support before assuming the Python
`redis://:password@host:6379/db` shape maps one-for-one.

## Auth, API URL, And Workers

The public API remains:

```text
https://$DOMAIN/api
```

The in-cluster API remains:

```text
http://prefect-server.prefect.svc.cluster.local:4200/api
```

That is why the existing worker manifest can stay simple:

```yaml
- name: PREFECT_API_URL
  value: http://prefect-server.prefect.svc.cluster.local:4200/api
- name: PREFECT_API_AUTH_STRING
  valueFrom:
    secretKeyRef:
      name: prefect-auth
      key: auth-string
```

Flow code can keep using Python Prefect clients, `.deploy()`, `flow.from_source`,
Kubernetes work pools, and the existing `uv run --with ...` job commands. The
server is swapped under the API surface; the execution runtime remains Python.

## UI Surface

The current Zig server image carries only the Prefect React v2 UI bundle. The
old UI is intentionally not bundled. In `deploy/prefect-values.yaml`, the chart
sets:

```yaml
config:
  ui:
    staticDir: /app/ui
    serveBase: /
```

So `https://$DOMAIN/` serves the Prefect v2 SPA and `/api` remains the API base.
The root `/ui-settings` endpoint returns the UI settings object with
`default_ui: "v2"` and `auth: "BASIC"` when server auth is enabled. Clients and
workers should still target `/api`.

## Data Migration Notes

Treat Python-to-zig server migration as a server implementation swap, not as a
guaranteed in-place schema migration.

The zig server initializes and uses its own schema shape. It is not Alembic and
does not replay Python Prefect migration history. For a low-risk adoption path,
use a fresh Postgres database/PVC for the zig server, then re-register
deployments from this repo.

Things that are naturally recreated:

- flow deployments from `prefect.yaml` / deployment scripts;
- Kubernetes work pool and queue configuration;
- block documents, variables, and concurrency limits if recreated through CLI or
  API;
- flow run history from new runs after the cutover.

Things that should be explicitly considered before switching an existing Python
database:

- historical flow/task run rows;
- automation definitions;
- block documents and secrets;
- variables;
- work pools, work queues, and base job templates;
- event/log history.

This repo keeps the Kubernetes work pool base job template as checked-in JSON at
`deploy/work-pools/kubernetes-pool-base-job-template.json`. A fresh-server
cutover should run `just storage` before expecting flow run jobs to land in the
`prefect` namespace; that creates the shared storage resources and applies the
work pool template with `prefect work-pool update --base-job-template`.

## Docket/Background Services

The original Python HA notes in `docs/archive/journey.md` call out that Python Prefect
needs `PREFECT_SERVER_DOCKET_URL` wired to Redis for proper HA background
coordination. That was a Python Helm chart footgun.

For the zig server, Docket is part of the server implementation. The current
`prefect-server` repo pins its zig `docket` dependency to a fixed commit in
`build.zig.zon`; this deployment is on a server build that includes Redis
reconnect handling for dropped service-worker sockets. The chart values do not
need the old Python `extraEnvVarsCM: prefect-docket-config` workaround.

The practical verification is logs, not just pod readiness:

```bash
kubectl logs -n prefect -l app.kubernetes.io/component=services --tail=100
```

Look for Docket and services startup lines such as scheduler, late runs,
automations, cancellation cleanup, and the docket worker.

## Validation Checklist

Useful checks after deploying or cutting over:

```bash
just health
just status
just logs
just prefect work-pool ls
just prefect deployment ls
```

Also check the in-cluster components directly:

```bash
kubectl get pods -n prefect
kubectl get svc -n prefect
kubectl logs -n prefect deploy/prefect-worker --tail=100
kubectl logs -n prefect -l app.kubernetes.io/component=webserver --tail=100
kubectl logs -n prefect -l app.kubernetes.io/component=services --tail=100
```

The zig chart labels the two server deployments with
`app.kubernetes.io/component=webserver` and
`app.kubernetes.io/component=services`; both share
`app.kubernetes.io/name=prefect-server`.

Then run or schedule a small diagnostic deployment and verify:

- the worker sees the work pool;
- a Kubernetes Job is created in the `prefect` namespace;
- the flow run reaches `Completed`;
- logs appear in the API/UI/CLI;
- result storage still writes to the expected PVC if the flow uses it.

## Rollback Shape

The current setup keeps Postgres and Redis outside the server chart. That makes
rollback mostly a Helm release decision if both server implementations are using
fresh/separate DB state.

For a clean rollback plan:

- keep the previous Python Helm values somewhere recoverable;
- use a separate database/PVC for Python and zig if historical data matters;
- keep the same `prefect-auth` secret if clients/workers should not change
  credentials;
- keep the in-cluster service name stable only for the active implementation;
- re-run `just worker` or restart the worker if the API service endpoint moves.

If an environment attempts to share a Python-created database with the zig
server, rollback and forward migration become much harder. Prefer a fresh DB
until schema compatibility is explicitly proven for that exact server version.

## Performance Context

The current zig server benchmark refresh in the sibling `prefect-server` repo
compared all three topologies against Python Prefect:

- local: zig 170.7 rps, p99 15.46ms; python 43.8 rps, p99 29.65ms.
- single: zig 150.5 rps, p99 90.62ms; python 19.7 rps, p99 166.08ms.
- HA: zig 144.0 rps, p99 128.20ms; python 50.5 rps, p99 135.94ms.

The useful reading for this repo is not "latency is solved forever." It is:

- zig stayed error-free in the benchmark runs;
- Python still returned 503s/timeouts under load;
- RSS/image size are much smaller with the zig server;
- the Postgres/Redis p99 tail is still worth watching in this topology.

For details, see `../prefect-server/docs/perf-zig-vs-python.md` from a sibling
checkout.
