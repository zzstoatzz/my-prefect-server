# operations

standing the system up, and the recipes that run it day to day. every command
is a `justfile` recipe; `just --list` is the full surface. `just` loads `.env`,
so nothing here needs variables exported in the shell.

## credentials

copy `.env.example` to `.env` and fill in `HCLOUD_TOKEN`, `POSTGRES_PASSWORD`,
`AUTH_STRING`, `DOMAIN`, `LETSENCRYPT_EMAIL`, and optionally `GRAFANA_DOMAIN`.

- `AUTH_STRING` (`user:pass`) is the Prefect API admin credential. the server
  enforces basic auth on `/api/*`; `just prefect <args>` injects it for the CLI.
- flow runtime secrets (`ANTHROPIC_API_KEY`, `TURBOPUFFER_API_KEY`,
  `CLOUDFLARE_API_TOKEN`, `TURSO_*`) are Prefect Secret blocks, not `.env`.
  `prefect.yaml` references them as `prefect-block://` values in
  `job_variables.env`, resolved when a run starts. flow code never calls the
  Secret API.
- `kubeconfig.yaml` at the repo root (gitignored) is what `kubectl` and every
  cluster recipe use. `just kubeconfig` fetches it after the server boots.

## the control plane (hetzner VM)

```sh
just init              # terraform init
just infra             # create the VM with k3s
just kubeconfig        # wait for k3s, fetch kubeconfig.yaml
just deploy            # cert-manager, prefect-server (zig chart), postgres + redis, monitoring, dashboards
just storage           # analytics hostPath + results PVC, kubernetes-pool base job template
```

then point DNS at `just server-ip`: `$DOMAIN`, `$GRAFANA_DOMAIN` (default
`prefect-metrics.waow.tech`), and `hub.waow.tech`.

the prefect server is the zig port at
[prefect-server](https://tangled.org/zzstoatzz.io/prefect-server), built on
the node and imported into k3s by `just publish-server-remote`. after a server
deploy, `just verify-deploy` waits for a flow run to *complete*, because pods
Running and `/health` 200 have both been true while every run crashed.

## the worker (heavypad)

flow execution runs on the home box as a systemd process worker for
`home-pool`, polling the server outbound over Tailscale. no ingress, no
port-forward. the installer and unit are in [deploy/home-worker/](../deploy/home-worker/);
[deploy/hub-data-sync/](../deploy/hub-data-sync/) rsyncs `hub.duckdb` and
`llm-spend.jsonl` to the VM every few minutes so the hub serves fresh data
without a round trip home.

`kubernetes-pool` survives only as a defined fallback. its base job template
is applied by `just storage`; no k8s worker runs in normal operation.

```sh
just heavypad-status   # installed toolchain, worker unit, disk, codex login expiry
```

## flows

`prefect.yaml` owns schedules, triggers, tags, parameters, and per-deployment
job variables. every push to `main` registers all of them through
`.tangled/workflows/deploy.yml`, pinned to the pushed commit. the inventory in
[deployments.md](deployments.md) is generated from the same file and CI fails
if it is stale.

```sh
just inventory                                   # regenerate docs/deployments.md
just prefect deploy --all                        # register by hand (CI does this on push)
just prefect deployment run 'diagnostics/diagnostics' --watch   # exercise the real worker
just automations                                 # apply deploy/automations.yaml (idempotent)
just work-pool                                   # apply deploy/work-pools templates
```

standalone automations (send-notification, cross-deployment triggers) live in
`deploy/automations.yaml` because `prefect.yaml` can only express
run-deployment triggers bound to a deployment.

## day to day

```sh
just health            # /api/health
just status            # node + pod resource usage
just logs              # tail prefect-server; `just logs <component>` for others
just prefect flow-run ls
just ssh               # the VM
just dashboards        # reload grafana from deploy/dashboards/
```

## the hub

```sh
just publish-web-remote   # build the sveltekit image on the node, import into k3s, roll the pod
```

`just web` is the older local docker build-and-push path. the hub itself is
described in [hub.md](hub.md).

## analytics

```sh
just init-analytics    # first time: dbt deps, seed, compile
```

`analytics.duckdb` is single-writer. every read-write open holds the
`analytics-duckdb-writer` global concurrency limit through
`mps.lock.analytics_write_slot`; readers snapshot the file instead of locking.
